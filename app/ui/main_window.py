"""
MainWindow — Fenêtre principale PySide6 (Phase 1 MVP).

Interface minimale comprenant :
- Bouton "Lancer l'analyse" (SPANX uniquement en Phase 1)
- Zone de logs en temps réel
- Bouton "Exporter CSV"
- Barre de statut avec progression

Pattern MVP : la fenêtre est passive, la logique est dans les signaux/slots.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QAction, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.config import settings
from app.core.events import event_bus
from app.core.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Worker thread pour le crawl (évite de bloquer l'UI)
# ---------------------------------------------------------------------------

class CrawlWorker(QObject):
    """Exécute le crawl dans un thread séparé."""

    finished = Signal(list)   # list[RunResult]
    error = Signal(str)
    log_message = Signal(str, str)   # (level, message)
    progress = Signal(int, int)      # (current, total)

    def __init__(self, brand_slugs: list[str]) -> None:
        super().__init__()
        self._brand_slugs = brand_slugs
        self._runner = None

    def run(self) -> None:
        try:
            from app.workflow.runner import WorkflowRunner
            self._runner = WorkflowRunner()
            results = self._runner.run(brand_slugs=self._brand_slugs)
            self.finished.emit(results)
        except Exception as exc:
            log.error("Erreur workflow", error=str(exc))
            self.error.emit(str(exc))

    def cancel(self) -> None:
        if self._runner:
            self._runner.cancel()


# ---------------------------------------------------------------------------
# Fenêtre principale
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Fenêtre principale de l'application Market Intelligence."""

    # Signal interne pour mettre à jour les logs depuis n'importe quel thread
    _log_received = Signal(str, str)
    _progress_received = Signal(str, int, int)

    def __init__(self) -> None:
        super().__init__()
        self._worker: CrawlWorker | None = None
        self._thread: QThread | None = None
        self._is_running = False

        self._setup_ui()
        self._setup_event_bus()
        self._log_received.connect(self._append_log)
        self._progress_received.connect(self._update_progress)

        log.info("Interface démarrée", version=settings.APP_VERSION)

    # -------------------------------------------------------------------
    # Construction de l'interface
    # -------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle(settings.APP_NAME)
        self.setMinimumSize(900, 620)

        # Widget central
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # --- En-tête ---
        header = QLabel("Market Intelligence Platform — Shapewear US")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        header.setFont(font)
        header.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(header)

        subtitle = QLabel("Plateforme de veille concurrentielle shapewear")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setStyleSheet("color: #666;")
        main_layout.addWidget(subtitle)

        # --- Barre de boutons ---
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self._btn_run = QPushButton("▶  Lancer l'analyse SPANX")
        self._btn_run.setMinimumHeight(38)
        self._btn_run.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; "
            "border-radius: 6px; font-weight: bold; padding: 0 16px; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
            "QPushButton:disabled { background-color: #94a3b8; }"
        )
        self._btn_run.clicked.connect(self._on_run_clicked)
        btn_layout.addWidget(self._btn_run)

        self._btn_cancel = QPushButton("⏹  Arrêter")
        self._btn_cancel.setMinimumHeight(38)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setStyleSheet(
            "QPushButton { background-color: #dc2626; color: white; "
            "border-radius: 6px; font-weight: bold; padding: 0 16px; }"
            "QPushButton:hover { background-color: #b91c1c; }"
            "QPushButton:disabled { background-color: #94a3b8; }"
        )
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)
        btn_layout.addWidget(self._btn_cancel)

        self._btn_export = QPushButton("⬇  Exporter CSV")
        self._btn_export.setMinimumHeight(38)
        self._btn_export.setStyleSheet(
            "QPushButton { background-color: #16a34a; color: white; "
            "border-radius: 6px; font-weight: bold; padding: 0 16px; }"
            "QPushButton:hover { background-color: #15803d; }"
            "QPushButton:disabled { background-color: #94a3b8; }"
        )
        self._btn_export.clicked.connect(self._on_export_clicked)
        btn_layout.addWidget(self._btn_export)

        self._btn_clear = QPushButton("🗑  Effacer logs")
        self._btn_clear.setMinimumHeight(38)
        self._btn_clear.setStyleSheet(
            "QPushButton { border-radius: 6px; padding: 0 12px; }"
            "QPushButton:hover { background-color: #e2e8f0; }"
        )
        self._btn_clear.clicked.connect(self._on_clear_logs)
        btn_layout.addWidget(self._btn_clear)

        btn_layout.addStretch()
        main_layout.addLayout(btn_layout)

        # --- Barre de progression ---
        progress_layout = QHBoxLayout()
        self._progress_label = QLabel("Prêt")
        self._progress_label.setMinimumWidth(220)
        progress_layout.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setMinimumHeight(20)
        progress_layout.addWidget(self._progress_bar)

        main_layout.addLayout(progress_layout)

        # --- Zone de logs ---
        logs_label = QLabel("Logs en temps réel :")
        logs_label.setStyleSheet("font-weight: bold;")
        main_layout.addWidget(logs_label)

        self._log_view = QTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setFont(QFont("Courier New", 9))
        self._log_view.setStyleSheet(
            "background-color: #0f172a; color: #e2e8f0; "
            "border-radius: 6px; padding: 8px;"
        )
        self._log_view.setMinimumHeight(320)
        main_layout.addWidget(self._log_view)

        # --- Barre de statut ---
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage("Prêt — Base de données : " + settings.DATABASE_URL)

        # Style global
        self.setStyleSheet(
            "QMainWindow { background-color: #f8fafc; }"
            "QWidget { font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; }"
            "QTextEdit { border: 1px solid #cbd5e1; }"
            "QProgressBar { border: 1px solid #cbd5e1; border-radius: 4px; }"
            "QProgressBar::chunk { background-color: #2563eb; border-radius: 4px; }"
        )

    def _setup_event_bus(self) -> None:
        """Abonne la fenêtre aux événements du bus."""
        event_bus.start()
        event_bus.subscribe("log.message", self._on_log_event)
        event_bus.subscribe("crawl.task.progress", self._on_progress_event)
        event_bus.subscribe("product.saved", self._on_product_saved)
        event_bus.subscribe("crawl.session.completed", self._on_session_completed)

    # -------------------------------------------------------------------
    # Slots / handlers de boutons
    # -------------------------------------------------------------------

    def _on_run_clicked(self) -> None:
        if self._is_running:
            return

        self._is_running = True
        self._btn_run.setEnabled(False)
        self._btn_cancel.setEnabled(True)
        self._btn_export.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.setRange(0, 0)   # Mode indéterminé
        self._progress_label.setText("Initialisation…")
        self._status_bar.showMessage("Session en cours…")
        self._append_log("INFO", "=== Démarrage de la session d'analyse ===")
        self._append_log("INFO", f"Heure de départ : {datetime.now().strftime('%H:%M:%S')}")

        # Lancer dans un QThread
        self._worker = CrawlWorker(brand_slugs=["spanx"])
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_crawl_finished)
        self._worker.error.connect(self._on_crawl_error)
        self._thread.start()

    def _on_cancel_clicked(self) -> None:
        if self._worker:
            self._worker.cancel()
        self._append_log("WARNING", "Annulation demandée — arrêt après la tâche en cours…")
        self._btn_cancel.setEnabled(False)

    def _on_export_clicked(self) -> None:
        try:
            from app.exports.csv_exporter import CsvExporter
            exporter = CsvExporter()
            path = exporter.export_from_db()
            self._append_log("INFO", f"Export CSV créé : {path}")
            QMessageBox.information(
                self,
                "Export réussi",
                f"Fichier CSV créé :\n{path}",
            )
        except Exception as exc:
            self._append_log("ERROR", f"Erreur export : {exc}")
            QMessageBox.critical(self, "Erreur export", str(exc))

    def _on_clear_logs(self) -> None:
        self._log_view.clear()

    def _on_crawl_finished(self, results: list) -> None:
        self._is_running = False
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._btn_export.setEnabled(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._progress_label.setText("Terminé")

        summary_parts = []
        for result in results:
            summary_parts.append(
                f"{result.brand_slug}: {result.products_found} produits "
                f"({result.products_new} nouveaux, "
                f"{result.products_changed} changés)"
            )
        summary = " | ".join(summary_parts)
        self._status_bar.showMessage(f"Session terminée — {summary}")
        self._append_log("INFO", f"=== Session terminée : {summary} ===")

        if self._thread:
            self._thread.quit()
            self._thread.wait()

    def _on_crawl_error(self, error_msg: str) -> None:
        self._is_running = False
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_label.setText("Erreur")
        self._status_bar.showMessage(f"Erreur : {error_msg}")
        self._append_log("ERROR", f"Erreur fatale : {error_msg}")
        QMessageBox.critical(self, "Erreur", f"La session a échoué :\n{error_msg}")

        if self._thread:
            self._thread.quit()
            self._thread.wait()

    # -------------------------------------------------------------------
    # Handlers d'événements du bus (appelés depuis n'importe quel thread)
    # -------------------------------------------------------------------

    def _on_log_event(self, level: str = "INFO", message: str = "", **kwargs) -> None:
        self._log_received.emit(level, message)

    def _on_progress_event(
        self,
        brand: str = "",
        category: str = "",
        current: int = 0,
        total: int = 0,
        **kwargs,
    ) -> None:
        self._progress_received.emit(f"{brand}/{category}", current, total)

    def _on_product_saved(self, brand: str = "", name: str = "", is_new: bool = False, **kwargs) -> None:
        status = "NOUVEAU" if is_new else "MÀJ"
        self._log_received.emit("INFO", f"[{status}] {brand} — {name}")

    def _on_session_completed(self, **kwargs) -> None:
        pass  # Géré par _on_crawl_finished via le signal

    # -------------------------------------------------------------------
    # Mise à jour de l'UI (dans le thread principal)
    # -------------------------------------------------------------------

    def _append_log(self, level: str, message: str) -> None:
        """Ajoute une ligne colorée dans la zone de logs."""
        timestamp = datetime.now().strftime("%H:%M:%S")
        color_map = {
            "DEBUG":    "#64748b",
            "INFO":     "#e2e8f0",
            "WARNING":  "#fbbf24",
            "ERROR":    "#f87171",
            "CRITICAL": "#ef4444",
        }
        color = color_map.get(level.upper(), "#e2e8f0")
        html = (
            f'<span style="color:#64748b">[{timestamp}]</span> '
            f'<span style="color:{color}; font-weight: bold">[{level}]</span> '
            f'<span style="color:{color}">{message}</span>'
        )
        self._log_view.append(html)
        # Auto-scroll vers le bas
        cursor = self._log_view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self._log_view.setTextCursor(cursor)

    def _update_progress(self, label: str, current: int, total: int) -> None:
        """Met à jour la barre de progression."""
        if total > 0:
            pct = int((current / total) * 100)
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(pct)
            self._progress_label.setText(f"{label} : {current}/{total}")
        else:
            self._progress_bar.setRange(0, 0)

    # -------------------------------------------------------------------
    # Fermeture
    # -------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._is_running:
            reply = QMessageBox.question(
                self,
                "Session en cours",
                "Une session est en cours. Arrêter et quitter ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
            if self._worker:
                self._worker.cancel()

        event_bus.stop()
        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

        super().closeEvent(event)
"""
MainWindow Phase 2 — Fenêtre principale PySide6 avec navigation complète.

Navigation latérale à 5 sections :
  - Dashboard   : KPIs, résumé dernière session, bouton lancer
  - Marques     : gestion des connecteurs de scraping
  - Résultats   : tableau des produits avec filtres avancés
  - Historique  : sessions passées et rapports
  - Paramètres  : configuration de l'application

Pattern MVP : la fenêtre orchestre les vues, la logique métier est dans les workers.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QAction, QFont, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
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

    finished           = Signal(list)    # list[RunResult]
    error              = Signal(str)
    log_message        = Signal(str, str)  # (level, message)
    _progress_received = Signal(str, int, int)  # (label, current, total)

    def __init__(self, brand_slugs: list[str]) -> None:
        super().__init__()
        self._brand_slugs = brand_slugs
        self._runner      = None

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
# Bouton de navigation latéral
# ---------------------------------------------------------------------------

class NavButton(QPushButton):
    """Bouton de navigation latérale avec état actif."""

    def __init__(self, icon: str, label: str, parent=None) -> None:
        super().__init__(f"  {icon}  {label}", parent)
        self.setCheckable(True)
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self._update_style(False)

    def setChecked(self, checked: bool) -> None:
        super().setChecked(checked)
        self._update_style(checked)

    def _update_style(self, active: bool) -> None:
        if active:
            self.setStyleSheet(
                "QPushButton { background: #2563EB; color: white; border: none; "
                "border-radius: 8px; font-size: 10pt; font-weight: bold; "
                "text-align: left; padding: 0 12px; }"
            )
        else:
            self.setStyleSheet(
                "QPushButton { background: transparent; color: #94A3B8; border: none; "
                "border-radius: 8px; font-size: 10pt; text-align: left; padding: 0 12px; }"
                "QPushButton:hover { background: #1E293B; color: #E2E8F0; }"
            )


# ---------------------------------------------------------------------------
# Fenêtre principale Phase 2
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Fenêtre principale de l'application Market Intelligence — Phase 2."""

    # Signaux internes pour thread-safety
    _log_received      = Signal(str, str)
    _progress_received = Signal(str, int, int)  # (label, current, total)

    def __init__(self) -> None:
        super().__init__()
        self._worker: CrawlWorker | None = None
        self._thread: QThread | None     = None
        self._is_running = False

        # Scheduler (planification automatique)
        self._scheduler = None
        self._init_scheduler()

        self._setup_ui()
        self._setup_event_bus()

        # Connecter les signaux internes
        self._log_received.connect(self._append_log)
        self._progress_received.connect(self._update_progress)

        # Rafraîchir le dashboard au démarrage
        QTimer.singleShot(500, self._refresh_current_view)

        log.info("Interface Phase 2 démarrée", version=settings.APP_VERSION)

    # -------------------------------------------------------------------
    # Scheduler
    # -------------------------------------------------------------------

    def _init_scheduler(self) -> None:
        """Initialise le planificateur avec le callback de lancement."""
        try:
            from app.workflow.scheduler import Scheduler

            def _scheduled_run(brands: list[str] | None) -> None:
                if not self._is_running:
                    slugs = brands or ["spanx", "skims", "honeylove", "shapermint", "wacoal"]
                    self._start_crawl(brand_slugs=slugs)

            self._scheduler = Scheduler(run_callback=_scheduled_run)
            self._scheduler.start()
        except Exception as exc:
            log.warning("Impossible d'initialiser le planificateur", error=str(exc))

    # -------------------------------------------------------------------
    # Construction de l'interface
    # -------------------------------------------------------------------

    def _setup_ui(self) -> None:
        self.setWindowTitle(settings.APP_NAME)
        self.setMinimumSize(1200, 700)

        # Widget racine
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # ── Sidebar ────────────────────────────────────────────────────────
        sidebar = self._build_sidebar()
        root_layout.addWidget(sidebar)

        # ── Zone de contenu ────────────────────────────────────────────────
        content_area = QWidget()
        content_area.setStyleSheet("background: #F1F5F9;")
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        # Barre supérieure
        topbar = self._build_topbar()
        content_layout.addWidget(topbar)

        # Stack de vues
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: #F1F5F9; }")
        content_layout.addWidget(self._stack)

        # Barre de progression globale (visible seulement pendant un crawl)
        progress_widget = self._build_progress_bar()
        content_layout.addWidget(progress_widget)

        root_layout.addWidget(content_area, 1)

        # ── Barre de statut ────────────────────────────────────────────────
        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "QStatusBar { background: #0F172A; color: #64748B; font-size: 8pt; }"
        )
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage(
            f"Prêt  |  BD : {settings.DATABASE_URL.replace('sqlite:///', '')}"
        )

        # ── Charger les vues ───────────────────────────────────────────────
        self._load_views()

        # Style global
        self.setStyleSheet(
            "QMainWindow { background: #0F172A; }"
            "QWidget { font-family: 'Segoe UI', Arial, sans-serif; }"
        )

    def _build_sidebar(self) -> QWidget:
        """Construit la barre de navigation latérale."""
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background: #0F172A;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 16)
        sidebar_layout.setSpacing(4)

        # Logo / titre
        logo_label = QLabel("Market\nIntelligence")
        logo_font = QFont()
        logo_font.setPointSize(13)
        logo_font.setBold(True)
        logo_label.setFont(logo_font)
        logo_label.setStyleSheet("color: #E2E8F0; padding: 8px 4px 16px 4px;")
        logo_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(logo_label)

        subtitle = QLabel("Shapewear US")
        subtitle.setStyleSheet("color: #475569; font-size: 8pt; padding-bottom: 12px;")
        subtitle.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(subtitle)

        # Séparateur
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #1E293B;")
        sidebar_layout.addWidget(sep)
        sidebar_layout.addSpacing(8)

        # Boutons de navigation
        self._nav_buttons: list[NavButton] = []
        nav_items = [
            ("🏠", "Dashboard"),
            ("🔗", "Marques"),
            ("📊", "Résultats"),
            ("📋", "Historique"),
            ("⚙️", "Paramètres"),
        ]
        for icon, label in nav_items:
            btn = NavButton(icon, label)
            btn.clicked.connect(lambda checked, lbl=label: self._navigate_to(lbl))
            sidebar_layout.addWidget(btn)
            self._nav_buttons.append(btn)

        sidebar_layout.addStretch()

        # Statut de la session en cours (visible uniquement pendant un crawl)
        self._session_status_label = QLabel("")
        self._session_status_label.setStyleSheet(
            "color: #2563EB; font-size: 8pt; padding: 4px;"
        )
        self._session_status_label.setWordWrap(True)
        self._session_status_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(self._session_status_label)

        # Version
        version_label = QLabel(f"v{settings.APP_VERSION}")
        version_label.setStyleSheet("color: #334155; font-size: 8pt;")
        version_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(version_label)

        return sidebar

    def _build_topbar(self) -> QWidget:
        """Construit la barre supérieure avec boutons d'action rapide."""
        topbar = QWidget()
        topbar.setFixedHeight(52)
        topbar.setStyleSheet(
            "background: white; border-bottom: 1px solid #E2E8F0;"
        )
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        # Titre de la vue courante
        self._view_title = QLabel("Dashboard")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        self._view_title.setFont(title_font)
        self._view_title.setStyleSheet("color: #1E293B;")
        layout.addWidget(self._view_title)
        layout.addStretch()

        # Bouton "Lancer l'analyse"
        self._btn_run = QPushButton("▶  Lancer l'analyse")
        self._btn_run.setMinimumHeight(34)
        self._btn_run.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 0 16px; font-size: 9pt; }"
            "QPushButton:hover { background: #1D4ED8; }"
            "QPushButton:disabled { background: #94A3B8; }"
        )
        self._btn_run.clicked.connect(self._on_run_clicked)
        layout.addWidget(self._btn_run)

        # Bouton "Arrêter"
        self._btn_cancel = QPushButton("⏹  Arrêter")
        self._btn_cancel.setMinimumHeight(34)
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setStyleSheet(
            "QPushButton { background: #DC2626; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 0 12px; font-size: 9pt; }"
            "QPushButton:hover { background: #B91C1C; }"
            "QPushButton:disabled { background: #94A3B8; }"
        )
        self._btn_cancel.clicked.connect(self._on_cancel_clicked)
        layout.addWidget(self._btn_cancel)

        # Bouton export rapide CSV
        self._btn_export = QPushButton("⬇  CSV")
        self._btn_export.setMinimumHeight(34)
        self._btn_export.setStyleSheet(
            "QPushButton { background: #16A34A; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 0 12px; font-size: 9pt; }"
            "QPushButton:hover { background: #15803D; }"
        )
        self._btn_export.clicked.connect(self._on_export_csv_clicked)
        layout.addWidget(self._btn_export)

        return topbar

    def _build_progress_bar(self) -> QWidget:
        """Construit la barre de progression (masquée par défaut)."""
        widget = QWidget()
        widget.setFixedHeight(0)  # Masquée par défaut
        widget.setStyleSheet("background: white; border-top: 1px solid #E2E8F0;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(16, 4, 16, 4)

        self._progress_label = QLabel("Prêt")
        self._progress_label.setStyleSheet("color: #475569; font-size: 8pt; min-width: 240px;")
        layout.addWidget(self._progress_label)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(14)
        self._progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #CBD5E1; border-radius: 7px; background: #F1F5F9; }"
            "QProgressBar::chunk { background: #2563EB; border-radius: 7px; }"
        )
        layout.addWidget(self._progress_bar, 1)

        self._progress_widget = widget
        return widget

    def _load_views(self) -> None:
        """Instancie et empile toutes les vues."""
        # Lazy import pour éviter les imports circulaires
        from app.ui.views.dashboard import DashboardView
        from app.ui.views.brands    import BrandsView
        from app.ui.views.results   import ResultsView
        from app.ui.views.history   import HistoryView
        from app.ui.views.settings  import SettingsView

        self._dashboard_view = DashboardView()
        self._brands_view    = BrandsView()
        self._results_view   = ResultsView()
        self._history_view   = HistoryView()
        self._settings_view  = SettingsView()

        # Connecter les signaux du dashboard
        self._dashboard_view.run_requested.connect(self._on_run_clicked)
        self._dashboard_view.export_csv_requested.connect(self._on_export_csv_clicked)
        self._dashboard_view.export_excel_requested.connect(self._on_export_excel_clicked)

        for view in [
            self._dashboard_view,
            self._brands_view,
            self._results_view,
            self._history_view,
            self._settings_view,
        ]:
            self._stack.addWidget(view)

        # Activer la première vue (Dashboard)
        self._navigate_to("Dashboard")

    # -------------------------------------------------------------------
    # Navigation
    # -------------------------------------------------------------------

    _VIEW_MAP = {
        "Dashboard":  0,
        "Marques":    1,
        "Résultats":  2,
        "Historique": 3,
        "Paramètres": 4,
    }

    def _navigate_to(self, view_name: str) -> None:
        """Active la vue demandée et met à jour la navigation."""
        idx = self._VIEW_MAP.get(view_name, 0)
        self._stack.setCurrentIndex(idx)
        self._view_title.setText(view_name)

        # Mettre à jour les boutons de nav
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == idx)

        # Rafraîchir la vue si elle a une méthode refresh()
        self._refresh_current_view()

    def _refresh_current_view(self) -> None:
        """Rafraîchit la vue active si elle le supporte."""
        view = self._stack.currentWidget()
        if view and hasattr(view, "refresh"):
            try:
                view.refresh()
            except Exception as exc:
                log.warning("Erreur rafraîchissement vue", error=str(exc))

    # -------------------------------------------------------------------
    # Bus d'événements
    # -------------------------------------------------------------------

    def _setup_event_bus(self) -> None:
        """Abonne la fenêtre aux événements du bus."""
        event_bus.start()
        # NOTE : on ne s'abonne PAS à "log.message" ici.
        # Les logs passent par le logger Python → fichier + console.
        # L'ancien main_window (Phase 1) avait une zone de logs inline ;
        # la Phase 2 n'en a pas : les logs sont visibles dans le terminal
        # ou le fichier de log rotatif. S'abonner ici créerait une boucle :
        #   log.info() → bus "log.message" → _on_log_event → _append_log → log.info() → ...
        event_bus.subscribe("crawl.task.progress",     self._on_progress_event)
        event_bus.subscribe("product.saved",            self._on_product_saved)
        event_bus.subscribe("crawl.session.completed",  self._on_session_completed)
        event_bus.subscribe("crawl.session.started",    self._on_session_started)

    # -------------------------------------------------------------------
    # Handlers de boutons
    # -------------------------------------------------------------------

    def _on_run_clicked(self) -> None:
        if self._is_running:
            return
        try:
            from app.connectors.registry import ConnectorRegistry
            brand_slugs = ConnectorRegistry().list_connectors()
        except Exception:
            brand_slugs = ["spanx", "skims", "honeylove", "shapermint", "wacoal"]
        self._start_crawl(brand_slugs=brand_slugs)

    def _start_crawl(self, brand_slugs: list[str]) -> None:
        """Lance un crawl avec la liste de marques donnée."""
        if self._is_running:
            return

        self._is_running = True
        self._btn_run.setEnabled(False)
        self._btn_cancel.setEnabled(True)

        # Afficher la barre de progression
        self._progress_widget.setFixedHeight(30)
        self._progress_bar.setRange(0, 0)   # Mode indéterminé
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"Initialisation — {', '.join(brand_slugs)}")
        self._session_status_label.setText("⏳ Session en cours…")
        self._status_bar.showMessage(f"Session en cours : {', '.join(brand_slugs)}")

        # Mettre à jour le dashboard
        if hasattr(self, "_dashboard_view"):
            self._dashboard_view.set_running(True)

        self._append_log("INFO", f"=== Démarrage session — {', '.join(brand_slugs)} ===")
        self._append_log("INFO", f"Heure : {datetime.now().strftime('%H:%M:%S')}")

        # Lancer dans un QThread
        self._worker = CrawlWorker(brand_slugs=brand_slugs)
        self._thread = QThread()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_crawl_finished)
        self._worker.error.connect(self._on_crawl_error)
        self._thread.start()

    def _on_cancel_clicked(self) -> None:
        if self._worker:
            self._worker.cancel()
        self._append_log("WARNING", "Annulation demandée…")
        self._btn_cancel.setEnabled(False)

    def _on_export_csv_clicked(self) -> None:
        try:
            from app.exports.csv_exporter import CsvExporter
            exporter = CsvExporter()
            path = exporter.export_from_db()
            self._append_log("INFO", f"CSV créé : {path}")
            QMessageBox.information(self, "Export réussi", f"CSV créé :\n{path}")
        except Exception as exc:
            self._append_log("ERROR", f"Erreur export CSV : {exc}")
            QMessageBox.critical(self, "Erreur export", str(exc))

    def _on_export_excel_clicked(self) -> None:
        try:
            from app.exports.excel_exporter import ExcelExporter
            exporter = ExcelExporter()
            path = exporter.export_from_db()
            self._append_log("INFO", f"Excel créé : {path}")
            QMessageBox.information(self, "Export réussi", f"Excel créé :\n{path}")
        except Exception as exc:
            self._append_log("ERROR", f"Erreur export Excel : {exc}")
            QMessageBox.critical(self, "Erreur export", str(exc))

    # -------------------------------------------------------------------
    # Handlers de fin de session
    # -------------------------------------------------------------------

    def _on_crawl_finished(self, results: list) -> None:
        self._is_running = False
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(100)
        self._session_status_label.setText("")

        parts = []
        for r in results:
            parts.append(
                f"{r.brand_slug}: {r.products_found} produits "
                f"({r.products_new} nouveaux, {r.products_changed} changés)"
            )
        summary = " | ".join(parts)
        self._status_bar.showMessage(f"Session terminée — {summary}")
        self._append_log("INFO", f"=== Session terminée : {summary} ===")
        self._progress_label.setText("Session terminée")

        # Masquer la barre de progression après 3s
        QTimer.singleShot(3000, lambda: self._progress_widget.setFixedHeight(0))

        if hasattr(self, "_dashboard_view"):
            self._dashboard_view.set_running(False)
            self._dashboard_view.refresh()

        if self._thread:
            self._thread.quit()
            self._thread.wait()

        # Proposer un rapport
        self._offer_report(results)

    def _on_crawl_error(self, error_msg: str) -> None:
        self._is_running = False
        self._btn_run.setEnabled(True)
        self._btn_cancel.setEnabled(False)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._session_status_label.setText("❌ Erreur")
        self._status_bar.showMessage(f"Erreur : {error_msg}")
        self._append_log("ERROR", f"Erreur fatale : {error_msg}")
        QMessageBox.critical(self, "Erreur", f"La session a échoué :\n{error_msg}")
        QTimer.singleShot(3000, lambda: self._progress_widget.setFixedHeight(0))

        if hasattr(self, "_dashboard_view"):
            self._dashboard_view.set_running(False)

        if self._thread:
            self._thread.quit()
            self._thread.wait()

    def _offer_report(self, results: list) -> None:
        """Propose de générer un rapport après la session."""
        total = sum(r.products_found for r in results)
        if total == 0:
            return
        reply = QMessageBox.question(
            self,
            "Session terminée",
            f"{total} produits analysés.\n\nGénérer un rapport Excel ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._on_export_excel_clicked()

    # -------------------------------------------------------------------
    # Handlers d'événements du bus
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
        label = f"{brand}/{category}" if category else brand
        self._progress_received.emit(label, current, total)

    def _on_product_saved(
        self, brand: str = "", name: str = "", is_new: bool = False, **kwargs
    ) -> None:
        status = "NOUVEAU" if is_new else "MÀJ"
        self._log_received.emit("INFO", f"[{status}] {name}")

    def _on_session_started(self, **kwargs) -> None:
        pass

    def _on_session_completed(self, **kwargs) -> None:
        # Rafraîchir le dashboard et l'historique
        if hasattr(self, "_history_view"):
            # Rafraîchissement différé (la DB est peut-être encore en cours d'écriture)
            QTimer.singleShot(500, self._history_view.refresh)

    # -------------------------------------------------------------------
    # Mise à jour UI (thread principal uniquement)
    # -------------------------------------------------------------------

    def _append_log(self, level: str, message: str) -> None:
        """
        Reçoit un message de log à afficher dans l'UI.

        IMPORTANT : ne jamais appeler log.info/warning/error ici.
        Le logger écrit sur le bus d'événements, qui rappelle _on_log_event,
        qui émet _log_received, qui rappelle _append_log → boucle infinie.
        Cette méthode est le point terminal de la chaîne : elle ne fait rien
        de plus (les vues abonnées au bus affichent les logs elles-mêmes).
        """
        # Point terminal — pas de re-log ici.

    def _update_progress(self, label: str, current: int, total: int) -> None:
        """Met à jour la barre de progression."""
        if total > 0:
            pct = min(100, int(current / total * 100))
            self._progress_bar.setRange(0, 100)
            self._progress_bar.setValue(pct)
            self._progress_label.setText(f"{label} : {current}/{total}")
            self._session_status_label.setText(f"⏳ {current}/{total}")
        else:
            self._progress_bar.setRange(0, 0)

    # -------------------------------------------------------------------
    # Fermeture propre
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

        # Arrêter le planificateur
        if self._scheduler:
            try:
                self._scheduler.stop()
            except Exception:
                pass

        event_bus.stop()

        if self._thread and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

        super().closeEvent(event)
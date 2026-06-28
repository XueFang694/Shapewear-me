"""
Vue Paramètres — Configuration complète de l'application.

Paramètres Phase 2 :
  - Délai entre requêtes (slider + affichage valeur)
  - Nombre de workers de scraping
  - Répertoire d'export (sélecteur de dossier)
  - Rotation User-Agent (toggle)
  - Base de données (chemin, test connexion, sauvegarde, restauration)
  - Proxy HTTP (champ texte, test)
  - Niveau de log
  - Purge des données (snapshots > N jours)
  - Bouton "Sauvegarder" / "Réinitialiser"
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.config import settings, PROJECT_ROOT
from app.core.logger import get_logger

log = get_logger(__name__)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        f = QFont(); f.setBold(True); f.setPointSize(10)
        self.setFont(f)
        self.setStyleSheet("color: #1E293B; padding-top: 6px;")


class SettingRow(QWidget):
    """Ligne paramètre : label + widget + description optionnelle."""
    def __init__(self, label: str, widget: QWidget, desc: str = "", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        lbl = QLabel(label)
        lbl.setMinimumWidth(200)
        lbl.setStyleSheet("color: #475569; font-size: 9pt;")
        layout.addWidget(lbl)
        layout.addWidget(widget)
        if desc:
            d = QLabel(desc)
            d.setStyleSheet("color: #94A3B8; font-size: 8pt;")
            layout.addWidget(d)
        layout.addStretch()


class SettingsView(QScrollArea):
    """Vue Paramètres avec sauvegarde dans settings.json."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._unsaved = False

        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(24, 16, 24, 24)
        main_layout.setSpacing(14)
        self.setWidget(container)

        # Titre
        title = QLabel("Paramètres")
        tf = QFont(); tf.setPointSize(14); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: #1E293B;")
        main_layout.addWidget(title)

        subtitle = QLabel("Configuration de l'application Market Intelligence Platform")
        subtitle.setStyleSheet("color: #64748B; font-size: 10pt;")
        main_layout.addWidget(subtitle)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #E2E8F0;"); main_layout.addWidget(sep)

        # ── SCRAPING ──────────────────────────────────────────────────────
        main_layout.addWidget(SectionTitle("🕷️  Scraping"))
        scraping_grp = self._make_group()
        grp_l = scraping_grp.layout()

        # Workers
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 6)
        self._workers_spin.setValue(getattr(settings, "MAX_WORKERS", 2))
        self._workers_spin.setFixedWidth(80)
        self._workers_spin.valueChanged.connect(self._mark_unsaved)
        grp_l.addWidget(SettingRow("Threads de scraping",
            self._workers_spin, "1 = séquentiel, 2-4 = parallèle (recommandé)"))

        # Délai min
        self._delay_min = QDoubleSpinBox()
        self._delay_min.setRange(0.5, 10.0)
        self._delay_min.setSingleStep(0.5)
        self._delay_min.setSuffix(" s")
        self._delay_min.setValue(1.5)
        self._delay_min.setFixedWidth(90)
        self._delay_min.valueChanged.connect(self._mark_unsaved)
        grp_l.addWidget(SettingRow("Délai minimum entre requêtes", self._delay_min))

        # Délai max
        self._delay_max = QDoubleSpinBox()
        self._delay_max.setRange(1.0, 20.0)
        self._delay_max.setSingleStep(0.5)
        self._delay_max.setSuffix(" s")
        self._delay_max.setValue(4.0)
        self._delay_max.setFixedWidth(90)
        self._delay_max.valueChanged.connect(self._mark_unsaved)
        grp_l.addWidget(SettingRow("Délai maximum entre requêtes", self._delay_max))

        # Rotation UA
        self._rotate_ua = QCheckBox("Activer la rotation des User-Agents")
        self._rotate_ua.setChecked(True)
        self._rotate_ua.stateChanged.connect(self._mark_unsaved)
        grp_l.addWidget(self._rotate_ua)

        # Proxy
        self._proxy_input = QLineEdit()
        self._proxy_input.setPlaceholderText("http://user:pass@proxy:port  (laisser vide si aucun)")
        self._proxy_input.setText(getattr(settings, "PROXY_URL", ""))
        self._proxy_input.textChanged.connect(self._mark_unsaved)
        self._proxy_input.setStyleSheet(
            "QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 8px; }"
        )
        proxy_row = QHBoxLayout()
        proxy_row.addWidget(QLabel("Proxy HTTP/SOCKS :"))
        proxy_row.addWidget(self._proxy_input, 1)
        btn_test_proxy = QPushButton("Tester")
        btn_test_proxy.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 4px; padding: 3px 8px; }"
        )
        btn_test_proxy.clicked.connect(self._test_proxy)
        proxy_row.addWidget(btn_test_proxy)
        proxy_w = QWidget(); proxy_w.setLayout(proxy_row)
        grp_l.addWidget(proxy_w)

        main_layout.addWidget(scraping_grp)

        # ── BASE DE DONNÉES ───────────────────────────────────────────────
        main_layout.addWidget(SectionTitle("🗄️  Base de données"))
        db_grp = self._make_group()
        db_l = db_grp.layout()

        # Chemin DB (lecture seule — résolu dynamiquement)
        from app.storage.database import get_active_db_url
        active_url = get_active_db_url() or settings.DATABASE_URL
        db_path_lbl = QLabel(active_url.replace("sqlite:///", ""))
        db_path_lbl.setStyleSheet(
            "color: #475569; font-size: 8pt; font-family: monospace; "
            "background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 8px;"
        )
        db_path_lbl.setWordWrap(True)
        db_l.addWidget(QLabel("Chemin actuel :"))
        db_l.addWidget(db_path_lbl)

        # Boutons DB
        db_btn_row = QHBoxLayout()
        btn_test_db = QPushButton("✓ Tester la connexion")
        btn_test_db.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 12px; }"
            "QPushButton:hover { background: #F1F5F9; }"
        )
        btn_test_db.clicked.connect(self._test_db)
        db_btn_row.addWidget(btn_test_db)

        btn_backup = QPushButton("💾 Sauvegarder")
        btn_backup.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; padding: 4px 12px; }"
        )
        btn_backup.clicked.connect(self._backup_db)
        db_btn_row.addWidget(btn_backup)

        btn_restore = QPushButton("↺ Restaurer")
        btn_restore.setStyleSheet(
            "QPushButton { background: #D97706; color: white; border-radius: 6px; padding: 4px 12px; }"
        )
        btn_restore.clicked.connect(self._restore_db)
        db_btn_row.addWidget(btn_restore)
        db_btn_row.addStretch()

        db_btn_w = QWidget(); db_btn_w.setLayout(db_btn_row)
        db_l.addWidget(db_btn_w)

        # Purge des snapshots anciens
        purge_row = QHBoxLayout()
        purge_row.addWidget(QLabel("Purger les snapshots de plus de"))
        self._purge_days = QSpinBox()
        self._purge_days.setRange(30, 365)
        self._purge_days.setValue(180)
        self._purge_days.setSuffix(" jours")
        self._purge_days.setFixedWidth(110)
        purge_row.addWidget(self._purge_days)
        btn_purge = QPushButton("Purger")
        btn_purge.setStyleSheet(
            "QPushButton { background: #DC2626; color: white; border-radius: 6px; padding: 4px 10px; }"
        )
        btn_purge.clicked.connect(self._purge_snapshots)
        purge_row.addWidget(btn_purge)
        purge_row.addStretch()
        purge_w = QWidget(); purge_w.setLayout(purge_row)
        db_l.addWidget(purge_w)

        main_layout.addWidget(db_grp)

        # ── EXPORTS ──────────────────────────────────────────────────────
        main_layout.addWidget(SectionTitle("📂  Exports"))
        export_grp = self._make_group()
        exp_l = export_grp.layout()

        export_dir_row = QHBoxLayout()
        self._export_dir_input = QLineEdit(str(settings.EXPORT_DIR))
        self._export_dir_input.setReadOnly(True)
        self._export_dir_input.setStyleSheet(
            "QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; "
            "padding: 4px 8px; background: #F8FAFC; }"
        )
        export_dir_row.addWidget(QLabel("Répertoire :"))
        export_dir_row.addWidget(self._export_dir_input, 1)
        btn_browse = QPushButton("Parcourir…")
        btn_browse.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 4px; padding: 3px 8px; }"
        )
        btn_browse.clicked.connect(self._browse_export_dir)
        export_dir_row.addWidget(btn_browse)
        exp_dir_w = QWidget(); exp_dir_w.setLayout(export_dir_row)
        exp_l.addWidget(exp_dir_w)

        btn_open_dir = QPushButton("📁 Ouvrir le dossier d'exports")
        btn_open_dir.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 12px; }"
        )
        btn_open_dir.clicked.connect(self._open_export_dir)
        exp_l.addWidget(btn_open_dir)
        main_layout.addWidget(export_grp)

        # ── LOGS ─────────────────────────────────────────────────────────
        main_layout.addWidget(SectionTitle("📋  Logs"))
        log_grp = self._make_group()
        log_l = log_grp.layout()

        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level.setCurrentText(getattr(settings, "LOG_LEVEL", "INFO"))
        self._log_level.setFixedWidth(120)
        self._log_level.currentTextChanged.connect(self._mark_unsaved)
        log_l.addWidget(SettingRow("Niveau de log", self._log_level))

        log_dir_lbl = QLabel(str(settings.LOG_DIR))
        log_dir_lbl.setStyleSheet("color: #64748B; font-size: 8pt; font-family: monospace;")
        log_l.addWidget(SettingRow("Répertoire logs", log_dir_lbl))

        btn_open_logs = QPushButton("📁 Ouvrir le dossier de logs")
        btn_open_logs.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 12px; }"
        )
        btn_open_logs.clicked.connect(self._open_log_dir)
        log_l.addWidget(btn_open_logs)
        main_layout.addWidget(log_grp)

        # ── Boutons save/reset ────────────────────────────────────────────
        action_row = QHBoxLayout()
        self._save_btn = QPushButton("💾 Sauvegarder les paramètres")
        self._save_btn.setMinimumHeight(36)
        self._save_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 0 20px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._save_btn.clicked.connect(self._save_settings)
        action_row.addWidget(self._save_btn)

        btn_reset = QPushButton("↺ Réinitialiser")
        btn_reset.setMinimumHeight(36)
        btn_reset.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 0 16px; }"
        )
        btn_reset.clicked.connect(self._reset_settings)
        action_row.addWidget(btn_reset)
        action_row.addStretch()

        action_w = QWidget(); action_w.setLayout(action_row)
        main_layout.addWidget(action_w)
        main_layout.addStretch()

    # ── Helpers UI ────────────────────────────────────────────────────────

    def _make_group(self) -> QGroupBox:
        grp = QGroupBox()
        grp.setStyleSheet(
            "QGroupBox { border: 1px solid #E2E8F0; border-radius: 8px; "
            "background: white; padding: 8px 12px; }"
        )
        l = QVBoxLayout(grp)
        l.setSpacing(6)
        return grp

    def _mark_unsaved(self) -> None:
        self._unsaved = True

    # ── Actions ──────────────────────────────────────────────────────────

    def _test_db(self) -> None:
        from app.storage.database import check_db_connection
        ok = check_db_connection()
        if ok:
            QMessageBox.information(self, "Connexion DB", "✓ Connexion à la base réussie.")
        else:
            QMessageBox.critical(self, "Connexion DB", "✗ Impossible de se connecter à la base.")

    def _test_proxy(self) -> None:
        proxy = self._proxy_input.text().strip()
        if not proxy:
            QMessageBox.information(self, "Proxy", "Aucun proxy configuré.")
            return
        try:
            import httpx
            with httpx.Client(proxies={"http://": proxy, "https://": proxy}, timeout=10) as c:
                r = c.get("https://httpbin.org/ip")
                QMessageBox.information(
                    self, "Proxy OK", f"✓ Proxy fonctionnel\nIP publique : {r.json().get('origin', '?')}"
                )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur proxy", f"✗ Proxy inaccessible :\n{exc}")

    def _backup_db(self) -> None:
        from app.storage.database import get_active_db_url
        url = get_active_db_url() or ""
        if not url.startswith("sqlite:///"):
            QMessageBox.warning(self, "Sauvegarde", "Sauvegarde uniquement pour SQLite.")
            return
        db_path = Path(url.replace("sqlite:///", ""))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = db_path.parent / f"shapewear_backup_{ts}.db"
        shutil.copy2(db_path, backup_path)
        QMessageBox.information(self, "Sauvegarde OK", f"Base sauvegardée :\n{backup_path}")

    def _restore_db(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, "Choisir une sauvegarde", str(settings.DATA_DIR),
            "SQLite (*.db);;Tous les fichiers (*.*)"
        )
        if not file:
            return
        reply = QMessageBox.question(
            self, "Restauration",
            "Remplacer la base actuelle par cette sauvegarde ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        from app.storage.database import get_active_db_url, dispose_engine
        url = get_active_db_url() or ""
        db_path = Path(url.replace("sqlite:///", ""))
        dispose_engine()
        shutil.copy2(file, db_path)
        QMessageBox.information(self, "Restauration OK", "Base restaurée. Redémarrez l'application.")

    def _purge_snapshots(self) -> None:
        days = self._purge_days.value()
        reply = QMessageBox.question(
            self, "Purge",
            f"Supprimer tous les snapshots de plus de {days} jours ?\n"
            "Cette action est irréversible.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        try:
            from app.storage.database import get_db
            from app.storage.models import ProductSnapshot
            from datetime import timedelta
            cutoff = datetime.utcnow() - timedelta(days=days)
            with get_db() as db:
                deleted = db.query(ProductSnapshot).filter(
                    ProductSnapshot.crawled_at < cutoff
                ).delete()
            QMessageBox.information(
                self, "Purge terminée", f"{deleted} snapshot(s) supprimé(s)."
            )
        except Exception as exc:
            QMessageBox.critical(self, "Erreur purge", str(exc))

    def _browse_export_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Choisir le répertoire d'export", str(settings.EXPORT_DIR)
        )
        if d:
            self._export_dir_input.setText(d)
            self._mark_unsaved()

    def _open_export_dir(self) -> None:
        import subprocess, sys
        path = self._export_dir_input.text()
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _open_log_dir(self) -> None:
        import subprocess, sys
        path = str(settings.LOG_DIR)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def _save_settings(self) -> None:
        data = {
            "MAX_WORKERS":  self._workers_spin.value(),
            "LOG_LEVEL":    self._log_level.currentText(),
            "PROXY_URL":    self._proxy_input.text().strip(),
            "EXPORT_DIR":   self._export_dir_input.text(),
        }
        settings_path = PROJECT_ROOT / "settings.json"
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self._unsaved = False
        log.info("Paramètres sauvegardés", path=str(settings_path))
        QMessageBox.information(
            self, "Sauvegardé", "Paramètres enregistrés.\n"
            "Certains changements nécessitent un redémarrage."
        )

    def _reset_settings(self) -> None:
        reply = QMessageBox.question(
            self, "Réinitialiser",
            "Supprimer le fichier settings.json et revenir aux valeurs par défaut ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        settings_path = PROJECT_ROOT / "settings.json"
        if settings_path.exists():
            settings_path.unlink()
        QMessageBox.information(self, "Réinitialisation", "Paramètres réinitialisés. Redémarrez l'application.")
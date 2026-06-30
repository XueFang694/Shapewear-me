"""
Vue Paramètres Phase 3 — Étend la Phase 2 avec :
  - Sélecteur de marché géographique (US, FR, IT, ES, ZH, GB, …)
  - Configuration du planificateur (mode, heure, jour)
  - Gestion du pool de proxies rotatifs
  - Serveur API REST (activation, port, statut)
  - Tous les paramètres Phase 2 conservés
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from PySide6.QtCore import QTime

from app.core.config import settings, PROJECT_ROOT
from app.core.logger import get_logger
from app.core.market import list_markets

log = get_logger(__name__)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        f = QFont(); f.setBold(True); f.setPointSize(10)
        self.setFont(f)
        self.setStyleSheet("color: #1E293B; padding-top: 8px;")


def _make_group(title: str = "") -> QGroupBox:
    grp = QGroupBox(title)
    grp.setStyleSheet(
        "QGroupBox { border: 1px solid #E2E8F0; border-radius: 8px; "
        "background: white; padding: 10px 14px; margin-top: 4px; }"
        "QGroupBox::title { color: #64748B; font-size: 9pt; subcontrol-origin: margin; left: 10px; }"
    )
    return grp


def _row(label: str, widget: QWidget, desc: str = "") -> QWidget:
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 2, 0, 2)
    lbl = QLabel(label)
    lbl.setMinimumWidth(200)
    lbl.setStyleSheet("color: #475569; font-size: 9pt;")
    l.addWidget(lbl)
    l.addWidget(widget)
    if desc:
        d = QLabel(desc)
        d.setStyleSheet("color: #94A3B8; font-size: 8pt;")
        l.addWidget(d)
    l.addStretch()
    return w


class SettingsView(QScrollArea):
    """Vue Paramètres complète Phase 3 avec support marché géographique."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)

        container = QWidget()
        main = QVBoxLayout(container)
        main.setContentsMargins(24, 16, 24, 24)
        main.setSpacing(14)
        self.setWidget(container)

        # Titre
        title = QLabel("Paramètres")
        tf = QFont(); tf.setPointSize(14); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: #1E293B;")
        main.addWidget(title)

        subtitle = QLabel("Configuration de la plateforme Market Intelligence")
        subtitle.setStyleSheet("color: #64748B; font-size: 10pt;")
        main.addWidget(subtitle)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #E2E8F0;"); main.addWidget(sep)

        # ── MARCHÉ GÉOGRAPHIQUE ───────────────────────────────────────────
        main.addWidget(SectionTitle("🌍  Marché géographique"))
        market_grp = _make_group()
        market_l = QVBoxLayout(market_grp)

        market_info = QLabel(
            "Le marché actif détermine la devise, les formats de date et de prix dans "
            "les exports, ainsi que les en-têtes HTTP envoyés aux sites cibles."
        )
        market_info.setWordWrap(True)
        market_info.setStyleSheet("color: #64748B; font-size: 9pt;")
        market_l.addWidget(market_info)

        # Combo de sélection du marché
        self._market_combo = QComboBox()
        self._market_combo.setFixedWidth(300)
        self._market_combo.setStyleSheet(
            "QComboBox { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 10px; }"
        )
        for m in list_markets():
            flag = self._market_flag(m.slug)
            self._market_combo.addItem(
                f"{flag}  {m.name} ({m.slug.upper()})  —  {m.currency}",
                m.slug,
            )
        # Sélectionner le marché actif
        current_market = getattr(settings, "MARKET", "us")
        for i in range(self._market_combo.count()):
            if self._market_combo.itemData(i) == current_market:
                self._market_combo.setCurrentIndex(i)
                break
        self._market_combo.currentIndexChanged.connect(self._on_market_changed)
        market_l.addWidget(_row("Marché actif", self._market_combo))

        # Aperçu du marché sélectionné
        self._market_preview = QLabel()
        self._market_preview.setStyleSheet(
            "background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 6px; "
            "padding: 8px 12px; color: #475569; font-size: 8pt; font-family: monospace;"
        )
        self._market_preview.setWordWrap(True)
        market_l.addWidget(self._market_preview)
        self._refresh_market_preview()

        main.addWidget(market_grp)

        # ── SCRAPING ──────────────────────────────────────────────────────
        main.addWidget(SectionTitle("🕷️  Scraping"))
        sc_grp = _make_group()
        sc_l = QVBoxLayout(sc_grp)

        self._workers = QSpinBox()
        self._workers.setRange(1, 6)
        self._workers.setValue(getattr(settings, "MAX_WORKERS", 2))
        self._workers.setFixedWidth(80)
        sc_l.addWidget(_row("Threads de scraping", self._workers, "1=séquentiel, 2-4=parallèle"))

        self._delay_min = QDoubleSpinBox()
        self._delay_min.setRange(0.5, 10.0); self._delay_min.setSingleStep(0.5)
        self._delay_min.setSuffix(" s"); self._delay_min.setValue(1.5)
        self._delay_min.setFixedWidth(90)
        sc_l.addWidget(_row("Délai minimum", self._delay_min))

        self._delay_max = QDoubleSpinBox()
        self._delay_max.setRange(1.0, 20.0); self._delay_max.setSingleStep(0.5)
        self._delay_max.setSuffix(" s"); self._delay_max.setValue(4.0)
        self._delay_max.setFixedWidth(90)
        sc_l.addWidget(_row("Délai maximum", self._delay_max))

        self._rotate_ua = QCheckBox("Rotation des User-Agents")
        self._rotate_ua.setChecked(True)
        sc_l.addWidget(self._rotate_ua)
        main.addWidget(sc_grp)

        # ── PLANIFICATEUR ─────────────────────────────────────────────────
        main.addWidget(SectionTitle("⏰  Planification automatique"))
        sched_grp = _make_group()
        sched_l = QVBoxLayout(sched_grp)

        self._sched_enabled = QCheckBox("Activer la planification automatique")
        sched_l.addWidget(self._sched_enabled)

        self._sched_mode = QComboBox()
        self._sched_mode.addItems(["Manuel", "Quotidien", "Hebdomadaire"])
        self._sched_mode.setFixedWidth(160)
        self._sched_mode.currentIndexChanged.connect(self._on_sched_mode_changed)
        sched_l.addWidget(_row("Mode", self._sched_mode))

        self._sched_time = QTimeEdit()
        self._sched_time.setTime(QTime(2, 0))
        self._sched_time.setDisplayFormat("HH:mm")
        self._sched_time.setFixedWidth(90)
        sched_l.addWidget(_row("Heure d'exécution", self._sched_time))

        self._sched_weekday = QComboBox()
        self._sched_weekday.addItems([
            "Lundi", "Mardi", "Mercredi", "Jeudi",
            "Vendredi", "Samedi", "Dimanche",
        ])
        self._sched_weekday.setFixedWidth(140)
        self._sched_weekday_row = _row("Jour (hebdomadaire)", self._sched_weekday)
        self._sched_weekday_row.hide()
        sched_l.addWidget(self._sched_weekday_row)

        self._sched_status_label = QLabel("Statut : —")
        self._sched_status_label.setStyleSheet("color: #64748B; font-size: 8pt; padding-top: 4px;")
        sched_l.addWidget(self._sched_status_label)

        self._load_scheduler_config()
        self._refresh_scheduler_status()
        timer = QTimer(self)
        timer.timeout.connect(self._refresh_scheduler_status)
        timer.start(30_000)
        main.addWidget(sched_grp)

        # ── PROXIES ───────────────────────────────────────────────────────
        main.addWidget(SectionTitle("🌐  Pool de Proxies Rotatifs"))
        proxy_grp = _make_group()
        proxy_l = QVBoxLayout(proxy_grp)

        proxy_l.addWidget(QLabel(
            "Entrez un proxy par ligne (http://user:pass@ip:port ou socks5://...) :"
        ))
        self._proxy_list_widget = QListWidget()
        self._proxy_list_widget.setMaximumHeight(120)
        self._proxy_list_widget.setStyleSheet(
            "QListWidget { border: 1px solid #CBD5E1; border-radius: 6px; "
            "font-size: 8pt; font-family: monospace; }"
        )
        proxy_l.addWidget(self._proxy_list_widget)

        proxy_edit_row = QHBoxLayout()
        self._proxy_input = QLineEdit()
        self._proxy_input.setPlaceholderText("http://user:pass@ip:port")
        self._proxy_input.setStyleSheet(
            "QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 8px; }"
        )
        proxy_edit_row.addWidget(self._proxy_input, 1)
        btn_add_proxy = QPushButton("+ Ajouter")
        btn_add_proxy.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; padding: 4px 10px; font-size: 8pt; }"
        )
        btn_add_proxy.clicked.connect(self._add_proxy)
        proxy_edit_row.addWidget(btn_add_proxy)
        btn_remove_proxy = QPushButton("Supprimer")
        btn_remove_proxy.setStyleSheet(
            "QPushButton { background: #DC2626; color: white; border-radius: 6px; padding: 4px 10px; font-size: 8pt; }"
        )
        btn_remove_proxy.clicked.connect(self._remove_proxy)
        proxy_edit_row.addWidget(btn_remove_proxy)
        proxy_edit_w = QWidget(); proxy_edit_w.setLayout(proxy_edit_row)
        proxy_l.addWidget(proxy_edit_w)

        proxy_opts_row = QHBoxLayout()
        proxy_opts_row.addWidget(QLabel("Stratégie :"))
        self._proxy_strategy = QComboBox()
        self._proxy_strategy.addItems(["round_robin", "random", "sticky"])
        self._proxy_strategy.setFixedWidth(130)
        proxy_opts_row.addWidget(self._proxy_strategy)
        proxy_opts_row.addStretch()
        proxy_opts_w = QWidget(); proxy_opts_w.setLayout(proxy_opts_row)
        proxy_l.addWidget(proxy_opts_w)
        self._proxy_stats_label = QLabel("")
        self._proxy_stats_label.setStyleSheet("color: #64748B; font-size: 8pt;")
        proxy_l.addWidget(self._proxy_stats_label)
        self._load_proxy_config()
        main.addWidget(proxy_grp)

        # ── API REST ──────────────────────────────────────────────────────
        main.addWidget(SectionTitle("🔌  API REST Locale"))
        api_grp = _make_group()
        api_l = QVBoxLayout(api_grp)
        api_info = QLabel(
            "L'API REST expose les données via HTTP pour les intégrations externes "
            "(Power BI, Google Sheets, scripts Python, etc.)."
        )
        api_info.setWordWrap(True); api_info.setStyleSheet("color: #64748B; font-size: 9pt;")
        api_l.addWidget(api_info)
        api_row = QHBoxLayout()
        self._api_port = QSpinBox()
        self._api_port.setRange(1024, 65535); self._api_port.setValue(8765)
        self._api_port.setFixedWidth(90)
        api_row.addWidget(QLabel("Port :")); api_row.addWidget(self._api_port)
        api_row.addStretch()
        self._api_btn = QPushButton("▶ Démarrer l'API")
        self._api_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; padding: 5px 14px; font-weight: bold; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._api_btn.clicked.connect(self._toggle_api)
        api_row.addWidget(self._api_btn)
        btn_open_docs = QPushButton("📖 Ouvrir /docs")
        btn_open_docs.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 5px 10px; }"
        )
        btn_open_docs.clicked.connect(self._open_api_docs)
        api_row.addWidget(btn_open_docs)
        api_row_w = QWidget(); api_row_w.setLayout(api_row)
        api_l.addWidget(api_row_w)
        self._api_status_label = QLabel("Statut : Arrêtée")
        self._api_status_label.setStyleSheet("color: #64748B; font-size: 8pt;")
        api_l.addWidget(self._api_status_label)
        main.addWidget(api_grp)

        # ── BASE DE DONNÉES ───────────────────────────────────────────────
        main.addWidget(SectionTitle("🗄️  Base de données"))
        db_grp = _make_group()
        db_l = QVBoxLayout(db_grp)
        from app.storage.database import get_active_db_url
        active_url = get_active_db_url() or settings.DATABASE_URL
        db_path_lbl = QLabel(active_url.replace("sqlite:///", ""))
        db_path_lbl.setStyleSheet(
            "color: #475569; font-size: 8pt; font-family: monospace; "
            "background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 4px; padding: 4px 8px;"
        )
        db_path_lbl.setWordWrap(True)
        db_l.addWidget(QLabel("Chemin actuel :")); db_l.addWidget(db_path_lbl)
        db_btn_row = QHBoxLayout()
        for label, slot, style in [
            ("✓ Tester",       self._test_db,    "border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 10px;"),
            ("💾 Sauvegarder", self._backup_db,  "background: #2563EB; color: white; border-radius: 6px; padding: 4px 10px;"),
            ("↺ Restaurer",    self._restore_db, "background: #D97706; color: white; border-radius: 6px; padding: 4px 10px;"),
        ]:
            btn = QPushButton(label)
            btn.setStyleSheet(f"QPushButton {{ {style} }}")
            btn.clicked.connect(slot)
            db_btn_row.addWidget(btn)
        db_btn_row.addStretch()
        purge_row = QHBoxLayout()
        purge_row.addWidget(QLabel("Purger les snapshots > "))
        self._purge_days = QSpinBox()
        self._purge_days.setRange(30, 365); self._purge_days.setValue(180)
        self._purge_days.setSuffix(" jours"); self._purge_days.setFixedWidth(110)
        purge_row.addWidget(self._purge_days)
        btn_purge = QPushButton("Purger")
        btn_purge.setStyleSheet(
            "QPushButton { background: #DC2626; color: white; border-radius: 6px; padding: 4px 8px; }"
        )
        btn_purge.clicked.connect(self._purge_snapshots)
        purge_row.addWidget(btn_purge); purge_row.addStretch()
        db_btn_w = QWidget(); db_btn_w.setLayout(db_btn_row)
        db_l.addWidget(db_btn_w)
        purge_w = QWidget(); purge_w.setLayout(purge_row)
        db_l.addWidget(purge_w)
        main.addWidget(db_grp)

        # ── LOGS ─────────────────────────────────────────────────────────
        main.addWidget(SectionTitle("📋  Logs"))
        log_grp = _make_group()
        log_l = QVBoxLayout(log_grp)
        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level.setCurrentText(getattr(settings, "LOG_LEVEL", "INFO"))
        self._log_level.setFixedWidth(120)
        log_l.addWidget(_row("Niveau de log", self._log_level))
        btn_open_logs = QPushButton("📁 Ouvrir le dossier de logs")
        btn_open_logs.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 10px; }"
        )
        btn_open_logs.clicked.connect(self._open_log_dir)
        log_l.addWidget(btn_open_logs)
        main.addWidget(log_grp)

        # ── Boutons save/reset ────────────────────────────────────────────
        action_row = QHBoxLayout()
        self._save_btn = QPushButton("💾 Sauvegarder tous les paramètres")
        self._save_btn.setMinimumHeight(36)
        self._save_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 0 20px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._save_btn.clicked.connect(self._save_all)
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
        main.addWidget(action_w)
        main.addStretch()

    # ── Marché ───────────────────────────────────────────────────────────

    def _on_market_changed(self, _idx: int) -> None:
        self._refresh_market_preview()

    def _refresh_market_preview(self) -> None:
        from app.core.market import get_market
        slug = self._market_combo.currentData() or "us"
        try:
            m = get_market(slug)
            example_price = m.format_price(68.00)
            example_date  = m.format_date(datetime.now())
            self._market_preview.setText(
                f"Locale : {m.locale}   |   Devise : {m.currency} ({m.currency_symbol})   |   "
                f"Exemple prix : {example_price}   |   Exemple date : {example_date}\n"
                f"Accept-Language : {m.accept_language}"
            )
        except Exception:
            self._market_preview.setText("—")

    @staticmethod
    def _market_flag(slug: str) -> str:
        """Emoji drapeau approximatif pour affichage dans le combo."""
        flags = {
            "us": "🇺🇸", "fr": "🇫🇷", "de": "🇩🇪", "it": "🇮🇹",
            "es": "🇪🇸", "gb": "🇬🇧", "nl": "🇳🇱", "be": "🇧🇪",
            "ch": "🇨🇭", "pt": "🇵🇹", "pl": "🇵🇱", "se": "🇸🇪",
            "no": "🇳🇴", "dk": "🇩🇰", "ca": "🇨🇦", "mx": "🇲🇽",
            "br": "🇧🇷", "au": "🇦🇺", "in": "🇮🇳", "zh": "🇨🇳",
            "tw": "🇹🇼", "jp": "🇯🇵", "kr": "🇰🇷",
        }
        return flags.get(slug.lower(), "🌐")

    # ── Planificateur ─────────────────────────────────────────────────────

    def _on_sched_mode_changed(self, idx: int) -> None:
        self._sched_weekday_row.setVisible(idx == 2)

    def _load_scheduler_config(self) -> None:
        try:
            from app.workflow.scheduler import Scheduler
            sched = Scheduler()
            cfg = sched.get_config()
            self._sched_enabled.setChecked(cfg.enabled)
            mode_map = {"manual": 0, "daily": 1, "weekly": 2}
            self._sched_mode.setCurrentIndex(mode_map.get(cfg.mode, 0))
            self._sched_time.setTime(QTime(cfg.hour, cfg.minute))
            self._sched_weekday.setCurrentIndex(cfg.weekday)
            self._sched_weekday_row.setVisible(cfg.mode == "weekly")
        except Exception as exc:
            log.debug("Config scheduler non chargée", error=str(exc))

    def _refresh_scheduler_status(self) -> None:
        try:
            from app.workflow.scheduler import Scheduler
            status = Scheduler().get_status()
            cfg_desc = status.get("description", "—")
            next_run = status.get("next_run")
            if next_run:
                from datetime import datetime as dt
                try:
                    nr = dt.fromisoformat(next_run)
                    cfg_desc += f"  —  Prochaine : {nr.strftime('%d/%m %H:%M')}"
                except Exception:
                    pass
            self._sched_status_label.setText(f"Statut : {cfg_desc}")
        except Exception:
            self._sched_status_label.setText("Statut : —")

    # ── Proxies ──────────────────────────────────────────────────────────

    def _load_proxy_config(self) -> None:
        try:
            settings_path = PROJECT_ROOT / "settings.json"
            if settings_path.exists():
                with open(settings_path, encoding="utf-8") as f:
                    data = json.load(f)
                for proxy in data.get("PROXY_LIST", []):
                    self._proxy_list_widget.addItem(proxy)
                strategy = data.get("PROXY_STRATEGY", "round_robin")
                idx = self._proxy_strategy.findText(strategy)
                if idx >= 0:
                    self._proxy_strategy.setCurrentIndex(idx)
        except Exception:
            pass

    def _add_proxy(self) -> None:
        url = self._proxy_input.text().strip()
        if url:
            self._proxy_list_widget.addItem(url)
            self._proxy_input.clear()

    def _remove_proxy(self) -> None:
        row = self._proxy_list_widget.currentRow()
        if row >= 0:
            self._proxy_list_widget.takeItem(row)

    # ── API REST ──────────────────────────────────────────────────────────

    def _toggle_api(self) -> None:
        try:
            from app.api.server import start_api_server, stop_api_server, is_api_running
            if is_api_running():
                stop_api_server()
                self._api_btn.setText("▶ Démarrer l'API")
                self._api_status_label.setText("Statut : Arrêtée")
            else:
                port = self._api_port.value()
                url  = start_api_server(port=port)
                self._api_btn.setText("⏹ Arrêter l'API")
                self._api_status_label.setText(f"Statut : Active sur {url}")
        except ImportError:
            QMessageBox.information(self, "API REST", "Module app.api.server non disponible.")

    def _open_api_docs(self) -> None:
        import webbrowser
        webbrowser.open(f"http://127.0.0.1:{self._api_port.value()}/docs")

    # ── Base de données ──────────────────────────────────────────────────

    def _test_db(self) -> None:
        from app.storage.database import check_db_connection
        ok = check_db_connection()
        if ok:
            QMessageBox.information(self, "Connexion DB", "✓ Connexion à la base réussie.")
        else:
            QMessageBox.critical(self, "Connexion DB", "✗ Impossible de se connecter.")

    def _backup_db(self) -> None:
        from app.storage.database import get_active_db_url
        url = get_active_db_url() or ""
        if not url.startswith("sqlite:///"):
            QMessageBox.warning(self, "Sauvegarde", "SQLite uniquement.")
            return
        db_path = Path(url.replace("sqlite:///", ""))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db_path.parent / f"shapewear_backup_{ts}.db"
        shutil.copy2(db_path, backup)
        QMessageBox.information(self, "Sauvegarde OK", f"Sauvegardé :\n{backup}")

    def _restore_db(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, "Choisir une sauvegarde", str(settings.DATA_DIR), "SQLite (*.db)"
        )
        if not file:
            return
        reply = QMessageBox.question(
            self, "Restauration", "Remplacer la base actuelle ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        from app.storage.database import dispose_engine, get_active_db_url
        db_path = Path((get_active_db_url() or "").replace("sqlite:///", ""))
        dispose_engine()
        shutil.copy2(file, db_path)
        QMessageBox.information(self, "Restauration OK", "Base restaurée. Redémarrez l'application.")

    def _purge_snapshots(self) -> None:
        days = self._purge_days.value()
        reply = QMessageBox.question(
            self, "Purge",
            f"Supprimer les snapshots de plus de {days} jours ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        from app.storage.database import get_db
        from app.storage.models import ProductSnapshot
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=days)
        with get_db() as db:
            deleted = db.query(ProductSnapshot).filter(
                ProductSnapshot.crawled_at < cutoff
            ).delete()
        QMessageBox.information(self, "Purge", f"{deleted} snapshot(s) supprimés.")

    def _open_log_dir(self) -> None:
        import subprocess, sys
        path = str(settings.LOG_DIR)
        if sys.platform == "win32":
            subprocess.Popen(["explorer", path])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ── Sauvegarde ───────────────────────────────────────────────────────

    def _save_all(self) -> None:
        proxies = [
            self._proxy_list_widget.item(i).text()
            for i in range(self._proxy_list_widget.count())
        ]
        mode_map = {0: "manual", 1: "daily", 2: "weekly"}
        t = self._sched_time.time()

        selected_market = self._market_combo.currentData() or "us"

        data = {
            "MARKET":            selected_market,
            "MAX_WORKERS":       self._workers.value(),
            "LOG_LEVEL":         self._log_level.currentText(),
            "PROXY_LIST":        proxies,
            "PROXY_STRATEGY":    self._proxy_strategy.currentText(),
            "schedule": {
                "mode":    mode_map.get(self._sched_mode.currentIndex(), "manual"),
                "hour":    t.hour(),
                "minute":  t.minute(),
                "weekday": self._sched_weekday.currentIndex(),
                "enabled": self._sched_enabled.isChecked(),
                "brands":  None,
            },
        }

        settings_path = PROJECT_ROOT / "settings.json"
        existing = {}
        if settings_path.exists():
            try:
                with open(settings_path, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                pass
        existing.update(data)
        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)

        try:
            from app.workflow.scheduler import Scheduler
            sched = Scheduler()
            sched.configure(
                mode=data["schedule"]["mode"],
                hour=t.hour(),
                minute=t.minute(),
                weekday=self._sched_weekday.currentIndex(),
                enabled=self._sched_enabled.isChecked(),
            )
        except Exception as exc:
            log.warning("Impossible de configurer le scheduler", error=str(exc))

        log.info("Paramètres sauvegardés", market=selected_market)
        QMessageBox.information(
            self, "Sauvegardé",
            f"Paramètres enregistrés (marché : {selected_market.upper()}).\n"
            "Certains changements nécessitent un redémarrage."
        )

    def _reset_settings(self) -> None:
        reply = QMessageBox.question(
            self, "Réinitialiser",
            "Supprimer settings.json et revenir aux valeurs par défaut ?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return
        p = PROJECT_ROOT / "settings.json"
        if p.exists():
            p.unlink()
        QMessageBox.information(self, "Réinitialisé", "Redémarrez l'application.")
"""
Vue Paramètres Phase 3 — Étend la Phase 2 avec :
  - Sélecteur de marché géographique (US, FR, IT, ES, ZH, GB, …)
  - Configuration du planificateur (mode, heure, jour)
  - Gestion du pool de proxies rotatifs
  - Serveur API REST (activation, port, statut)
  - Tous les paramètres Phase 2 conservés

Correctifs UI :
  - Contraste amélioré sur tous les widgets (labels, spinboxes, combos, checkboxes)
  - Bouton de reset complet de la base de données avec confirmation
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

# ── Feuille de styles globale ─────────────────────────────────────────────────
# Appliquée à toute la vue pour garantir un contraste suffisant partout.
_GLOBAL_STYLE = """
    QLabel {
        color: #1E293B;
        font-size: 9pt;
    }
    QGroupBox {
        border: 1px solid #CBD5E1;
        border-radius: 8px;
        background: #FFFFFF;
        padding: 10px 14px;
        margin-top: 4px;
        color: #1E293B;
        font-size: 9pt;
        font-weight: bold;
    }
    QGroupBox::title {
        color: #475569;
        font-size: 9pt;
        subcontrol-origin: margin;
        left: 10px;
    }
    QSpinBox, QDoubleSpinBox {
        border: 1px solid #94A3B8;
        border-radius: 4px;
        padding: 3px 6px;
        background: #FFFFFF;
        color: #1E293B;
        font-size: 9pt;
    }
    QSpinBox:focus, QDoubleSpinBox:focus {
        border-color: #2563EB;
    }
    QComboBox {
        border: 1px solid #94A3B8;
        border-radius: 4px;
        padding: 3px 8px;
        background: #FFFFFF;
        color: #1E293B;
        font-size: 9pt;
    }
    QComboBox:focus {
        border-color: #2563EB;
    }
    QComboBox QAbstractItemView {
        color: #1E293B;
        background: #FFFFFF;
        selection-background-color: #EFF6FF;
        selection-color: #1E293B;
    }
    QTimeEdit {
        border: 1px solid #94A3B8;
        border-radius: 4px;
        padding: 3px 6px;
        background: #FFFFFF;
        color: #1E293B;
        font-size: 9pt;
    }
    QCheckBox {
        color: #1E293B;
        font-size: 9pt;
        spacing: 6px;
    }
    QCheckBox::indicator {
        width: 16px;
        height: 16px;
        border-radius: 3px;
        border: 2px solid #94A3B8;
        background: #FFFFFF;
    }
    QCheckBox::indicator:checked {
        border-color: #2563EB;
        background: #2563EB;
    }
    QCheckBox::indicator:hover {
        border-color: #2563EB;
    }
    QLineEdit {
        border: 1px solid #94A3B8;
        border-radius: 4px;
        padding: 4px 8px;
        background: #FFFFFF;
        color: #1E293B;
        font-size: 9pt;
    }
    QLineEdit:focus {
        border-color: #2563EB;
    }
    QListWidget {
        border: 1px solid #CBD5E1;
        border-radius: 6px;
        background: #FFFFFF;
        color: #1E293B;
        font-size: 8pt;
        font-family: monospace;
    }
    QScrollArea {
        border: none;
        background: transparent;
    }
"""

# ── Couleurs de boutons ───────────────────────────────────────────────────────
_BTN_PRIMARY = (
    "QPushButton { background: #2563EB; color: #FFFFFF; border-radius: 6px; "
    "padding: 5px 14px; font-size: 9pt; font-weight: bold; border: none; }"
    "QPushButton:hover { background: #1D4ED8; }"
    "QPushButton:disabled { background: #CBD5E1; color: #94A3B8; }"
)
_BTN_SUCCESS = (
    "QPushButton { background: #16A34A; color: #FFFFFF; border-radius: 6px; "
    "padding: 5px 14px; font-size: 9pt; font-weight: bold; border: none; }"
    "QPushButton:hover { background: #15803D; }"
)
_BTN_WARNING = (
    "QPushButton { background: #D97706; color: #FFFFFF; border-radius: 6px; "
    "padding: 5px 14px; font-size: 9pt; font-weight: bold; border: none; }"
    "QPushButton:hover { background: #B45309; }"
)
_BTN_DANGER = (
    "QPushButton { background: #DC2626; color: #FFFFFF; border-radius: 6px; "
    "padding: 5px 14px; font-size: 9pt; font-weight: bold; border: none; }"
    "QPushButton:hover { background: #B91C1C; }"
)
_BTN_NEUTRAL = (
    "QPushButton { background: #FFFFFF; color: #374151; border-radius: 6px; "
    "padding: 5px 12px; font-size: 9pt; border: 1px solid #CBD5E1; }"
    "QPushButton:hover { background: #F1F5F9; }"
)


class SectionTitle(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        f = QFont()
        f.setBold(True)
        f.setPointSize(10)
        self.setFont(f)
        self.setStyleSheet("color: #1E293B; padding-top: 8px; font-size: 10pt;")


def _make_group(title: str = "") -> QGroupBox:
    grp = QGroupBox(title)
    return grp


def _row(label: str, widget: QWidget, desc: str = "") -> QWidget:
    w = QWidget()
    l = QHBoxLayout(w)
    l.setContentsMargins(0, 2, 0, 2)
    lbl = QLabel(label)
    lbl.setMinimumWidth(200)
    lbl.setStyleSheet("color: #374151; font-size: 9pt;")
    l.addWidget(lbl)
    l.addWidget(widget)
    if desc:
        d = QLabel(desc)
        d.setStyleSheet("color: #64748B; font-size: 8pt;")
        l.addWidget(d)
    l.addStretch()
    return w


class SettingsView(QScrollArea):
    """Vue Paramètres complète Phase 3 avec support marché géographique."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(_GLOBAL_STYLE)

        container = QWidget()
        container.setStyleSheet("background: #F8FAFC;")
        main = QVBoxLayout(container)
        main.setContentsMargins(24, 16, 24, 24)
        main.setSpacing(14)
        self.setWidget(container)

        # Titre
        title = QLabel("Paramètres")
        tf = QFont()
        tf.setPointSize(14)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: #1E293B; font-size: 14pt;")
        main.addWidget(title)

        subtitle = QLabel("Configuration de la plateforme Market Intelligence")
        subtitle.setStyleSheet("color: #64748B; font-size: 10pt;")
        main.addWidget(subtitle)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #CBD5E1; background: #CBD5E1; max-height: 1px;")
        main.addWidget(sep)

        # ── MARCHÉ GÉOGRAPHIQUE ───────────────────────────────────────────
        main.addWidget(SectionTitle("🌍  Marché géographique"))
        market_grp = _make_group()
        market_l = QVBoxLayout(market_grp)

        market_info = QLabel(
            "Le marché actif détermine la devise, les formats de date et de prix dans "
            "les exports, ainsi que les en-têtes HTTP envoyés aux sites cibles."
        )
        market_info.setWordWrap(True)
        market_info.setStyleSheet("color: #475569; font-size: 9pt;")
        market_l.addWidget(market_info)

        self._market_combo = QComboBox()
        self._market_combo.setFixedWidth(320)
        for m in list_markets():
            flag = self._market_flag(m.slug)
            self._market_combo.addItem(
                f"{flag}  {m.name} ({m.slug.upper()})  —  {m.currency}",
                m.slug,
            )
        current_market = getattr(settings, "MARKET", "us")
        for i in range(self._market_combo.count()):
            if self._market_combo.itemData(i) == current_market:
                self._market_combo.setCurrentIndex(i)
                break
        self._market_combo.currentIndexChanged.connect(self._on_market_changed)
        market_l.addWidget(_row("Marché actif", self._market_combo))

        self._market_preview = QLabel()
        self._market_preview.setStyleSheet(
            "background: #F1F5F9; border: 1px solid #CBD5E1; border-radius: 6px; "
            "padding: 8px 12px; color: #374151; font-size: 8pt; font-family: monospace;"
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
        sc_l.addWidget(_row("Threads de scraping", self._workers, "1 = séquentiel, 2–4 = parallèle"))

        self._delay_min = QDoubleSpinBox()
        self._delay_min.setRange(0.5, 10.0)
        self._delay_min.setSingleStep(0.5)
        self._delay_min.setSuffix(" s")
        self._delay_min.setValue(1.5)
        self._delay_min.setFixedWidth(90)
        sc_l.addWidget(_row("Délai minimum", self._delay_min))

        self._delay_max = QDoubleSpinBox()
        self._delay_max.setRange(1.0, 20.0)
        self._delay_max.setSingleStep(0.5)
        self._delay_max.setSuffix(" s")
        self._delay_max.setValue(4.0)
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
        self._sched_mode.setFixedWidth(180)
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
        self._sched_weekday.setFixedWidth(160)
        self._sched_weekday_row = _row("Jour (hebdomadaire)", self._sched_weekday)
        self._sched_weekday_row.hide()
        sched_l.addWidget(self._sched_weekday_row)

        self._sched_status_label = QLabel("Statut : —")
        self._sched_status_label.setStyleSheet("color: #475569; font-size: 8pt; padding-top: 4px;")
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

        proxy_info_lbl = QLabel(
            "Entrez un proxy par ligne (http://user:pass@ip:port ou socks5://...) :"
        )
        proxy_info_lbl.setStyleSheet("color: #374151; font-size: 9pt;")
        proxy_l.addWidget(proxy_info_lbl)

        self._proxy_list_widget = QListWidget()
        self._proxy_list_widget.setMaximumHeight(120)
        proxy_l.addWidget(self._proxy_list_widget)

        proxy_edit_row = QHBoxLayout()
        self._proxy_input = QLineEdit()
        self._proxy_input.setPlaceholderText("http://user:pass@ip:port")
        proxy_edit_row.addWidget(self._proxy_input, 1)

        btn_add_proxy = QPushButton("+ Ajouter")
        btn_add_proxy.setStyleSheet(_BTN_PRIMARY)
        btn_add_proxy.clicked.connect(self._add_proxy)
        proxy_edit_row.addWidget(btn_add_proxy)

        btn_remove_proxy = QPushButton("Supprimer")
        btn_remove_proxy.setStyleSheet(_BTN_DANGER)
        btn_remove_proxy.clicked.connect(self._remove_proxy)
        proxy_edit_row.addWidget(btn_remove_proxy)

        proxy_edit_w = QWidget()
        proxy_edit_w.setLayout(proxy_edit_row)
        proxy_l.addWidget(proxy_edit_w)

        proxy_opts_row = QHBoxLayout()
        strat_lbl = QLabel("Stratégie :")
        strat_lbl.setStyleSheet("color: #374151; font-size: 9pt;")
        proxy_opts_row.addWidget(strat_lbl)
        self._proxy_strategy = QComboBox()
        self._proxy_strategy.addItems(["round_robin", "random", "sticky"])
        self._proxy_strategy.setFixedWidth(140)
        proxy_opts_row.addWidget(self._proxy_strategy)
        proxy_opts_row.addStretch()
        proxy_opts_w = QWidget()
        proxy_opts_w.setLayout(proxy_opts_row)
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
        api_info.setWordWrap(True)
        api_info.setStyleSheet("color: #475569; font-size: 9pt;")
        api_l.addWidget(api_info)

        api_row = QHBoxLayout()
        port_lbl = QLabel("Port :")
        port_lbl.setStyleSheet("color: #374151; font-size: 9pt;")
        self._api_port = QSpinBox()
        self._api_port.setRange(1024, 65535)
        self._api_port.setValue(8765)
        self._api_port.setFixedWidth(90)
        api_row.addWidget(port_lbl)
        api_row.addWidget(self._api_port)
        api_row.addStretch()

        self._api_btn = QPushButton("▶ Démarrer l'API")
        self._api_btn.setStyleSheet(_BTN_PRIMARY)
        self._api_btn.clicked.connect(self._toggle_api)
        api_row.addWidget(self._api_btn)

        btn_open_docs = QPushButton("📖 Ouvrir /docs")
        btn_open_docs.setStyleSheet(_BTN_NEUTRAL)
        btn_open_docs.clicked.connect(self._open_api_docs)
        api_row.addWidget(btn_open_docs)

        api_row_w = QWidget()
        api_row_w.setLayout(api_row)
        api_l.addWidget(api_row_w)

        self._api_status_label = QLabel("Statut : Arrêtée")
        self._api_status_label.setStyleSheet("color: #64748B; font-size: 8pt;")
        api_l.addWidget(self._api_status_label)
        main.addWidget(api_grp)

        # ── BASE DE DONNÉES ───────────────────────────────────────────────
        main.addWidget(SectionTitle("🗄️  Base de données"))
        db_grp = _make_group()
        db_l = QVBoxLayout(db_grp)

        path_title = QLabel("Chemin actuel :")
        path_title.setStyleSheet("color: #374151; font-size: 9pt; font-weight: bold;")
        db_l.addWidget(path_title)

        from app.storage.database import get_active_db_url
        active_url = get_active_db_url() or settings.DATABASE_URL
        db_path_lbl = QLabel(active_url.replace("sqlite:///", ""))
        db_path_lbl.setStyleSheet(
            "color: #1E293B; font-size: 8pt; font-family: monospace; "
            "background: #F1F5F9; border: 1px solid #CBD5E1; border-radius: 4px; padding: 5px 10px;"
        )
        db_path_lbl.setWordWrap(True)
        db_l.addWidget(db_path_lbl)

        # Boutons principaux
        db_btn_row = QHBoxLayout()
        btn_test_db = QPushButton("✓ Tester")
        btn_test_db.setStyleSheet(_BTN_NEUTRAL)
        btn_test_db.clicked.connect(self._test_db)
        db_btn_row.addWidget(btn_test_db)

        btn_backup = QPushButton("💾 Sauvegarder")
        btn_backup.setStyleSheet(_BTN_PRIMARY)
        btn_backup.clicked.connect(self._backup_db)
        db_btn_row.addWidget(btn_backup)

        btn_restore = QPushButton("↺ Restaurer")
        btn_restore.setStyleSheet(_BTN_WARNING)
        btn_restore.clicked.connect(self._restore_db)
        db_btn_row.addWidget(btn_restore)

        db_btn_row.addStretch()
        db_btn_w = QWidget()
        db_btn_w.setLayout(db_btn_row)
        db_l.addWidget(db_btn_w)

        # Purge des snapshots
        purge_row = QHBoxLayout()
        purge_lbl = QLabel("Purger les snapshots > ")
        purge_lbl.setStyleSheet("color: #374151; font-size: 9pt;")
        purge_row.addWidget(purge_lbl)
        self._purge_days = QSpinBox()
        self._purge_days.setRange(30, 365)
        self._purge_days.setValue(180)
        self._purge_days.setSuffix(" jours")
        self._purge_days.setFixedWidth(120)
        purge_row.addWidget(self._purge_days)

        btn_purge = QPushButton("Purger")
        btn_purge.setStyleSheet(_BTN_DANGER)
        btn_purge.clicked.connect(self._purge_snapshots)
        purge_row.addWidget(btn_purge)
        purge_row.addStretch()

        purge_w = QWidget()
        purge_w.setLayout(purge_row)
        db_l.addWidget(purge_w)

        # ── Séparateur avant reset ────────────────────────────────────────
        sep_reset = QFrame()
        sep_reset.setFrameShape(QFrame.HLine)
        sep_reset.setStyleSheet("color: #FCA5A5; background: #FCA5A5; max-height: 1px; margin: 6px 0;")
        db_l.addWidget(sep_reset)

        # Zone de danger : reset complet
        danger_title = QLabel("⚠️  Zone de danger")
        danger_title.setStyleSheet("color: #DC2626; font-size: 9pt; font-weight: bold;")
        db_l.addWidget(danger_title)

        reset_row = QHBoxLayout()
        reset_info = QLabel(
            "Supprime <b>toutes</b> les données de la base (produits, snapshots, "
            "sessions, événements). Cette opération est irréversible."
        )
        reset_info.setWordWrap(True)
        reset_info.setStyleSheet("color: #374151; font-size: 9pt;")
        reset_row.addWidget(reset_info, 1)

        btn_reset_db = QPushButton("🗑️  Vider la base")
        btn_reset_db.setStyleSheet(_BTN_DANGER)
        btn_reset_db.setMinimumWidth(140)
        btn_reset_db.clicked.connect(self._reset_database)
        reset_row.addWidget(btn_reset_db)

        reset_w = QWidget()
        reset_w.setLayout(reset_row)
        db_l.addWidget(reset_w)

        main.addWidget(db_grp)

        # ── LOGS ─────────────────────────────────────────────────────────
        main.addWidget(SectionTitle("📋  Logs"))
        log_grp = _make_group()
        log_l = QVBoxLayout(log_grp)

        self._log_level = QComboBox()
        self._log_level.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level.setCurrentText(getattr(settings, "LOG_LEVEL", "INFO"))
        self._log_level.setFixedWidth(140)
        log_l.addWidget(_row("Niveau de log", self._log_level))

        btn_open_logs = QPushButton("📁 Ouvrir le dossier de logs")
        btn_open_logs.setStyleSheet(_BTN_NEUTRAL)
        btn_open_logs.clicked.connect(self._open_log_dir)
        log_l.addWidget(btn_open_logs)
        main.addWidget(log_grp)

        # ── Boutons save/reset ────────────────────────────────────────────
        action_row = QHBoxLayout()
        self._save_btn = QPushButton("💾 Sauvegarder tous les paramètres")
        self._save_btn.setMinimumHeight(36)
        self._save_btn.setStyleSheet(
            "QPushButton { background: #2563EB; color: #FFFFFF; border-radius: 6px; "
            "font-weight: bold; padding: 0 20px; font-size: 9pt; border: none; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._save_btn.clicked.connect(self._save_all)
        action_row.addWidget(self._save_btn)

        btn_reset_settings = QPushButton("↺ Réinitialiser les paramètres")
        btn_reset_settings.setMinimumHeight(36)
        btn_reset_settings.setStyleSheet(
            "QPushButton { background: #FFFFFF; color: #374151; border-radius: 6px; "
            "padding: 0 16px; font-size: 9pt; border: 1px solid #CBD5E1; }"
            "QPushButton:hover { background: #F1F5F9; }"
        )
        btn_reset_settings.clicked.connect(self._reset_settings)
        action_row.addWidget(btn_reset_settings)
        action_row.addStretch()

        action_w = QWidget()
        action_w.setLayout(action_row)
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
            example_date = m.format_date(datetime.now())
            self._market_preview.setText(
                f"Locale : {m.locale}   |   Devise : {m.currency} ({m.currency_symbol})   |   "
                f"Exemple prix : {example_price}   |   Exemple date : {example_date}\n"
                f"Accept-Language : {m.accept_language}"
            )
        except Exception:
            self._market_preview.setText("—")

    @staticmethod
    def _market_flag(slug: str) -> str:
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
                url = start_api_server(port=port)
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
            QMessageBox.warning(self, "Sauvegarde", "Sauvegarde disponible pour SQLite uniquement.")
            return
        db_path = Path(url.replace("sqlite:///", ""))
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = db_path.parent / f"shapewear_backup_{ts}.db"
        shutil.copy2(db_path, backup)
        QMessageBox.information(self, "Sauvegarde réussie", f"Base sauvegardée :\n{backup}")

    def _restore_db(self) -> None:
        file, _ = QFileDialog.getOpenFileName(
            self, "Choisir une sauvegarde", str(settings.DATA_DIR), "SQLite (*.db)"
        )
        if not file:
            return
        reply = QMessageBox.question(
            self, "Restauration",
            "Remplacer la base actuelle par cette sauvegarde ?\n"
            "Les données actuelles seront perdues.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        from app.storage.database import dispose_engine, get_active_db_url
        db_path = Path((get_active_db_url() or "").replace("sqlite:///", ""))
        dispose_engine()
        shutil.copy2(file, db_path)
        QMessageBox.information(
            self, "Restauration réussie",
            "Base restaurée avec succès.\nVeuillez redémarrer l'application."
        )

    def _purge_snapshots(self) -> None:
        days = self._purge_days.value()
        reply = QMessageBox.question(
            self, "Purge des snapshots",
            f"Supprimer définitivement tous les snapshots de plus de {days} jours ?\n"
            "Cette opération est irréversible.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
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
        QMessageBox.information(self, "Purge terminée", f"{deleted} snapshot(s) supprimés.")

    def _reset_database(self) -> None:
        """Vide complètement la base de données après double confirmation."""
        # Première confirmation
        reply1 = QMessageBox.warning(
            self,
            "⚠️  Vider la base de données",
            "Vous êtes sur le point de <b>supprimer toutes les données</b> de la base :\n\n"
            "• Tous les produits et leurs prix\n"
            "• Tout l'historique des snapshots\n"
            "• Toutes les sessions d'analyse\n"
            "• Tous les événements de changement\n\n"
            "Cette opération est <b>irréversible</b>.\n\n"
            "Voulez-vous continuer ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply1 != QMessageBox.Yes:
            return

        # Deuxième confirmation (sécurité supplémentaire)
        reply2 = QMessageBox.critical(
            self,
            "Confirmation finale",
            "⚠️  DERNIÈRE CONFIRMATION\n\n"
            "Toutes les données seront définitivement supprimées.\n"
            "Êtes-vous absolument certain de vouloir continuer ?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply2 != QMessageBox.Yes:
            return

        try:
            from app.storage.database import get_db, dispose_engine, init_db
            from app.storage.models import (
                Base, ChangeEvent, ProductSnapshot, Variant,
                Product, CrawlSession, Brand,
            )
            from sqlalchemy import text

            # Supprimer toutes les données dans le bon ordre (FK)
            with get_db() as db:
                db.query(ChangeEvent).delete()
                db.query(ProductSnapshot).delete()
                db.query(Variant).delete()
                db.query(Product).delete()
                db.query(CrawlSession).delete()
                db.query(Brand).delete()

            log.info("Base de données vidée par l'utilisateur")
            QMessageBox.information(
                self,
                "Base vidée",
                "✓ Toutes les données ont été supprimées.\n\n"
                "La base est maintenant vide et prête pour une nouvelle analyse.",
            )
        except Exception as exc:
            log.error("Erreur reset base", error=str(exc))
            QMessageBox.critical(
                self,
                "Erreur",
                f"Une erreur est survenue lors du reset :\n{exc}",
            )

    def _open_log_dir(self) -> None:
        import subprocess
        import sys
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
            "MARKET":         selected_market,
            "MAX_WORKERS":    self._workers.value(),
            "LOG_LEVEL":      self._log_level.currentText(),
            "PROXY_LIST":     proxies,
            "PROXY_STRATEGY": self._proxy_strategy.currentText(),
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
            f"✓ Paramètres enregistrés (marché : {selected_market.upper()}).\n"
            "Certains changements nécessitent un redémarrage de l'application.",
        )

    def _reset_settings(self) -> None:
        reply = QMessageBox.question(
            self, "Réinitialiser les paramètres",
            "Supprimer settings.json et revenir aux valeurs par défaut ?\n"
            "(Cela ne supprime pas les données de la base.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        p = PROJECT_ROOT / "settings.json"
        if p.exists():
            p.unlink()
        QMessageBox.information(
            self, "Réinitialisé",
            "Paramètres réinitialisés.\nVeuillez redémarrer l'application."
        )
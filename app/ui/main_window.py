"""
MainWindow Phase 2 — Fenêtre principale PySide6 avec navigation complète.

Navigation latérale à 5 sections :
  - Dashboard   : KPIs, résumé dernière session, bouton lancer
  - Marques     : gestion des connecteurs de scraping
  - Résultats   : tableau des produits avec filtres avancés
  - Historique  : sessions passées et rapports
  - Paramètres  : configuration de l'application

Pattern MVP : la fenêtre orchestre les vues, la logique métier est dans les workers.

Changements v2.2 :
  - ExcelExportDialog : sélecteur de mode export (Variantes / Produit principal).
    Le mode "Variantes" est le comportement historique (une ligne par variante).
    Le mode "Produit principal" exporte une ligne par produit avec les tailles,
    couleurs et SKUs fusionnés dans des cellules dédiées.
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QAction, QFont, QIcon, QColor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QRadioButton,
    QScrollArea,
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

_BRAND_COLORS = {
    "spanx":      "#1B3A6B",
    "skims":      "#C8A882",
    "honeylove":  "#C0392B",
    "shapermint": "#27AE60",
    "wacoal":     "#3727AE",
}


# ---------------------------------------------------------------------------
# Dialog de sélection des marques et du mode pour l'export Excel
# ---------------------------------------------------------------------------

class ExcelExportDialog(QDialog):
    """
    Dialog permettant de choisir quelle(s) marque(s) exporter en Excel
    et le mode d'export :
      • Variantes       — une ligne par variante couleur × taille
                          (comportement historique, détail maximal)
      • Produit principal — une ligne par produit, tailles / couleurs / SKUs
                          fusionnés dans des cellules dédiées
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Exporter en Excel")
        self.setFixedWidth(420)
        self.setModal(True)
        self.setStyleSheet("QDialog { background: #F8FAFC; }")

        self._checkboxes: dict[str, QCheckBox] = {}
        self._available_brands: list[str] = []
        self._setup_ui()
        self._load_brands()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        # ── Titre ────────────────────────────────────────────────────────
        title = QLabel("Exporter en Excel")
        title_font = QFont()
        title_font.setPointSize(11)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #1E293B;")
        layout.addWidget(title)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.HLine)
        sep0.setStyleSheet("color: #E2E8F0;")
        layout.addWidget(sep0)

        # ── Section mode d'export ────────────────────────────────────────
        mode_title = QLabel("Mode d'export")
        mode_title.setStyleSheet(
            "color: #475569; font-weight: bold; font-size: 9pt;"
        )
        layout.addWidget(mode_title)

        # Qt QSS : technique "double-ring" sans ::after ni outline.
        # checked   → fond violet, bordure blanche épaisse (5px) + border-radius
        #             → cercle violet visible autour du blanc central
        # unchecked → fond blanc, bordure grise fine
        # Le contour violet extérieur en checked est obtenu en réduisant
        # la taille de l'indicator de 2px (14px au lieu de 16px) et en
        # ajoutant un padding sur le QRadioButton pour compenser.
        _radio_style_checked = """
            QRadioButton {
                font-size: 9pt;
                color: #1E293B;
                spacing: 8px;
            }
            QRadioButton::indicator {
                width: 18px;
                height: 18px;
                border-radius: 9px;
            }
            QRadioButton::indicator:unchecked {
                border: 2px solid #94A3B8;
                background: white;
            }
            QRadioButton::indicator:unchecked:hover {
                border: 2px solid #7C3AED;
                background: #F5F3FF;
            }
            QRadioButton::indicator:checked {
                border: 5px solid #7C3AED;
                background: white;
            }
            QRadioButton:checked {
                color: #7C3AED;
                font-weight: bold;
            }
        """

        self._radio_variants = QRadioButton(
            "Variantes  —  une ligne par variante couleur × taille"
        )
        self._radio_variants.setChecked(True)
        self._radio_variants.setStyleSheet(_radio_style_checked)
        layout.addWidget(self._radio_variants)

        # Description du mode variants
        desc_variants = QLabel(
            "Détail complet. Chaque combinaison couleur / taille occupe\n"
            "une ligne distincte. Idéal pour les analyses granulaires."
        )
        desc_variants.setStyleSheet(
            "color: #64748B; font-size: 8pt; padding-left: 24px;"
        )
        layout.addWidget(desc_variants)

        self._radio_product = QRadioButton(
            "Produit principal  —  une ligne par produit"
        )
        self._radio_product.setStyleSheet(_radio_style_checked)
        layout.addWidget(self._radio_product)

        # Description du mode product
        desc_product = QLabel(
            "Vue synthétique. Les couleurs, tailles et SKUs sont fusionnés\n"
            "dans des cellules dédiées, séparés par \" | \"."
        )
        desc_product.setStyleSheet(
            "color: #64748B; font-size: 8pt; padding-left: 24px;"
        )
        layout.addWidget(desc_product)

        # Grouper les boutons radio pour exclusivité mutuelle
        self._mode_group = QButtonGroup(self)
        self._mode_group.addButton(self._radio_variants)
        self._mode_group.addButton(self._radio_product)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet("color: #E2E8F0;")
        layout.addWidget(sep1)

        # ── Section sélection des marques ────────────────────────────────
        brands_title = QLabel("Marques à exporter")
        brands_title.setStyleSheet(
            "color: #475569; font-weight: bold; font-size: 9pt;"
        )
        layout.addWidget(brands_title)

        brands_sub = QLabel("Un fichier Excel distinct sera créé par marque.")
        brands_sub.setStyleSheet("color: #64748B; font-size: 8pt;")
        layout.addWidget(brands_sub)

        # Checkbox "Toutes les marques"
        self._cb_all = QCheckBox("Toutes les marques")
        self._cb_all.setStyleSheet(
            "QCheckBox { font-weight: bold; color: #1E293B; font-size: 10pt; spacing: 8px; }"
            "QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px; }"
            "QCheckBox::indicator:unchecked { border: 2px solid #94A3B8; background: white; }"
            "QCheckBox::indicator:unchecked:hover { border: 2px solid #7C3AED; background: #F5F3FF; }"
            "QCheckBox::indicator:checked { border: 2px solid #7C3AED; background: #7C3AED; }"
            "QCheckBox::indicator:indeterminate { border: 2px solid #7C3AED; background: #DDD6FE; }"
        )
        self._cb_all.stateChanged.connect(self._on_all_toggled)
        layout.addWidget(self._cb_all)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #E2E8F0;")
        layout.addWidget(sep2)

        # Zone scrollable pour les marques individuelles
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedHeight(160)

        self._brands_container = QWidget()
        self._brands_layout = QVBoxLayout(self._brands_container)
        self._brands_layout.setContentsMargins(4, 4, 4, 4)
        self._brands_layout.setSpacing(6)
        self._brands_layout.addStretch()
        scroll.setWidget(self._brands_container)
        layout.addWidget(scroll)

        # Note d'information dynamique
        self._info_label = QLabel("")
        self._info_label.setStyleSheet(
            "color: #475569; font-size: 8pt; "
            "background: #F1F5F9; border-radius: 4px; padding: 6px 10px;"
        )
        self._info_label.setWordWrap(True)
        layout.addWidget(self._info_label)

        # Boutons OK / Annuler
        btn_box = QDialogButtonBox()
        self._btn_export = QPushButton("📊  Exporter")
        self._btn_export.setEnabled(False)
        self._btn_export.setStyleSheet(
            "QPushButton { background: #7C3AED; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 6px 20px; }"
            "QPushButton:hover { background: #6D28D9; }"
            "QPushButton:disabled { background: #CBD5E1; color: #94A3B8; }"
        )
        btn_cancel = QPushButton("Annuler")
        btn_cancel.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 6px 16px; }"
            "QPushButton:hover { background: #F1F5F9; }"
        )
        btn_box.addButton(btn_cancel, QDialogButtonBox.RejectRole)
        btn_box.addButton(self._btn_export, QDialogButtonBox.AcceptRole)
        btn_cancel.clicked.connect(self.reject)
        self._btn_export.clicked.connect(self.accept)
        layout.addWidget(btn_box)

        # Mise à jour de la note lorsqu'on change de mode
        self._radio_variants.toggled.connect(self._update_info)
        self._radio_product.toggled.connect(self._update_info)

    def _load_brands(self) -> None:
        try:
            from app.connectors.registry import ConnectorRegistry
            self._available_brands = ConnectorRegistry().list_connectors()
        except Exception:
            self._available_brands = ["spanx", "skims", "honeylove", "shapermint", "wacoal"]

        # Vider l'ancien contenu (hors stretch)
        while self._brands_layout.count() > 1:
            item = self._brands_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for slug in self._available_brands:
            color = _BRAND_COLORS.get(slug, "#2C3E50")
            cb = QCheckBox(slug.upper())
            cb.setStyleSheet(
                f"QCheckBox {{ color: {color}; font-weight: bold; font-size: 10pt; spacing: 8px; }}"
                "QCheckBox::indicator { width: 16px; height: 16px; border-radius: 3px; }"
                "QCheckBox::indicator:unchecked { border: 2px solid #94A3B8; background: white; }"
                f"QCheckBox::indicator:unchecked:hover {{ border: 2px solid {color}; background: #F8F8FF; }}"
                f"QCheckBox::indicator:checked {{ border: 2px solid {color}; background: {color}; }}"
            )
            cb.stateChanged.connect(self._on_brand_toggled)
            self._brands_layout.insertWidget(self._brands_layout.count() - 1, cb)
            self._checkboxes[slug] = cb

        self._update_info()

    def _on_all_toggled(self, state: int) -> None:
        checked = state == Qt.Checked
        for cb in self._checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._update_export_button()
        self._update_info()

    def _on_brand_toggled(self) -> None:
        selected = self.get_selected_brands()
        all_checked = len(selected) == len(self._available_brands)
        self._cb_all.blockSignals(True)
        self._cb_all.setChecked(all_checked)
        self._cb_all.blockSignals(False)
        self._update_export_button()
        self._update_info()

    def _update_export_button(self) -> None:
        self._btn_export.setEnabled(len(self.get_selected_brands()) > 0)

    def _update_info(self) -> None:
        selected = self.get_selected_brands()
        mode     = self.get_export_mode()
        mode_label = "produit" if mode == "product" else "variantes"
        n = len(selected)
        if n == 0:
            self._info_label.setText("Sélectionnez au moins une marque.")
        elif n == 1:
            self._info_label.setText(
                f"1 fichier Excel sera créé ({mode_label}) : "
                f"export_…_{selected[0]}.xlsx"
            )
        else:
            self._info_label.setText(
                f"{n} fichiers Excel seront créés ({mode_label}), un par marque :\n"
                + ", ".join(f"{s}.xlsx" for s in selected)
            )

    # ── Accesseurs ────────────────────────────────────────────────────────

    def get_selected_brands(self) -> list[str]:
        return [slug for slug, cb in self._checkboxes.items() if cb.isChecked()]

    def get_export_mode(self) -> str:
        """Retourne "variants" ou "product"."""
        return "product" if self._radio_product.isChecked() else "variants"


# ---------------------------------------------------------------------------
# Worker thread pour le crawl (évite de bloquer l'UI)
# ---------------------------------------------------------------------------

class CrawlWorker(QObject):
    """Exécute le crawl dans un thread séparé."""

    finished           = Signal(list)
    error              = Signal(str)
    log_message        = Signal(str, str)
    _progress_received = Signal(str, int, int)

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

    _log_received      = Signal(str, str)
    _progress_received = Signal(str, int, int)

    def __init__(self) -> None:
        super().__init__()
        self._worker: CrawlWorker | None = None
        self._thread: QThread | None     = None
        self._is_running = False

        self._scheduler = None
        self._init_scheduler()

        self._setup_ui()
        self._setup_event_bus()

        self._log_received.connect(self._append_log)
        self._progress_received.connect(self._update_progress)

        QTimer.singleShot(500, self._refresh_current_view)

        log.info("Interface Phase 2 démarrée", version=settings.APP_VERSION)

    # -------------------------------------------------------------------
    # Scheduler
    # -------------------------------------------------------------------

    def _init_scheduler(self) -> None:
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

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = self._build_sidebar()
        root_layout.addWidget(sidebar)

        content_area = QWidget()
        content_area.setStyleSheet("background: #F1F5F9;")
        content_layout = QVBoxLayout(content_area)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        topbar = self._build_topbar()
        content_layout.addWidget(topbar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget { background: #F1F5F9; }")
        content_layout.addWidget(self._stack)

        progress_widget = self._build_progress_bar()
        content_layout.addWidget(progress_widget)

        root_layout.addWidget(content_area, 1)

        self._status_bar = QStatusBar()
        self._status_bar.setStyleSheet(
            "QStatusBar { background: #0F172A; color: #64748B; font-size: 8pt; }"
        )
        self.setStatusBar(self._status_bar)
        self._status_bar.showMessage(
            f"Prêt  |  BD : {settings.DATABASE_URL.replace('sqlite:///', '')}"
        )

        self._load_views()

        self.setStyleSheet(
            "QMainWindow { background: #0F172A; }"
            "QWidget { font-family: 'Segoe UI', Arial, sans-serif; }"
        )

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setFixedWidth(220)
        sidebar.setStyleSheet("background: #0F172A;")
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 16, 12, 16)
        sidebar_layout.setSpacing(4)

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

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #1E293B;")
        sidebar_layout.addWidget(sep)
        sidebar_layout.addSpacing(8)

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

        self._session_status_label = QLabel("")
        self._session_status_label.setStyleSheet(
            "color: #2563EB; font-size: 8pt; padding: 4px;"
        )
        self._session_status_label.setWordWrap(True)
        self._session_status_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(self._session_status_label)

        version_label = QLabel(f"v{settings.APP_VERSION}")
        version_label.setStyleSheet("color: #334155; font-size: 8pt;")
        version_label.setAlignment(Qt.AlignCenter)
        sidebar_layout.addWidget(version_label)

        return sidebar

    def _build_topbar(self) -> QWidget:
        topbar = QWidget()
        topbar.setFixedHeight(52)
        topbar.setStyleSheet(
            "background: white; border-bottom: 1px solid #E2E8F0;"
        )
        layout = QHBoxLayout(topbar)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(8)

        self._view_title = QLabel("Dashboard")
        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        self._view_title.setFont(title_font)
        self._view_title.setStyleSheet("color: #1E293B;")
        layout.addWidget(self._view_title)
        layout.addStretch()

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

        self._btn_export = QPushButton("⬇  CSV")
        self._btn_export.setMinimumHeight(34)
        self._btn_export.setStyleSheet(
            "QPushButton { background: #16A34A; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 0 12px; font-size: 9pt; }"
            "QPushButton:hover { background: #15803D; }"
        )
        self._btn_export.clicked.connect(self._on_export_csv_clicked)
        layout.addWidget(self._btn_export)

        self._btn_excel = QPushButton("📊  Excel")
        self._btn_excel.setMinimumHeight(34)
        self._btn_excel.setToolTip(
            "Exporter en Excel — choisir la/les marque(s) et le mode d'export"
        )
        self._btn_excel.setStyleSheet(
            "QPushButton { background: #7C3AED; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 0 12px; font-size: 9pt; }"
            "QPushButton:hover { background: #6D28D9; }"
        )
        self._btn_excel.clicked.connect(self._on_export_excel_clicked)
        layout.addWidget(self._btn_excel)

        return topbar

    def _build_progress_bar(self) -> QWidget:
        widget = QWidget()
        widget.setFixedHeight(0)
        widget.setStyleSheet("background: white; border-top: 1px solid #E2E8F0;")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(16, 4, 16, 4)

        self._progress_label = QLabel("Prêt")
        self._progress_label.setStyleSheet(
            "color: #475569; font-size: 8pt; min-width: 240px;"
        )
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
        idx = self._VIEW_MAP.get(view_name, 0)
        self._stack.setCurrentIndex(idx)
        self._view_title.setText(view_name)
        for i, btn in enumerate(self._nav_buttons):
            btn.setChecked(i == idx)
        self._refresh_current_view()

    def _refresh_current_view(self) -> None:
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
        event_bus.start()
        event_bus.subscribe("crawl.task.progress",    self._on_progress_event)
        event_bus.subscribe("product.saved",           self._on_product_saved)
        event_bus.subscribe("crawl.session.completed", self._on_session_completed)
        event_bus.subscribe("crawl.session.started",   self._on_session_started)

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
        if self._is_running:
            return

        self._is_running = True
        self._btn_run.setEnabled(False)
        self._btn_cancel.setEnabled(True)

        self._progress_widget.setFixedHeight(30)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setValue(0)
        self._progress_label.setText(f"Initialisation — {', '.join(brand_slugs)}")
        self._session_status_label.setText("⏳ Session en cours…")
        self._status_bar.showMessage(f"Session en cours : {', '.join(brand_slugs)}")

        if hasattr(self, "_dashboard_view"):
            self._dashboard_view.set_running(True)

        self._append_log("INFO", f"=== Démarrage session — {', '.join(brand_slugs)} ===")
        self._append_log("INFO", f"Heure : {datetime.now().strftime('%H:%M:%S')}")

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
        """
        Ouvre le dialog de sélection (marques + mode), puis exporte
        un fichier Excel distinct pour chaque marque choisie.
        """
        dialog = ExcelExportDialog(parent=self)
        if dialog.exec() != QDialog.Accepted:
            return

        selected = dialog.get_selected_brands()
        mode     = dialog.get_export_mode()

        if not selected:
            return

        try:
            from app.exports.excel_exporter import ExcelExporter
            exporter = ExcelExporter()
            created_paths: list[Path] = []

            for slug in selected:
                path = exporter.export_brand(slug, mode=mode)
                created_paths.append(path)
                self._append_log(
                    "INFO",
                    f"Excel créé ({mode}) : {path.name}",
                )

            if len(created_paths) == 1:
                msg = f"Fichier créé :\n{created_paths[0]}"
            else:
                names = "\n".join(p.name for p in created_paths)
                msg = (
                    f"{len(created_paths)} fichiers créés dans :\n"
                    f"{created_paths[0].parent}\n\n{names}"
                )

            QMessageBox.information(self, "Export Excel réussi", msg)

        except Exception as exc:
            self._append_log("ERROR", f"Erreur export Excel : {exc}")
            QMessageBox.critical(self, "Erreur export Excel", str(exc))

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

        QTimer.singleShot(3000, lambda: self._progress_widget.setFixedHeight(0))

        if hasattr(self, "_dashboard_view"):
            self._dashboard_view.set_running(False)
            self._dashboard_view.refresh()

        if self._thread:
            self._thread.quit()
            self._thread.wait()

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
        if hasattr(self, "_history_view"):
            QTimer.singleShot(500, self._history_view.refresh)

    # -------------------------------------------------------------------
    # Mise à jour UI (thread principal uniquement)
    # -------------------------------------------------------------------

    def _append_log(self, level: str, message: str) -> None:
        pass

    def _update_progress(self, label: str, current: int, total: int) -> None:
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
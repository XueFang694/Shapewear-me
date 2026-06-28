"""
Vue Dashboard — KPIs, résumé dernière session, bouton lancer.

Affiche :
  - 4 KPI cards (produits suivis, nouveaux, changements de prix, suppressions)
  - Tableau des marques actives avec statut et compteurs
  - Résumé de la dernière session
  - Graphique d'évolution simplifié (via QLabel/HTML)
"""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.core.logger import get_logger

log = get_logger(__name__)

_BRAND_COLORS = {
    "spanx":      "#1B3A6B",
    "skims":      "#C8A882",
    "honeylove":  "#C0392B",
    "shapermint": "#27AE60",
}


class KpiCard(QFrame):
    """Widget KPI : valeur + libellé + couleur."""

    def __init__(self, label: str, value: str = "—", color: str = "#2563eb") -> None:
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background: white; border-radius: 8px; "
            f"border: 1px solid #E2E8F0; }}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMinimumHeight(100)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        self._value_label = QLabel(value)
        font = QFont()
        font.setPointSize(26)
        font.setBold(True)
        self._value_label.setFont(font)
        self._value_label.setStyleSheet(f"color: {color}; border: none;")
        layout.addWidget(self._value_label)

        lbl = QLabel(label)
        lbl.setStyleSheet("color: #64748B; font-size: 9pt; border: none;")
        layout.addWidget(lbl)

    def set_value(self, value: str) -> None:
        self._value_label.setText(value)


class DashboardView(QWidget):
    """Vue d'accueil avec KPIs et résumé de session."""

    # Signal émis quand l'utilisateur clique "Lancer"
    run_requested = Signal()
    export_csv_requested = Signal()
    export_excel_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._setup_ui()

    # ── Construction ─────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(16)

        # Titre
        title = QLabel("Tableau de Bord")
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #1E293B;")
        root.addWidget(title)

        subtitle = QLabel("Vue d'ensemble de votre veille concurrentielle shapewear")
        subtitle.setStyleSheet("color: #64748B; font-size: 10pt;")
        root.addWidget(subtitle)

        # ── Boutons d'action ────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._btn_run = QPushButton("▶  Lancer l'analyse")
        self._btn_run.setMinimumHeight(36)
        self._btn_run.setStyleSheet(
            "QPushButton { background:#2563EB; color:white; border-radius:6px; "
            "font-weight:bold; padding:0 20px; }"
            "QPushButton:hover { background:#1D4ED8; }"
            "QPushButton:disabled { background:#94A3B8; }"
        )
        self._btn_run.clicked.connect(self.run_requested)
        btn_row.addWidget(self._btn_run)

        self._btn_csv = QPushButton("⬇  Exporter CSV")
        self._btn_csv.setMinimumHeight(36)
        self._btn_csv.setStyleSheet(
            "QPushButton { background:#16A34A; color:white; border-radius:6px; "
            "font-weight:bold; padding:0 16px; }"
            "QPushButton:hover { background:#15803D; }"
        )
        self._btn_csv.clicked.connect(self.export_csv_requested)
        btn_row.addWidget(self._btn_csv)

        self._btn_excel = QPushButton("📊  Exporter Excel")
        self._btn_excel.setMinimumHeight(36)
        self._btn_excel.setStyleSheet(
            "QPushButton { background:#7C3AED; color:white; border-radius:6px; "
            "font-weight:bold; padding:0 16px; }"
            "QPushButton:hover { background:#6D28D9; }"
        )
        self._btn_excel.clicked.connect(self.export_excel_requested)
        btn_row.addWidget(self._btn_excel)

        btn_row.addStretch()
        root.addLayout(btn_row)

        # ── KPI Cards ────────────────────────────────────────────────────────
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(12)
        self._kpi_active   = KpiCard("Produits actifs",      "—", "#2563EB")
        self._kpi_new      = KpiCard("Nouveaux (session)",   "—", "#16A34A")
        self._kpi_changes  = KpiCard("Changements de prix",  "—", "#D97706")
        self._kpi_removed  = KpiCard("Suppressions",         "—", "#DC2626")
        self._kpi_promo    = KpiCard("En promotion",         "—", "#7C3AED")
        self._kpi_bs       = KpiCard("Best Sellers",         "—", "#C8A882")

        kpi_grid.addWidget(self._kpi_active,  0, 0)
        kpi_grid.addWidget(self._kpi_new,     0, 1)
        kpi_grid.addWidget(self._kpi_changes, 0, 2)
        kpi_grid.addWidget(self._kpi_removed, 1, 0)
        kpi_grid.addWidget(self._kpi_promo,   1, 1)
        kpi_grid.addWidget(self._kpi_bs,      1, 2)
        root.addLayout(kpi_grid)

        # ── Tableau des marques ──────────────────────────────────────────────
        brands_group = QGroupBox("Marques actives")
        brands_group.setStyleSheet(
            "QGroupBox { font-weight: bold; border: 1px solid #E2E8F0; "
            "border-radius: 8px; margin-top: 8px; padding-top: 16px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 12px; }"
        )
        brands_layout = QVBoxLayout(brands_group)

        self._brands_table = QTableWidget(0, 6)
        self._brands_table.setHorizontalHeaderLabels([
            "Marque", "Produits actifs", "Best Sellers",
            "En promo", "Dernière session", "Statut"
        ])
        self._brands_table.horizontalHeader().setStretchLastSection(True)
        self._brands_table.setAlternatingRowColors(True)
        self._brands_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._brands_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._brands_table.setMaximumHeight(160)
        self._brands_table.setStyleSheet(
            "QTableWidget { border: none; font-size: 9pt; }"
            "QHeaderView::section { background: #F8FAFC; font-weight: bold; "
            "border-bottom: 2px solid #E2E8F0; padding: 6px; }"
        )
        brands_layout.addWidget(self._brands_table)
        root.addWidget(brands_group)

        # ── Résumé dernière session ──────────────────────────────────────────
        session_group = QGroupBox("Dernière session d'analyse")
        session_group.setStyleSheet(brands_group.styleSheet())
        session_layout = QVBoxLayout(session_group)

        self._session_label = QLabel("Aucune session enregistrée.")
        self._session_label.setStyleSheet("color: #64748B; padding: 8px;")
        self._session_label.setWordWrap(True)
        session_layout.addWidget(self._session_label)
        root.addWidget(session_group)

        root.addStretch()

    # ── Mise à jour des données ───────────────────────────────────────────

    def refresh(self) -> None:
        """Recharge toutes les données depuis la base."""
        try:
            self._load_data()
        except Exception as exc:
            log.error("Erreur rafraîchissement dashboard", error=str(exc))

    def _load_data(self) -> None:
        from app.storage.database import get_db
        from app.storage.models import Brand, ChangeEvent, CrawlSession, Product
        from app.storage.repository import SnapshotRepository

        with get_db() as db:
            brands   = db.query(Brand).filter_by(active=True).all()
            products = db.query(Product).all()
            active_p = [p for p in products if p.is_active]

            snap_repo = SnapshotRepository(db)
            snapshots = {p.id: snap_repo.get_latest(p.id) for p in products}

            on_sale  = [p for p in active_p if snapshots.get(p.id) and snapshots[p.id].on_sale]
            bs_prods = [p for p in active_p if p.is_best_seller]

            # Dernière session
            last_session = (
                db.query(CrawlSession)
                .order_by(CrawlSession.started_at.desc())
                .first()
            )

            # Événements de la dernière session
            new_evts   = []
            price_evts = []
            if last_session:
                new_evts = db.query(ChangeEvent).filter_by(
                    session_id=last_session.id, event_type="product.new"
                ).count()
                price_evts = db.query(ChangeEvent).filter_by(
                    session_id=last_session.id, event_type="price.changed"
                ).count()
                removed_evts = db.query(ChangeEvent).filter_by(
                    session_id=last_session.id, event_type="product.removed"
                ).count()
            else:
                removed_evts = 0

            removed_p = [p for p in products if not p.is_active]

        # Mettre à jour les KPIs
        self._kpi_active.set_value(str(len(active_p)))
        self._kpi_new.set_value(str(new_evts))
        self._kpi_changes.set_value(str(price_evts))
        self._kpi_removed.set_value(str(len(removed_p)))
        self._kpi_promo.set_value(str(len(on_sale)))
        self._kpi_bs.set_value(str(len(bs_prods)))

        # Tableau des marques
        self._brands_table.setRowCount(len(brands))
        for row, brand in enumerate(brands):
            bps      = [p for p in products if p.brand_id == brand.id]
            active_c = len([p for p in bps if p.is_active])
            bs_c     = len([p for p in bps if p.is_best_seller])
            promo_c  = len([p for p in bps if snapshots.get(p.id) and snapshots[p.id].on_sale])
            color    = _BRAND_COLORS.get(brand.slug, "#2C3E50")

            name_item = QTableWidgetItem(brand.name)
            name_item.setForeground(Qt.GlobalColor.white)  # texte blanc
            # Fond coloré simulé via stylesheet sur l'item
            self._brands_table.setItem(row, 0, name_item)
            self._brands_table.setItem(row, 1, QTableWidgetItem(str(active_c)))
            self._brands_table.setItem(row, 2, QTableWidgetItem(str(bs_c)))
            self._brands_table.setItem(row, 3, QTableWidgetItem(str(promo_c)))

            # Dernière session
            last_s = last_session
            if last_s and last_s.started_at:
                date_str = last_s.started_at.strftime("%d/%m/%Y %H:%M")
            else:
                date_str = "—"
            self._brands_table.setItem(row, 4, QTableWidgetItem(date_str))
            self._brands_table.setItem(row, 5, QTableWidgetItem("Actif ✓"))

        self._brands_table.resizeColumnsToContents()

        # Résumé dernière session
        if last_session:
            duration = ""
            if last_session.started_at and last_session.ended_at:
                secs = int((last_session.ended_at - last_session.started_at).total_seconds())
                duration = f" en {secs // 60}m {secs % 60}s"
            text = (
                f"Dernière analyse : {last_session.started_at.strftime('%d/%m/%Y à %H:%M')}{duration}\n"
                f"Produits analysés : {last_session.products_found}  |  "
                f"Nouveaux : {last_session.products_new}  |  "
                f"Modifiés : {last_session.products_changed}  |  "
                f"Supprimés : {last_session.products_removed}  |  "
                f"Statut : {last_session.status}"
            )
        else:
            text = "Aucune session enregistrée. Cliquez « Lancer l'analyse » pour démarrer."
        self._session_label.setText(text)

    def set_running(self, running: bool) -> None:
        """Active/désactive le bouton pendant un crawl."""
        self._btn_run.setEnabled(not running)
        self._btn_csv.setEnabled(not running)
        self._btn_excel.setEnabled(not running)
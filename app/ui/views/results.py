"""
Vue Résultats — Tableau des produits avec filtres avancés et détail produit.

Fonctionnalités Phase 2 :
  - Filtres : marque, famille, fourchette de prix, statut (actif/promo/nouveau/supprimé)
  - Tableau paginé : Produit | Marque | Famille | Prix | Remise | Dispo | BS | MAJ
  - Panneau latéral de détail : historique des prix (tableau + sparkline ASCII)
  - Recherche textuelle en temps réel
  - Export CSV/Excel depuis les résultats filtrés
  - Tri par colonne
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from PySide6.QtCore import (
    Qt, QSortFilterProxyModel, QThread, Signal, QObject,
    QAbstractTableModel, QModelIndex,
)
from PySide6.QtGui import QFont, QColor, QBrush
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTableView,
    QTextEdit,
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

_COLUMNS = [
    ("Produit",       "name",             340),
    ("Marque",        "brand",            90),
    ("Famille",       "family",           130),
    ("Prix",          "price",            75),
    ("Remise",        "discount_pct",     70),
    ("Dispo",         "availability",     65),
    ("BS",            "is_best_seller",   40),
    ("En promo",      "on_sale",          70),
    ("Dernière MAJ",  "last_seen",        110),
]


# ---------------------------------------------------------------------------
# Modèle de données Qt
# ---------------------------------------------------------------------------

class ProductTableModel(QAbstractTableModel):
    """Modèle Qt pour le tableau des produits."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._data: list[dict] = []
        self._headers = [col[0] for col in _COLUMNS]
        self._keys    = [col[1] for col in _COLUMNS]

    def load(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._data = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._data)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._headers)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self._headers[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._data):
            return None
        row = self._data[index.row()]
        key = self._keys[index.column()]
        val = row.get(key)

        if role == Qt.DisplayRole:
            if key == "price":
                return f"${val:.2f}" if val is not None else "—"
            if key == "discount_pct":
                return f"-{val:.0f}%" if val else ""
            if key == "is_best_seller":
                return "⭐" if val else ""
            if key == "on_sale":
                return "🏷️" if val else ""
            if key == "availability":
                return "✓" if val == "in_stock" else ("✗" if val == "out_of_stock" else "?")
            if key == "last_seen" and val:
                try:
                    if isinstance(val, str):
                        val = datetime.fromisoformat(val)
                    return val.strftime("%d/%m/%Y")
                except Exception:
                    return str(val)
            if key == "brand":
                return (val or "").upper()
            return str(val) if val is not None else ""

        if role == Qt.ForegroundRole:
            if key == "brand":
                color = _BRAND_COLORS.get(row.get("brand", ""), "#2C3E50")
                return QBrush(QColor(color))
            if key == "discount_pct" and val:
                return QBrush(QColor("#16A34A"))
            if key == "availability":
                if val == "in_stock":
                    return QBrush(QColor("#16A34A"))
                if val == "out_of_stock":
                    return QBrush(QColor("#DC2626"))

        if role == Qt.BackgroundRole:
            if not row.get("is_active", True):
                return QBrush(QColor("#FEF2F2"))
            if row.get("is_best_seller"):
                return QBrush(QColor("#FFFBEB"))
            if index.row() % 2 == 0:
                return QBrush(QColor("#F8FAFC"))

        if role == Qt.UserRole:
            return row  # Données complètes pour le panneau détail

        if role == Qt.TextAlignmentRole:
            if key in ("price", "discount_pct", "is_best_seller", "on_sale", "availability"):
                return Qt.AlignCenter

        return None

    def get_row(self, index: int) -> dict | None:
        if 0 <= index < len(self._data):
            return self._data[index]
        return None


# ---------------------------------------------------------------------------
# Panneau de détail produit
# ---------------------------------------------------------------------------

class ProductDetailPanel(QScrollArea):
    """Panneau latéral affichant le détail d'un produit sélectionné."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(300)
        self.setMaximumWidth(420)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(8)
        self.setWidget(container)

        self._placeholder = QLabel("Cliquez sur un produit\npour voir son détail.")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setStyleSheet("color: #94A3B8; font-size: 11pt;")
        self._layout.addWidget(self._placeholder)
        self._layout.addStretch()

        self._widgets: list[QWidget] = []

    def show_product(self, row: dict) -> None:
        """Affiche le détail d'un produit."""
        # Nettoyer le panneau
        for w in self._widgets:
            self._layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()
        self._placeholder.hide()

        brand   = row.get("brand", "").upper()
        color   = _BRAND_COLORS.get(row.get("brand", ""), "#2C3E50")
        name    = row.get("name", "—")
        product_id = row.get("product_id")

        # En-tête nom + marque
        title = QLabel(name)
        title_font = QFont()
        title_font.setBold(True)
        title_font.setPointSize(11)
        title.setFont(title_font)
        title.setWordWrap(True)
        title.setStyleSheet(f"color: #1E293B;")
        self._add(title)

        brand_lbl = QLabel(brand)
        brand_lbl.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 9pt;")
        self._add(brand_lbl)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #E2E8F0;")
        self._add(sep)

        # Informations clés
        price         = row.get("price")
        original      = row.get("original_price")
        discount      = row.get("discount_pct")
        availability  = row.get("availability", "unknown")
        family        = row.get("family", "—")
        subfamily     = row.get("subfamily", "")
        compression   = row.get("compression_level", "")
        zones_raw     = row.get("target_zones", "")
        is_bs         = row.get("is_best_seller", False)
        on_sale       = row.get("on_sale", False)

        info_lines = [
            ("Famille",       f"{family}{(' / ' + subfamily) if subfamily else ''}"),
            ("Prix",          f"${price:.2f}" if price else "—"),
        ]
        if on_sale and original:
            info_lines.append(("Prix original", f"${original:.2f}"))
            info_lines.append(("Remise",        f"-{discount:.0f}%" if discount else "—"))
        info_lines.append(("Disponibilité",
            "✓ En stock" if availability == "in_stock"
            else "✗ Rupture" if availability == "out_of_stock" else "?"))
        if compression:
            info_lines.append(("Compression", compression))
        if is_bs:
            info_lines.append(("Statut", "⭐ Best Seller"))

        # Zones corporelles
        try:
            zones = json.loads(zones_raw) if zones_raw and zones_raw.startswith("[") else []
            if zones:
                info_lines.append(("Zones ciblées", ", ".join(zones)))
        except Exception:
            pass

        for label, value in info_lines:
            row_w = QWidget()
            row_l = QHBoxLayout(row_w)
            row_l.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"{label} :")
            lbl.setStyleSheet("color: #64748B; font-size: 8pt; min-width: 100px;")
            val = QLabel(value)
            val.setStyleSheet("color: #1E293B; font-size: 9pt;")
            val.setWordWrap(True)
            row_l.addWidget(lbl)
            row_l.addWidget(val, 1)
            self._add(row_w)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #E2E8F0;")
        self._add(sep2)

        # Matériaux
        mat_main = row.get("material_main")
        mat_lin  = row.get("material_lining")
        if mat_main:
            mat_title = QLabel("Composition")
            mat_title.setStyleSheet("font-weight: bold; font-size: 9pt; color: #475569;")
            self._add(mat_title)
            mat_lbl = QLabel(mat_main)
            mat_lbl.setWordWrap(True)
            mat_lbl.setStyleSheet("color: #1E293B; font-size: 8pt;")
            self._add(mat_lbl)
            if mat_lin:
                lin_lbl = QLabel(f"Doublure : {mat_lin}")
                lin_lbl.setWordWrap(True)
                lin_lbl.setStyleSheet("color: #64748B; font-size: 8pt;")
                self._add(lin_lbl)

        # Historique des prix depuis la DB
        if product_id:
            self._load_price_history(product_id)

        # URL
        url = row.get("url", "")
        if url:
            url_btn = QPushButton("🔗 Voir sur le site")
            url_btn.setStyleSheet(
                "QPushButton { border: 1px solid #CBD5E1; border-radius: 4px; "
                "padding: 4px 8px; font-size: 8pt; color: #2563EB; }"
                "QPushButton:hover { background: #EFF6FF; }"
            )
            url_btn.clicked.connect(lambda: self._open_url(url))
            self._add(url_btn)

        self._layout.addStretch()

    def _load_price_history(self, product_id: int) -> None:
        """Charge et affiche l'historique des prix depuis la DB."""
        try:
            from app.storage.database import get_db
            from app.storage.repository import SnapshotRepository
            with get_db() as db:
                snapshots = SnapshotRepository(db).get_price_history(product_id, days=90)

            if not snapshots:
                return

            sep = QFrame()
            sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet("color: #E2E8F0;")
            self._add(sep)

            hist_title = QLabel(f"Historique prix (90 jours — {len(snapshots)} points)")
            hist_title.setStyleSheet("font-weight: bold; font-size: 9pt; color: #475569;")
            self._add(hist_title)

            # Tableau des 10 derniers snapshots
            recent = snapshots[-10:]
            hist_lines = []
            for snap in reversed(recent):
                date_str = snap.crawled_at.strftime("%d/%m") if snap.crawled_at else "—"
                price_str = f"${snap.price:.2f}" if snap.price else "—"
                sale_str = " 🏷️" if snap.on_sale else ""
                hist_lines.append(f"{date_str}  {price_str}{sale_str}")

            hist_text = QTextEdit()
            hist_text.setReadOnly(True)
            hist_text.setPlainText("\n".join(hist_lines))
            hist_text.setMaximumHeight(160)
            hist_text.setFont(QFont("Courier New", 8))
            hist_text.setStyleSheet(
                "QTextEdit { background: #F8FAFC; border: 1px solid #E2E8F0; "
                "border-radius: 4px; padding: 4px; }"
            )
            self._add(hist_text)

            # Mini sparkline ASCII
            prices = [s.price for s in snapshots if s.price is not None]
            if len(prices) >= 2:
                spark = self._build_sparkline(prices)
                spark_lbl = QLabel(spark)
                spark_lbl.setFont(QFont("Courier New", 8))
                spark_lbl.setStyleSheet("color: #2563EB;")
                self._add(spark_lbl)

                # Stats prix
                p_min = min(prices)
                p_max = max(prices)
                p_avg = sum(prices) / len(prices)
                stats_lbl = QLabel(
                    f"Min: ${p_min:.2f}  |  Moy: ${p_avg:.2f}  |  Max: ${p_max:.2f}"
                )
                stats_lbl.setStyleSheet("color: #64748B; font-size: 8pt;")
                self._add(stats_lbl)

        except Exception as exc:
            log.warning("Impossible de charger l'historique prix", error=str(exc))

    @staticmethod
    def _build_sparkline(prices: list[float], width: int = 20) -> str:
        """Génère un mini sparkline en caractères Unicode."""
        if len(prices) < 2:
            return ""
        blocks = " ▁▂▃▄▅▆▇█"
        mn, mx = min(prices), max(prices)
        rang = mx - mn or 1
        # Sous-échantillonner si trop de points
        step = max(1, len(prices) // width)
        sampled = prices[::step][:width]
        chars = []
        for p in sampled:
            idx = int((p - mn) / rang * (len(blocks) - 1))
            chars.append(blocks[idx])
        return "".join(chars)

    @staticmethod
    def _open_url(url: str) -> None:
        """Ouvre l'URL dans le navigateur."""
        import webbrowser
        webbrowser.open(url)

    def _add(self, widget: QWidget) -> None:
        pos = max(0, self._layout.count() - 1)  # Avant le stretch
        self._layout.insertWidget(pos, widget)
        self._widgets.append(widget)

    def clear(self) -> None:
        for w in self._widgets:
            self._layout.removeWidget(w)
            w.deleteLater()
        self._widgets.clear()
        self._placeholder.show()


# ---------------------------------------------------------------------------
# Worker pour le chargement async des données
# ---------------------------------------------------------------------------

class DataLoaderWorker(QObject):
    """Charge les données depuis la DB dans un thread séparé."""
    finished = Signal(list)
    error    = Signal(str)

    def __init__(self, filters: dict) -> None:
        super().__init__()
        self._filters = filters

    def run(self) -> None:
        try:
            rows = self._load(self._filters)
            self.finished.emit(rows)
        except Exception as exc:
            self.error.emit(str(exc))

    def _load(self, filters: dict) -> list[dict]:
        from app.storage.database import get_db
        from app.storage.models import Product
        from app.storage.repository import BrandRepository, SnapshotRepository

        rows = []
        with get_db() as db:
            brands = BrandRepository(db).list_active()
            brand_map = {b.id: b for b in brands}

            query = db.query(Product)

            # Filtre marque
            if filters.get("brand_slug"):
                brand = next((b for b in brands if b.slug == filters["brand_slug"]), None)
                if brand:
                    query = query.filter(Product.brand_id == brand.id)

            # Filtre famille
            if filters.get("family"):
                query = query.filter(Product.family == filters["family"])

            # Filtre statut
            status = filters.get("status", "all")
            if status == "active":
                query = query.filter(Product.is_active == True)
            elif status == "removed":
                query = query.filter(Product.is_active == False)
            elif status == "best_seller":
                query = query.filter(Product.is_best_seller == True)

            products = query.order_by(Product.last_seen.desc()).all()
            snap_repo = SnapshotRepository(db)

            for p in products:
                snap  = snap_repo.get_latest(p.id)
                brand = brand_map.get(p.brand_id)

                price    = snap.price if snap else None
                orig     = snap.original_price if snap else None
                disc     = snap.discount_pct if snap else None
                on_sale  = snap.on_sale if snap else False
                avail    = snap.availability if snap else "unknown"

                # Filtre prix
                if filters.get("price_min") and price and price < filters["price_min"]:
                    continue
                if filters.get("price_max") and price and price > filters["price_max"]:
                    continue

                # Filtre promo
                if filters.get("on_sale_only") and not on_sale:
                    continue

                # Filtre recherche textuelle
                search = (filters.get("search") or "").lower().strip()
                if search and search not in (p.name or "").lower():
                    continue

                rows.append({
                    "product_id":    p.id,
                    "name":          p.name,
                    "brand":         brand.slug if brand else "?",
                    "family":        p.family or "",
                    "subfamily":     p.subfamily or "",
                    "compression_level": p.compression_level or "",
                    "target_zones":  p.target_zones or "",
                    "price":         price,
                    "original_price": orig,
                    "discount_pct":  disc,
                    "on_sale":       on_sale,
                    "availability":  avail,
                    "is_best_seller": p.is_best_seller,
                    "is_active":     p.is_active,
                    "last_seen":     p.last_seen,
                    "first_seen":    p.first_seen,
                    "removed_at":    p.removed_at,
                    "url":           p.url,
                    "material_main":   p.material_main or "",
                    "material_lining": p.material_lining or "",
                    "material_composition_json": p.material_composition_json or "",
                    "rating":        p.rating,
                    "review_count":  p.review_count,
                    "category_raw":  p.category_raw or "",
                })
        return rows


# ---------------------------------------------------------------------------
# Vue principale
# ---------------------------------------------------------------------------

class ResultsView(QWidget):
    """Vue Résultats avec tableau filtrable et détail produit."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._all_rows:    list[dict]  = []
        self._load_thread: QThread | None = None
        self._families:    list[str]   = []
        self._setup_ui()

    # ── Construction ─────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── En-tête ────────────────────────────────────────────────────────
        header_w = QWidget()
        header_w.setStyleSheet("background: #F8FAFC; border-bottom: 1px solid #E2E8F0;")
        header_l = QVBoxLayout(header_w)
        header_l.setContentsMargins(20, 12, 20, 12)
        header_l.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("Résultats — Catalogue Produits")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setStyleSheet("color: #1E293B;")
        title_row.addWidget(title)
        title_row.addStretch()
        self._count_label = QLabel("— produits")
        self._count_label.setStyleSheet("color: #64748B; font-size: 10pt;")
        title_row.addWidget(self._count_label)
        header_l.addLayout(title_row)

        # ── Filtres ────────────────────────────────────────────────────────
        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        # Recherche
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("🔍  Rechercher un produit…")
        self._search_input.setMinimumWidth(220)
        self._search_input.setStyleSheet(
            "QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 5px 10px; }"
            "QLineEdit:focus { border-color: #2563EB; }"
        )
        self._search_input.textChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._search_input)

        # Marque
        self._brand_combo = QComboBox()
        self._brand_combo.addItem("Toutes les marques", "")
        for slug in ["spanx", "skims", "honeylove", "shapermint"]:
            self._brand_combo.addItem(slug.upper(), slug)
        self._brand_combo.setStyleSheet(
            "QComboBox { border: 1px solid #CBD5E1; border-radius: 6px; padding: 5px 10px; }"
        )
        self._brand_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._brand_combo)

        # Famille
        self._family_combo = QComboBox()
        self._family_combo.addItem("Toutes les familles", "")
        self._family_combo.setMinimumWidth(160)
        self._family_combo.setStyleSheet(self._brand_combo.styleSheet())
        self._family_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._family_combo)

        # Statut
        self._status_combo = QComboBox()
        self._status_combo.addItems(["Tous", "Actifs", "En promo", "Best Sellers", "Supprimés"])
        self._status_combo.setStyleSheet(self._brand_combo.styleSheet())
        self._status_combo.currentIndexChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._status_combo)

        filter_row.addStretch()

        # Boutons action
        self._btn_refresh = QPushButton("↻  Actualiser")
        self._btn_refresh.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; "
            "padding: 5px 12px; }"
            "QPushButton:hover { background: #F1F5F9; }"
        )
        self._btn_refresh.clicked.connect(self.refresh)
        filter_row.addWidget(self._btn_refresh)

        self._btn_export_csv = QPushButton("⬇  CSV")
        self._btn_export_csv.setStyleSheet(
            "QPushButton { background: #16A34A; color: white; border-radius: 6px; "
            "padding: 5px 12px; font-weight: bold; }"
            "QPushButton:hover { background: #15803D; }"
        )
        self._btn_export_csv.clicked.connect(self._export_csv)
        filter_row.addWidget(self._btn_export_csv)

        self._btn_export_excel = QPushButton("📊  Excel")
        self._btn_export_excel.setStyleSheet(
            "QPushButton { background: #7C3AED; color: white; border-radius: 6px; "
            "padding: 5px 12px; font-weight: bold; }"
            "QPushButton:hover { background: #6D28D9; }"
        )
        self._btn_export_excel.clicked.connect(self._export_excel)
        filter_row.addWidget(self._btn_export_excel)

        header_l.addLayout(filter_row)
        root.addWidget(header_w)

        # ── Splitter principal : tableau | détail ──────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #E2E8F0; }")

        # Tableau
        table_container = QWidget()
        table_layout    = QVBoxLayout(table_container)
        table_layout.setContentsMargins(0, 0, 0, 0)

        self._model = ProductTableModel()
        self._table = QTableView()
        self._table.setModel(self._model)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QTableView.SelectRows)
        self._table.setSelectionMode(QTableView.SingleSelection)
        self._table.setEditTriggers(QTableView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(
            "QTableView { border: none; font-size: 9pt; }"
            "QTableView::item { padding: 4px 8px; border-bottom: 1px solid #F1F5F9; }"
            "QTableView::item:selected { background: #EFF6FF; color: #1E293B; }"
            "QHeaderView::section { background: #F8FAFC; font-weight: bold; "
            "border-bottom: 2px solid #E2E8F0; padding: 6px 8px; font-size: 8pt; }"
        )

        # Largeurs de colonnes
        for i, (_, _, width) in enumerate(_COLUMNS):
            self._table.setColumnWidth(i, width)

        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

        self._table.selectionModel().currentRowChanged.connect(self._on_row_selected)
        table_layout.addWidget(self._table)

        # Label chargement
        self._loading_label = QLabel("Chargement…")
        self._loading_label.setAlignment(Qt.AlignCenter)
        self._loading_label.setStyleSheet("color: #94A3B8; font-size: 11pt; padding: 20px;")
        self._loading_label.hide()
        table_layout.addWidget(self._loading_label)

        splitter.addWidget(table_container)

        # Panneau détail
        self._detail_panel = ProductDetailPanel()
        splitter.addWidget(self._detail_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

    # ── Données ──────────────────────────────────────────────────────────

    def refresh(self) -> None:
        """Recharge les données depuis la DB avec les filtres courants."""
        self._loading_label.show()
        self._table.hide()
        filters = self._build_filters()

        worker = DataLoaderWorker(filters)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_data_loaded)
        worker.error.connect(self._on_load_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._load_thread = thread
        thread.start()

    def _on_data_loaded(self, rows: list[dict]) -> None:
        self._all_rows = rows
        self._loading_label.hide()
        self._table.show()
        self._model.load(rows)
        self._count_label.setText(f"{len(rows):,} produit(s)")
        self._update_families(rows)
        log.info("Résultats chargés", count=len(rows))

    def _on_load_error(self, error: str) -> None:
        self._loading_label.setText(f"Erreur : {error}")
        log.error("Erreur chargement résultats", error=error)

    def _update_families(self, rows: list[dict]) -> None:
        """Met à jour le combo familles selon les données."""
        families = sorted(set(r.get("family", "") for r in rows if r.get("family")))
        current = self._family_combo.currentData()
        self._family_combo.blockSignals(True)
        self._family_combo.clear()
        self._family_combo.addItem("Toutes les familles", "")
        for f in families:
            self._family_combo.addItem(f, f)
        # Restaurer la sélection
        for i in range(self._family_combo.count()):
            if self._family_combo.itemData(i) == current:
                self._family_combo.setCurrentIndex(i)
                break
        self._family_combo.blockSignals(False)

    def _build_filters(self) -> dict:
        status_map = {
            0: "all", 1: "active", 2: "on_sale",
            3: "best_seller", 4: "removed",
        }
        return {
            "search":     self._search_input.text().strip(),
            "brand_slug": self._brand_combo.currentData() or "",
            "family":     self._family_combo.currentData() or "",
            "status":     status_map.get(self._status_combo.currentIndex(), "all"),
            "on_sale_only": self._status_combo.currentIndex() == 2,
        }

    def _on_filter_changed(self) -> None:
        """Applique les filtres côté client sur les données déjà chargées."""
        filters = self._build_filters()
        filtered = self._apply_filters_local(self._all_rows, filters)
        self._model.load(filtered)
        self._count_label.setText(f"{len(filtered):,} produit(s)")

    def _apply_filters_local(self, rows: list[dict], filters: dict) -> list[dict]:
        """Filtrage local rapide (sans requête DB)."""
        result = rows
        search = filters.get("search", "").lower()
        if search:
            result = [r for r in result if search in (r.get("name") or "").lower()]

        brand = filters.get("brand_slug")
        if brand:
            result = [r for r in result if r.get("brand") == brand]

        family = filters.get("family")
        if family:
            result = [r for r in result if r.get("family") == family]

        status = filters.get("status", "all")
        if status == "active":
            result = [r for r in result if r.get("is_active")]
        elif status == "removed":
            result = [r for r in result if not r.get("is_active")]
        elif status == "best_seller":
            result = [r for r in result if r.get("is_best_seller")]
        elif status == "on_sale":
            result = [r for r in result if r.get("on_sale")]

        return result

    def _on_row_selected(self, current, previous) -> None:
        """Affiche le détail du produit sélectionné."""
        if not current.isValid():
            return
        row = self._model.get_row(current.row())
        if row:
            self._detail_panel.show_product(row)

    # ── Exports ──────────────────────────────────────────────────────────

    def _export_csv(self) -> None:
        try:
            from app.exports.csv_exporter import CsvExporter
            brand = self._brand_combo.currentData() or None
            brands = [brand] if brand else None
            path = CsvExporter().export_from_db(brand_slugs=brands)
            log.info("Export CSV depuis résultats", path=str(path))
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export réussi", f"CSV créé :\n{path}")
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Erreur export", str(exc))

    def _export_excel(self) -> None:
        try:
            from app.exports.excel_exporter import ExcelExporter
            brand = self._brand_combo.currentData() or None
            brands = [brand] if brand else None
            path = ExcelExporter().export_from_db(brand_slugs=brands)
            log.info("Export Excel depuis résultats", path=str(path))
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export réussi", f"Excel créé :\n{path}")
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Erreur export", str(exc))
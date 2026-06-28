"""
Vue Historique — Liste chronologique des sessions d'analyse.

Fonctionnalités Phase 2 :
  - Liste sessions triée par date (plus récente en tête)
  - Détail session : stats + liste des changements (nouveaux, prix, promo, suppressions)
  - Filtre par marque et statut
  - Bouton "Générer rapport PDF/HTML"
  - Purge des sessions anciennes
"""
from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont, QColor, QBrush
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.logger import get_logger

log = get_logger(__name__)

_STATUS_COLORS = {
    "completed": ("#16A34A", "✓ Terminé"),
    "failed":    ("#DC2626", "✗ Échoué"),
    "running":   ("#2563EB", "⏳ En cours"),
    "cancelled": ("#D97706", "⚠ Annulé"),
}

_BRAND_COLORS = {
    "spanx":      "#1B3A6B",
    "skims":      "#C8A882",
    "honeylove":  "#C0392B",
    "shapermint": "#27AE60",
}

_EVENT_ICONS = {
    "product.new":           ("🆕", "#16A34A"),
    "product.removed":       ("🗑️", "#DC2626"),
    "product.back_in_stock": ("🔄", "#2563EB"),
    "price.changed":         ("💰", "#D97706"),
    "sale.started":          ("🏷️", "#7C3AED"),
    "sale.ended":            ("💸", "#64748B"),
    "best_seller.gained":    ("⭐", "#F59E0B"),
    "best_seller.lost":      ("☆",  "#94A3B8"),
    "availability.changed":  ("📦", "#0891B2"),
    "variant.added":         ("➕", "#16A34A"),
    "variant.removed":       ("➖", "#DC2626"),
    "variant.back_in_stock": ("✅", "#2563EB"),
}


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class SessionLoaderWorker(QObject):
    """Charge la liste des sessions depuis la DB."""
    finished = Signal(list)
    error    = Signal(str)

    def __init__(self, brand_slug: str = "") -> None:
        super().__init__()
        self._brand_slug = brand_slug

    def run(self) -> None:
        try:
            from app.storage.database import get_db
            from app.storage.models import Brand, CrawlSession
            with get_db() as db:
                query = db.query(CrawlSession).order_by(CrawlSession.started_at.desc())
                if self._brand_slug:
                    brand = db.query(Brand).filter_by(slug=self._brand_slug).first()
                    if brand:
                        query = query.filter(CrawlSession.brand_id == brand.id)
                sessions = query.limit(100).all()
                brands = {b.id: b for b in db.query(Brand).all()}
                result = []
                for s in sessions:
                    brand = brands.get(s.brand_id)
                    duration = "—"
                    if s.started_at and s.ended_at:
                        secs = int((s.ended_at - s.started_at).total_seconds())
                        duration = f"{secs // 60}m {secs % 60}s"
                    result.append({
                        "id":               s.id,
                        "brand_slug":       brand.slug if brand else "?",
                        "brand_name":       brand.name if brand else "?",
                        "started_at":       s.started_at,
                        "ended_at":         s.ended_at,
                        "duration":         duration,
                        "status":           s.status,
                        "products_found":   s.products_found,
                        "products_new":     s.products_new,
                        "products_changed": s.products_changed,
                        "products_removed": s.products_removed,
                        "errors_count":     s.errors_count,
                    })
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


class SessionDetailWorker(QObject):
    """Charge les changements d'une session."""
    finished = Signal(list)
    error    = Signal(str)

    def __init__(self, session_id: int) -> None:
        super().__init__()
        self._session_id = session_id

    def run(self) -> None:
        try:
            from app.storage.database import get_db
            from app.storage.models import ChangeEvent, Product
            with get_db() as db:
                events = (
                    db.query(ChangeEvent)
                    .filter_by(session_id=self._session_id)
                    .order_by(ChangeEvent.detected_at.desc())
                    .limit(500)
                    .all()
                )
                products = {p.id: p for p in db.query(Product).all()}
                result = []
                for e in events:
                    p = products.get(e.product_id)
                    result.append({
                        "event_type":  e.event_type,
                        "product_name": p.name if p else "—",
                        "field_name":  e.field_name or "",
                        "old_value":   e.old_value or "",
                        "new_value":   e.new_value or "",
                        "detected_at": e.detected_at,
                    })
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))


# ---------------------------------------------------------------------------
# Panneau de détail d'une session
# ---------------------------------------------------------------------------

class SessionDetailPanel(QScrollArea):
    """Panneau latéral affichant le détail d'une session sélectionnée."""

    report_requested = Signal(int)  # session_id

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumWidth(380)

        container = QWidget()
        self._layout = QVBoxLayout(container)
        self._layout.setContentsMargins(12, 12, 12, 12)
        self._layout.setSpacing(8)
        self.setWidget(container)

        placeholder = QLabel("Sélectionnez une session\npour voir le détail.")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color: #94A3B8; font-size: 11pt;")
        self._layout.addWidget(placeholder)
        self._layout.addStretch()
        self._placeholder = placeholder
        self._dynamic_widgets: list[QWidget] = []
        self._current_session_id: int | None = None

    def show_session(self, session: dict) -> None:
        self._clear_dynamic()
        self._placeholder.hide()
        self._current_session_id = session["id"]

        # En-tête
        brand_color = _BRAND_COLORS.get(session.get("brand_slug", ""), "#2C3E50")
        title = QLabel(f"Session #{session['id']} — {session.get('brand_name', '?')}")
        tf = QFont(); tf.setBold(True); tf.setPointSize(11)
        title.setFont(tf)
        title.setStyleSheet(f"color: {brand_color};")
        self._add(title)

        # Date et durée
        started = session.get("started_at")
        date_str = started.strftime("%d/%m/%Y à %H:%M") if started else "—"
        info = QLabel(f"📅 {date_str}  |  ⏱ {session.get('duration', '—')}")
        info.setStyleSheet("color: #64748B; font-size: 9pt;")
        self._add(info)

        # Statut
        status = session.get("status", "")
        s_color, s_label = _STATUS_COLORS.get(status, ("#64748B", status))
        status_lbl = QLabel(s_label)
        status_lbl.setStyleSheet(f"color: {s_color}; font-weight: bold; font-size: 10pt;")
        self._add(status_lbl)

        sep = QFrame(); sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color: #E2E8F0;"); self._add(sep)

        # KPIs de la session
        kpis = [
            ("Produits analysés",  session.get("products_found", 0),   "#2563EB"),
            ("Nouveaux",           session.get("products_new", 0),     "#16A34A"),
            ("Modifiés",           session.get("products_changed", 0), "#D97706"),
            ("Supprimés",          session.get("products_removed", 0), "#DC2626"),
            ("Erreurs",            session.get("errors_count", 0),     "#64748B"),
        ]
        kpi_w = QWidget()
        kpi_l = QHBoxLayout(kpi_w)
        kpi_l.setContentsMargins(0, 0, 0, 0)
        kpi_l.setSpacing(4)
        for label, value, color in kpis:
            card = QFrame()
            card.setStyleSheet(
                f"QFrame {{ background: #F8FAFC; border: 1px solid #E2E8F0; "
                f"border-radius: 6px; }}"
            )
            c_l = QVBoxLayout(card)
            c_l.setContentsMargins(6, 4, 6, 4)
            c_l.setSpacing(0)
            v_lbl = QLabel(str(value))
            vf = QFont(); vf.setBold(True); vf.setPointSize(14)
            v_lbl.setFont(vf)
            v_lbl.setStyleSheet(f"color: {color}; border: none;")
            v_lbl.setAlignment(Qt.AlignCenter)
            l_lbl = QLabel(label)
            l_lbl.setStyleSheet("color: #64748B; font-size: 7pt; border: none;")
            l_lbl.setAlignment(Qt.AlignCenter)
            l_lbl.setWordWrap(True)
            c_l.addWidget(v_lbl)
            c_l.addWidget(l_lbl)
            kpi_l.addWidget(card)
        self._add(kpi_w)

        sep2 = QFrame(); sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #E2E8F0;"); self._add(sep2)

        # Bouton rapport
        btn_row = QHBoxLayout()
        btn_pdf = QPushButton("📄 Générer rapport PDF")
        btn_pdf.setStyleSheet(
            "QPushButton { background: #1B3A6B; color: white; border-radius: 6px; "
            "padding: 5px 12px; font-size: 9pt; }"
            "QPushButton:hover { background: #1e40af; }"
        )
        btn_pdf.clicked.connect(lambda: self._generate_report("pdf"))
        btn_row.addWidget(btn_pdf)
        btn_html = QPushButton("🌐 HTML")
        btn_html.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; "
            "padding: 5px 10px; font-size: 9pt; }"
        )
        btn_html.clicked.connect(lambda: self._generate_report("html"))
        btn_row.addWidget(btn_html)
        btn_row.addStretch()
        btn_w = QWidget(); btn_w.setLayout(btn_row)
        self._add(btn_w)

        sep3 = QFrame(); sep3.setFrameShape(QFrame.HLine)
        sep3.setStyleSheet("color: #E2E8F0;"); self._add(sep3)

        # Titre changements
        chg_title = QLabel("Changements détectés")
        chg_title.setStyleSheet("font-weight: bold; font-size: 9pt; color: #475569;")
        self._add(chg_title)

        # Placeholder pendant le chargement
        self._events_label = QLabel("Chargement des changements…")
        self._events_label.setStyleSheet("color: #94A3B8; font-size: 9pt;")
        self._add(self._events_label)

        self._layout.addStretch()

        # Charger les événements async
        self._load_events(session["id"])

    def _load_events(self, session_id: int) -> None:
        worker = SessionDetailWorker(session_id)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_events_loaded)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._detail_thread = thread
        thread.start()

    def _on_events_loaded(self, events: list) -> None:
        if hasattr(self, "_events_label"):
            self._layout.removeWidget(self._events_label)
            self._events_label.deleteLater()

        if not events:
            no_evt = QLabel("Aucun changement enregistré.")
            no_evt.setStyleSheet("color: #94A3B8; font-size: 9pt; padding: 8px;")
            self._add(no_evt)
            return

        # Regrouper par type
        by_type: dict[str, list] = {}
        for e in events:
            by_type.setdefault(e["event_type"], []).append(e)

        for event_type, evts in sorted(by_type.items()):
            icon, color = _EVENT_ICONS.get(event_type, ("•", "#64748B"))
            type_label = QLabel(f"{icon}  {event_type.replace('.', ' › ')} ({len(evts)})")
            type_label.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 9pt;")
            self._add(type_label)

            # Afficher les 5 premiers
            for e in evts[:5]:
                prod_name = e.get("product_name", "?")
                old_v = e.get("old_value", "")
                new_v = e.get("new_value", "")
                if old_v and new_v and event_type == "price.changed":
                    try:
                        delta = float(new_v) - float(old_v)
                        detail = (
                            f"  {prod_name[:40]}…\n"
                            f"  ${float(old_v):.2f} → ${float(new_v):.2f} "
                            f"({'▲' if delta > 0 else '▼'} {abs(delta):.2f})"
                        )
                    except Exception:
                        detail = f"  {prod_name[:40]}"
                else:
                    detail = f"  {prod_name[:50]}"
                item_lbl = QLabel(detail)
                item_lbl.setStyleSheet("color: #475569; font-size: 8pt; padding-left: 12px;")
                item_lbl.setWordWrap(True)
                self._add(item_lbl)

            if len(evts) > 5:
                more = QLabel(f"  … et {len(evts)-5} autre(s)")
                more.setStyleSheet("color: #94A3B8; font-size: 8pt; padding-left: 12px;")
                self._add(more)

    def _generate_report(self, fmt: str) -> None:
        if not self._current_session_id:
            return
        try:
            from app.exports.pdf_exporter import PdfExporter
            exporter = PdfExporter()
            if fmt == "pdf":
                path = exporter.export_from_db(session_id=self._current_session_id)
            else:
                path = exporter.export_html(filename=f"report_session{self._current_session_id}.html")
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self.parent(), "Rapport généré", f"Fichier créé :\n{path}"
            )
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self.parent(), "Erreur", str(exc))

    def _add(self, w: QWidget) -> None:
        pos = max(0, self._layout.count() - 1)
        self._layout.insertWidget(pos, w)
        self._dynamic_widgets.append(w)

    def _clear_dynamic(self) -> None:
        for w in self._dynamic_widgets:
            self._layout.removeWidget(w)
            w.deleteLater()
        self._dynamic_widgets.clear()


# ---------------------------------------------------------------------------
# Vue principale
# ---------------------------------------------------------------------------

class HistoryView(QWidget):
    """Vue Historique des sessions d'analyse."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._sessions: list[dict] = []
        self._load_thread: QThread | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # En-tête
        header_w = QWidget()
        header_w.setStyleSheet("background: #F8FAFC; border-bottom: 1px solid #E2E8F0;")
        header_l = QVBoxLayout(header_w)
        header_l.setContentsMargins(20, 12, 20, 12)

        title_row = QHBoxLayout()
        title = QLabel("Historique des Sessions")
        tf = QFont(); tf.setPointSize(14); tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet("color: #1E293B;")
        title_row.addWidget(title)
        title_row.addStretch()

        # Filtre marque
        self._brand_filter = QComboBox()
        self._brand_filter.addItem("Toutes les marques", "")
        for slug in ["spanx", "skims", "honeylove", "shapermint"]:
            self._brand_filter.addItem(slug.upper(), slug)
        self._brand_filter.setStyleSheet(
            "QComboBox { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 10px; }"
        )
        self._brand_filter.currentIndexChanged.connect(self.refresh)
        title_row.addWidget(self._brand_filter)

        btn_refresh = QPushButton("↻ Actualiser")
        btn_refresh.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 10px; }"
            "QPushButton:hover { background: #F1F5F9; }"
        )
        btn_refresh.clicked.connect(self.refresh)
        title_row.addWidget(btn_refresh)
        header_l.addLayout(title_row)
        root.addWidget(header_w)

        # Splitter : liste | détail
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: #E2E8F0; }")

        # Tableau des sessions
        table_w = QWidget()
        table_l = QVBoxLayout(table_w)
        table_l.setContentsMargins(0, 0, 0, 0)

        self._sessions_table = QTableWidget(0, 7)
        self._sessions_table.setHorizontalHeaderLabels([
            "Marque", "Date", "Durée", "Produits",
            "Nouveaux", "Modifiés", "Statut"
        ])
        self._sessions_table.horizontalHeader().setStretchLastSection(True)
        self._sessions_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._sessions_table.setAlternatingRowColors(True)
        self._sessions_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._sessions_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._sessions_table.verticalHeader().setVisible(False)
        self._sessions_table.setShowGrid(False)
        self._sessions_table.setStyleSheet(
            "QTableWidget { border: none; font-size: 9pt; }"
            "QTableWidget::item { padding: 5px 8px; border-bottom: 1px solid #F1F5F9; }"
            "QTableWidget::item:selected { background: #EFF6FF; color: #1E293B; }"
            "QHeaderView::section { background: #F8FAFC; font-weight: bold; "
            "border-bottom: 2px solid #E2E8F0; padding: 6px 8px; }"
        )
        self._sessions_table.itemSelectionChanged.connect(self._on_session_selected)
        table_l.addWidget(self._sessions_table)

        splitter.addWidget(table_w)

        # Panneau détail
        self._detail = SessionDetailPanel()
        splitter.addWidget(self._detail)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        root.addWidget(splitter)

    def refresh(self) -> None:
        brand = self._brand_filter.currentData() or ""
        worker = SessionLoaderWorker(brand)
        thread = QThread()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_sessions_loaded)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(thread.deleteLater)
        self._load_thread = thread
        thread.start()

    def _on_sessions_loaded(self, sessions: list[dict]) -> None:
        self._sessions = sessions
        self._sessions_table.setRowCount(len(sessions))

        for row, s in enumerate(sessions):
            brand_color = _BRAND_COLORS.get(s.get("brand_slug", ""), "#2C3E50")
            status = s.get("status", "")
            s_color, s_label = _STATUS_COLORS.get(status, ("#64748B", status))

            started = s.get("started_at")
            date_str = started.strftime("%d/%m/%Y %H:%M") if started else "—"

            items = [
                (s.get("brand_name", "?"), brand_color),
                (date_str,                 "#1E293B"),
                (s.get("duration", "—"),   "#64748B"),
                (str(s.get("products_found", 0)),   "#1E293B"),
                (str(s.get("products_new", 0)),     "#16A34A"),
                (str(s.get("products_changed", 0)), "#D97706"),
                (s_label,                  s_color),
            ]
            for col, (text, color) in enumerate(items):
                item = QTableWidgetItem(text)
                item.setForeground(QBrush(QColor(color)))
                if col in (3, 4, 5):
                    item.setTextAlignment(Qt.AlignCenter)
                self._sessions_table.setItem(row, col, item)

        self._sessions_table.resizeColumnsToContents()
        log.info("Sessions chargées", count=len(sessions))

    def _on_session_selected(self) -> None:
        rows = self._sessions_table.selectedItems()
        if not rows:
            return
        row_idx = self._sessions_table.currentRow()
        if 0 <= row_idx < len(self._sessions):
            self._detail.show_session(self._sessions[row_idx])
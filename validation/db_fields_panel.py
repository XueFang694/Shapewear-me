"""
db_fields_panel.py — Panneau droit de l'outil de validation.

Affiche tous les champs de la base de données pour un produit donné.
Chaque ligne émet un signal au survol pour déclencher le highlight
dans le WebView.

Signaux :
    field_hovered(field_name)   : survol d'un champ
    field_left()                : souris quittant le panneau
    field_validated(product_id, field_name)
    field_error_reported(product_id, field_name, comment)
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from validation.zone_mapper import FIELD_COLORS

# ── Constantes visuelles ─────────────────────────────────────────────────────

_STATUS_COLORS = {
    "ok":      {"bg": "#F0FDF4", "border": "#86EFAC", "dot": "#16A34A"},
    "empty":   {"bg": "#FFFBEB", "border": "#FCD34D", "dot": "#D97706"},
    "suspect": {"bg": "#FFF1F2", "border": "#FDA4AF", "dot": "#E11D48"},
    "neutral": {"bg": "#F8FAFC", "border": "#E2E8F0", "dot": "#94A3B8"},
}

_ROW_HOVER_BG = "#EFF6FF"


def _value_status(value: str | None) -> str:
    """Détermine le statut d'une valeur pour le code couleur."""
    if value is None or value.strip() == "" or value in ("None", "—"):
        return "empty"
    # Valeurs suspectes
    suspect_patterns = ["unknown", "null", "n/a", "0", "non", "0.0"]
    if str(value).lower() in suspect_patterns:
        return "suspect"
    return "ok"


class FieldRow(QFrame):
    """
    Une ligne dans le panneau BDD représentant un champ.

    Émet des signaux au survol et aux clics sur les boutons d'action.
    """

    hovered    = Signal(str)   # field_name
    left       = Signal()
    validated  = Signal(str)   # field_name
    error_reported = Signal(str, str)  # field_name, comment

    def __init__(
        self,
        field_name: str,
        value: str | None,
        category: str,
        found_on_page: bool = True,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._field_name = field_name
        self._value = value
        self._category = category
        self._found_on_page = found_on_page
        self._is_validated = False
        self._status = _value_status(value)

        self._setup_ui()
        self.setMouseTracking(True)
        self.installEventFilter(self)

    def _setup_ui(self) -> None:
        colors = FIELD_COLORS.get(self._category, FIELD_COLORS["meta"])
        status = _STATUS_COLORS.get(self._status, _STATUS_COLORS["neutral"])

        self.setFrameShape(QFrame.NoFrame)
        self.setFixedHeight(52)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._apply_style(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)

        # Bande colorée gauche (catégorie)
        accent = QFrame()
        accent.setFixedWidth(3)
        accent.setFixedHeight(36)
        accent.setStyleSheet(
            f"background: {colors['outline']}; border-radius: 2px;"
        )
        layout.addWidget(accent)

        # Colonne principale : nom + valeur
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)

        name_label = QLabel(self._field_name)
        name_label.setStyleSheet(
            "color: #374151; font-size: 8pt; font-weight: 600; background: transparent;"
        )
        text_col.addWidget(name_label)

        val_text = str(self._value) if self._value else "—"
        if len(val_text) > 80:
            val_text = val_text[:77] + "…"

        self._value_label = QLabel(val_text)
        val_color = {
            "ok":      "#1E293B",
            "empty":   "#D97706",
            "suspect": "#DC2626",
            "neutral": "#64748B",
        }.get(self._status, "#64748B")
        self._value_label.setStyleSheet(
            f"color: {val_color}; font-size: 8pt; background: transparent;"
        )
        self._value_label.setWordWrap(False)
        text_col.addWidget(self._value_label)
        layout.addLayout(text_col, 1)

        # Indicateur "trouvé sur la page"
        self._page_dot = QLabel()
        dot_tooltip = "Champ trouvé sur la page" if self._found_on_page else "Champ non détecté"
        dot_color = "#16A34A" if self._found_on_page else "#94A3B8"
        self._page_dot.setFixedSize(8, 8)
        self._page_dot.setStyleSheet(
            f"background: {dot_color}; border-radius: 4px;"
        )
        self._page_dot.setToolTip(dot_tooltip)
        layout.addWidget(self._page_dot)

        # Dot statut BDD
        bdd_dot = QLabel()
        bdd_dot.setFixedSize(8, 8)
        bdd_dot.setStyleSheet(
            f"background: {status['dot']}; border-radius: 4px;"
        )
        bdd_dot.setToolTip(f"Valeur BDD : {self._status}")
        layout.addWidget(bdd_dot)

        # Boutons action (apparaissent au survol via CSS)
        self._btn_ok = QPushButton("✓")
        self._btn_ok.setFixedSize(22, 22)
        self._btn_ok.setToolTip("Marquer comme vérifié")
        self._btn_ok.setStyleSheet(
            "QPushButton { background: #D1FAE5; color: #065F46; border: none; "
            "border-radius: 4px; font-size: 9pt; font-weight: bold; }"
            "QPushButton:hover { background: #6EE7B7; }"
        )
        self._btn_ok.clicked.connect(self._on_validate)
        self._btn_ok.hide()
        layout.addWidget(self._btn_ok)

        self._btn_err = QPushButton("⚠")
        self._btn_err.setFixedSize(22, 22)
        self._btn_err.setToolTip("Signaler une erreur")
        self._btn_err.setStyleSheet(
            "QPushButton { background: #FEE2E2; color: #991B1B; border: none; "
            "border-radius: 4px; font-size: 9pt; }"
            "QPushButton:hover { background: #FECACA; }"
        )
        self._btn_err.clicked.connect(self._on_report_error)
        self._btn_err.hide()
        layout.addWidget(self._btn_err)

    def _apply_style(self, hovered: bool) -> None:
        if hovered:
            self.setStyleSheet(
                "FieldRow { background: #EFF6FF; border-bottom: 1px solid #BFDBFE; }"
            )
        else:
            self.setStyleSheet(
                "FieldRow { background: white; border-bottom: 1px solid #F1F5F9; }"
            )

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Enter:
            self._apply_style(True)
            self._btn_ok.show()
            self._btn_err.show()
            self.hovered.emit(self._field_name)
        elif event.type() == QEvent.Leave:
            self._apply_style(False)
            self._btn_ok.hide()
            self._btn_err.hide()
            self.left.emit()
        return super().eventFilter(obj, event)

    def mark_found(self, found: bool) -> None:
        """Met à jour l'indicateur 'trouvé sur la page'."""
        self._found_on_page = found
        dot_color = "#16A34A" if found else "#94A3B8"
        self._page_dot.setStyleSheet(
            f"background: {dot_color}; border-radius: 4px;"
        )

    def mark_validated(self) -> None:
        """Marque visuellement ce champ comme validé."""
        self._is_validated = True
        self.setStyleSheet(
            "FieldRow { background: #F0FDF4; border-bottom: 1px solid #BBF7D0; }"
        )

    def _on_validate(self) -> None:
        self.mark_validated()
        self.validated.emit(self._field_name)

    def _on_report_error(self) -> None:
        dlg = _ErrorDialog(self._field_name, self._value, self)
        if dlg.exec() == QDialog.Accepted:
            self.error_reported.emit(self._field_name, dlg.get_comment())
            self.setStyleSheet(
                "FieldRow { background: #FFF1F2; border-bottom: 1px solid #FECDD3; }"
            )


class _ErrorDialog(QDialog):
    """Dialog pour saisir un commentaire d'erreur."""

    def __init__(self, field_name: str, current_value: str | None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Signaler une erreur")
        self.setFixedSize(400, 220)
        self.setStyleSheet("QDialog { background: #F8FAFC; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        layout.addWidget(QLabel(f"Champ : <b>{field_name}</b>"))
        layout.addWidget(QLabel(f"Valeur actuelle : <code>{current_value or '—'}</code>"))

        layout.addWidget(QLabel("Commentaire :"))
        self._comment = QTextEdit()
        self._comment.setFixedHeight(70)
        self._comment.setPlaceholderText("Décrivez l'anomalie détectée…")
        self._comment.setStyleSheet(
            "QTextEdit { border: 1px solid #CBD5E1; border-radius: 4px; "
            "padding: 4px; background: white; color: #1E293B; font-size: 9pt; }"
        )
        layout.addWidget(self._comment)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_comment(self) -> str:
        return self._comment.toPlainText().strip()


# ── Panneau complet ───────────────────────────────────────────────────────────

class DbFieldsPanel(QWidget):
    """
    Panneau droit affichant tous les champs BDD d'un produit.

    Émet des signaux pour le highlight synchronisé avec la WebView.
    """

    field_hovered  = Signal(str)    # field_name
    field_left     = Signal()
    field_validated = Signal(int, str)   # product_id, field_name
    field_error    = Signal(int, str, str)  # product_id, field_name, comment

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._product_id: int | None = None
        self._rows: dict[str, FieldRow] = {}
        self._found_fields: set[str] = set()
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # En-tête du produit
        self._header = QWidget()
        self._header.setStyleSheet("background: #1E293B;")
        self._header.setFixedHeight(64)
        header_layout = QVBoxLayout(self._header)
        header_layout.setContentsMargins(12, 8, 12, 8)
        header_layout.setSpacing(2)

        self._product_name_label = QLabel("Sélectionnez un produit")
        name_font = QFont()
        name_font.setPointSize(10)
        name_font.setBold(True)
        self._product_name_label.setFont(name_font)
        self._product_name_label.setStyleSheet("color: #E2E8F0; background: transparent;")
        header_layout.addWidget(self._product_name_label)

        self._product_meta_label = QLabel("")
        self._product_meta_label.setStyleSheet("color: #64748B; font-size: 8pt; background: transparent;")
        header_layout.addWidget(self._product_meta_label)
        root.addWidget(self._header)

        # Légende
        legend = _LegendBar()
        root.addWidget(legend)

        # Compteur de champs trouvés
        self._found_counter = QLabel("")
        self._found_counter.setStyleSheet(
            "color: #475569; font-size: 8pt; padding: 3px 10px; "
            "background: #F8FAFC; border-bottom: 1px solid #E2E8F0;"
        )
        root.addWidget(self._found_counter)

        # Liste scrollable des champs
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { border: none; background: white; }"
            "QScrollBar:vertical { width: 6px; background: #F1F5F9; }"
            "QScrollBar::handle:vertical { background: #CBD5E1; border-radius: 3px; }"
        )

        self._fields_container = QWidget()
        self._fields_container.setStyleSheet("background: white;")
        self._fields_layout = QVBoxLayout(self._fields_container)
        self._fields_layout.setContentsMargins(0, 0, 0, 0)
        self._fields_layout.setSpacing(0)
        self._fields_layout.addStretch()

        scroll.setWidget(self._fields_container)
        root.addWidget(scroll, 1)

    # ── Interface publique ────────────────────────────────────────────────

    def load_product(self, item) -> None:
        """
        Charge les champs d'un ProductValidationItem dans le panneau.

        Args:
            item: ProductValidationItem
        """
        from validation.product_loader import ProductValidationItem
        self._product_id = item.id
        self._rows.clear()
        self._found_fields.clear()

        # En-tête
        self._product_name_label.setText(item.name)
        self._product_meta_label.setText(
            f"{item.brand_slug.upper()}  ·  {item.family or '—'}  ·  "
            f"{'Actif' if item.is_active else 'Inactif'}"
        )

        # Vider l'ancienne liste
        while self._fields_layout.count() > 1:
            item_w = self._fields_layout.takeAt(0)
            if item_w.widget():
                item_w.widget().deleteLater()

        # Remplir avec les nouveaux champs
        field_dict = item.to_field_dict()
        for field_name, value in field_dict.items():
            category = item.field_category(field_name)
            row = FieldRow(
                field_name=field_name,
                value=value,
                category=category,
                found_on_page=False,  # sera mis à jour quand la page se charge
            )
            row.hovered.connect(self.field_hovered)
            row.left.connect(self.field_left)
            row.validated.connect(
                lambda fn, pid=self._product_id: self.field_validated.emit(pid, fn)
            )
            row.error_reported.connect(
                lambda fn, cmt, pid=self._product_id: self.field_error.emit(pid, fn, cmt)
            )

            self._fields_layout.insertWidget(
                self._fields_layout.count() - 1, row
            )
            self._rows[field_name] = row

        self._update_found_counter()

    def update_found_fields(self, found: list[str]) -> None:
        """Met à jour les indicateurs 'trouvé sur la page' pour chaque champ."""
        self._found_fields = set(found)
        for field_name, row in self._rows.items():
            row.mark_found(field_name in self._found_fields)
        self._update_found_counter()

    def _update_found_counter(self) -> None:
        total = len(self._rows)
        found = len(self._found_fields)
        if total == 0:
            self._found_counter.setText("")
            return
        pct = int(found / total * 100) if total else 0
        self._found_counter.setText(
            f"  Zones détectées sur la page : {found}/{total} ({pct}%)"
        )


class _LegendBar(QWidget):
    """Barre de légende des codes couleurs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setFixedHeight(24)
        self.setStyleSheet("background: #F8FAFC; border-bottom: 1px solid #E2E8F0;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(10)

        categories = [
            ("Nom",    "title"),
            ("Prix",   "price"),
            ("Stock",  "availability"),
            ("Tailles","variants"),
            ("Avis",   "reviews"),
            ("Matière","materials"),
        ]

        for label, cat in categories:
            colors = FIELD_COLORS.get(cat, FIELD_COLORS["meta"])
            dot = QLabel("●")
            dot.setStyleSheet(
                f"color: {colors['outline']}; font-size: 9pt; background: transparent;"
            )
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #64748B; font-size: 7pt; background: transparent;")
            layout.addWidget(dot)
            layout.addWidget(lbl)

        layout.addStretch()
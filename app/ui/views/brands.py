"""
Vue Brands — Gestion des connecteurs de marques.

Contenu :
  - Liste des connecteurs disponibles avec statut
  - Bouton "Tester la connexion" par connecteur
  - Affichage des métadonnées (moteur, URL de base, rate-limit)
  - Résumé des produits par marque
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
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

_ENGINE_LABELS = {
    "shopify_json": "Shopify JSON API",
    "html":         "HTML (sélecteurs CSS)",
    "graphql":      "GraphQL",
}


class ConnectionWorker(QObject):
    """Teste la connexion dans un thread séparé."""

    result   = Signal(str, str)  # (slug, status)
    finished = Signal()

    def __init__(self, slug: str) -> None:
        super().__init__()
        self._slug = slug

    def run(self) -> None:
        try:
            from app.connectors.registry import ConnectorRegistry
            connector = ConnectorRegistry().get(self._slug)
            status    = connector.test_connection()
            self.result.emit(self._slug, status.value)
        except Exception as exc:
            self.result.emit(self._slug, f"error: {exc}")
        finally:
            # Toujours émettre finished pour libérer le thread proprement
            self.finished.emit()


class ConnectorCard(QFrame):
    """Carte d'un connecteur avec ses métadonnées et actions."""

    test_requested = Signal(str)  # slug

    def __init__(self, slug: str, meta: dict, stats: dict, parent=None) -> None:
        super().__init__(parent)
        self._slug = slug
        color = _BRAND_COLORS.get(slug, "#2C3E50")

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            f"ConnectorCard {{ background: white; border-radius: 10px; "
            f"border: 2px solid {color}; }}"
        )
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        # En-tête : nom + pastille couleur
        header = QHBoxLayout()
        name_label = QLabel(meta.get("name", slug.upper()))
        name_font  = QFont()
        name_font.setPointSize(13)
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setStyleSheet(f"color: {color}; border: none;")
        header.addWidget(name_label)
        header.addStretch()

        self._status_label = QLabel("●  Non testé")
        self._status_label.setStyleSheet("color: #94A3B8; border: none; font-size: 9pt;")
        header.addWidget(self._status_label)
        layout.addLayout(header)

        # Métadonnées
        engine_name = _ENGINE_LABELS.get(meta.get("engine", ""), meta.get("engine", ""))
        info_text = (
            f"URL : {meta.get('base_url', '—')}\n"
            f"Moteur : {engine_name}\n"
            f"Rate limit : {meta.get('rate_limit_rps', '—')} req/s  |  "
            f"Délai : {meta.get('delay_min', '—')}–{meta.get('delay_max', '—')} s"
        )
        info = QLabel(info_text)
        info.setStyleSheet("color: #64748B; font-size: 8pt; border: none;")
        layout.addWidget(info)

        # Stats produits
        if stats:
            stats_text = (
                f"Produits actifs : {stats.get('active', 0)}  |  "
                f"Best Sellers : {stats.get('best_sellers', 0)}  |  "
                f"En promo : {stats.get('on_sale', 0)}  |  "
                f"Supprimés : {stats.get('removed', 0)}"
            )
            stats_lbl = QLabel(stats_text)
            stats_lbl.setStyleSheet("color: #475569; font-size: 8pt; border: none;")
            layout.addWidget(stats_lbl)

        # Bouton test
        btn_row = QHBoxLayout()
        btn_test = QPushButton("🔍  Tester la connexion")
        btn_test.setStyleSheet(
            f"QPushButton {{ background: {color}; color: white; border-radius: 6px; "
            f"padding: 4px 14px; font-size: 9pt; }}"
        )
        btn_test.clicked.connect(lambda: self.test_requested.emit(slug))
        btn_row.addWidget(btn_test)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def set_connection_status(self, status: str) -> None:
        color_map = {
            "ok":      ("🟢  Connecté", "#16A34A"),
            "failed":  ("🔴  Échec",    "#DC2626"),
            "blocked": ("🟡  Bloqué",   "#D97706"),
            "timeout": ("🟠  Timeout",  "#EA580C"),
        }
        # Gestion des statuts "error: ..." non listés
        if status not in color_map:
            label = f"⚪  {status[:30]}"
            color = "#64748B"
        else:
            label, color = color_map[status]
        self._status_label.setText(label)
        self._status_label.setStyleSheet(
            f"color: {color}; border: none; font-size: 9pt; font-weight: bold;"
        )

    def set_testing(self) -> None:
        self._status_label.setText("⏳  Test en cours…")
        self._status_label.setStyleSheet("color: #2563EB; border: none; font-size: 9pt;")


class BrandsView(QWidget):
    """Vue de gestion des connecteurs de marques."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cards: dict[str, ConnectorCard] = {}
        # Garder des références fortes sur (worker, thread) pour éviter GC prématuré
        self._active_tests: list[tuple] = []
        self._setup_ui()

    # ── Construction ─────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # Titre
        title = QLabel("Connecteurs Marques")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet("color: #1E293B;")
        root.addWidget(title)

        subtitle = QLabel(
            "Gérez les connecteurs de scraping pour chaque marque. "
            "Testez la connexion avant de lancer une session d'analyse."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #64748B; font-size: 10pt;")
        root.addWidget(subtitle)

        # Bouton tester tout
        btn_row = QHBoxLayout()
        self._btn_test_all = QPushButton("🔍  Tester toutes les connexions")
        self._btn_test_all.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 6px 16px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._btn_test_all.clicked.connect(self._test_all)
        btn_row.addWidget(self._btn_test_all)
        btn_row.addStretch()
        root.addLayout(btn_row)

        # Zone scrollable pour les cartes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; }")

        container = QWidget()
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setSpacing(12)
        self._cards_layout.setContentsMargins(0, 0, 0, 0)
        self._cards_layout.addStretch()

        scroll.setWidget(container)
        root.addWidget(scroll)

    # ── Chargement ────────────────────────────────────────────────────────

    def refresh(self) -> None:
        try:
            self._load_connectors()
        except Exception as exc:
            log.error("Erreur chargement brands", error=str(exc))

    def _load_connectors(self) -> None:
        from app.connectors.registry import ConnectorRegistry
        from app.storage.database import get_db
        from app.storage.models import Brand, Product
        from app.storage.repository import SnapshotRepository

        registry = ConnectorRegistry()
        slugs    = registry.list_connectors()

        # Stats depuis la base
        db_stats: dict[str, dict] = {}
        try:
            with get_db() as db:
                brands = db.query(Brand).all()
                snap_repo = SnapshotRepository(db)
                for b in brands:
                    products  = db.query(Product).filter_by(brand_id=b.id).all()
                    snapshots = {p.id: snap_repo.get_latest(p.id) for p in products}
                    db_stats[b.slug] = {
                        "active":       sum(1 for p in products if p.is_active),
                        "best_sellers": sum(1 for p in products if p.is_best_seller),
                        "on_sale":      sum(
                            1 for p in products
                            if snapshots.get(p.id) and snapshots[p.id].on_sale
                        ),
                        "removed":      sum(1 for p in products if not p.is_active),
                    }
        except Exception as exc:
            log.warning("Impossible de charger les stats", error=str(exc))

        # Supprimer les anciennes cartes
        for card in self._cards.values():
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()

        # Créer les nouvelles cartes
        for slug in slugs:
            try:
                connector = registry.get(slug)
                meta_obj  = connector.get_metadata()
                meta = {
                    "name":           meta_obj.name,
                    "base_url":       meta_obj.base_url,
                    "engine":         meta_obj.engine,
                    "rate_limit_rps": connector.rate_limit_rps,
                    "delay_min":      connector.delay_min,
                    "delay_max":      connector.delay_max,
                }
            except Exception:
                meta = {"name": slug.upper()}

            card = ConnectorCard(slug, meta, db_stats.get(slug, {}))
            card.test_requested.connect(self._test_single)
            # Insérer avant le stretch
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            self._cards[slug] = card

    # ── Test de connexion ─────────────────────────────────────────────────

    def _test_single(self, slug: str) -> None:
        card = self._cards.get(slug)
        if card:
            card.set_testing()

        worker = ConnectionWorker(slug)
        thread = QThread()
        worker.moveToThread(thread)

        # Garder une référence forte sur worker ET thread
        pair = (worker, thread)
        self._active_tests.append(pair)

        def _on_result(s: str, status: str) -> None:
            self._on_test_result(s, status)

        def _cleanup() -> None:
            # Retirer la paire de la liste après fin du thread
            try:
                self._active_tests.remove(pair)
            except ValueError:
                pass

        thread.started.connect(worker.run)
        worker.result.connect(_on_result)
        # finished du worker → quit du thread
        worker.finished.connect(thread.quit)
        # Après que le thread est bien arrêté → nettoyage
        thread.finished.connect(_cleanup)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        thread.start()

    def _test_all(self) -> None:
        for slug in list(self._cards.keys()):
            self._test_single(slug)

    def _on_test_result(self, slug: str, status: str) -> None:
        card = self._cards.get(slug)
        if card:
            card.set_connection_status(status)
        log.info("Test connexion", brand=slug, status=status)
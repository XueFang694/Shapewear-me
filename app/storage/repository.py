"""
Repositories v2 — accès aux données avec gestion du cycle de vie des variantes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.storage.models import Brand, ChangeEvent, CrawlSession, Product, ProductSnapshot, Variant
from app.core.logger import get_logger

log = get_logger(__name__)


@dataclass
class ProductFilter:
    brand_id: int | None = None
    family: str | None = None
    subfamily: str | None = None
    is_active: bool | None = True
    on_sale: bool | None = None
    is_best_seller: bool | None = None
    search: str | None = None


# ---------------------------------------------------------------------------
# BrandRepository
# ---------------------------------------------------------------------------

class BrandRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def get_by_slug(self, slug: str) -> Brand | None:
        return self._db.query(Brand).filter_by(slug=slug).first()

    def get_or_create(self, slug: str, name: str, base_url: str) -> Brand:
        brand = self.get_by_slug(slug)
        if not brand:
            brand = Brand(slug=slug, name=name, base_url=base_url)
            self._db.add(brand)
            self._db.flush()
            log.info("Marque créée", slug=slug)
        return brand

    def list_active(self) -> list[Brand]:
        return self._db.query(Brand).filter_by(active=True).all()


# ---------------------------------------------------------------------------
# ProductRepository
# ---------------------------------------------------------------------------

class ProductRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def save(self, product_data: dict[str, Any]) -> Product:
        brand_id    = product_data["brand_id"]
        external_id = product_data["external_id"]
        existing    = self.find_by_external_id(brand_id, external_id)
        now = datetime.utcnow()

        if existing:
            updatable = (
                "name", "url", "category_raw", "family", "subfamily",
                "compression_level", "target_zones", "is_active",
                "classification_manual_review", "is_best_seller",
                "best_seller_first_seen", "best_seller_last_seen",
                "rating", "review_count",
                "material_main", "material_lining",
                "material_composition_json", "material_raw",
                "removed_at", "back_in_stock_at",
                "reviews_text_json",
            )
            for field in updatable:
                if field in product_data:
                    setattr(existing, field, product_data[field])
            existing.last_seen = now
            self._db.flush()
            return existing
        else:
            allowed = {k for k in product_data if hasattr(Product, k)}
            product = Product(**{k: product_data[k] for k in allowed})
            product.first_seen = now
            product.last_seen  = now
            self._db.add(product)
            self._db.flush()
            return product

    def find_by_external_id(self, brand_id: int, external_id: str) -> Product | None:
        return self._db.query(Product).filter_by(
            brand_id=brand_id, external_id=external_id
        ).first()

    def find_active_by_brand(self, brand_id: int) -> list[Product]:
        return self._db.query(Product).filter_by(brand_id=brand_id, is_active=True).all()

    def mark_as_removed(self, product_ids: list[int]) -> None:
        if not product_ids:
            return
        now = datetime.utcnow()
        self._db.query(Product).filter(Product.id.in_(product_ids)).update(
            {"is_active": False, "removed_at": now}, synchronize_session=False
        )

    def mark_back_in_stock(self, product_id: int) -> None:
        now = datetime.utcnow()
        self._db.query(Product).filter_by(id=product_id).update(
            {"is_active": True, "back_in_stock_at": now, "removed_at": None},
            synchronize_session=False,
        )

    def count_by_brand(self, brand_id: int) -> int:
        return self._db.query(Product).filter_by(brand_id=brand_id, is_active=True).count()


# ---------------------------------------------------------------------------
# VariantRepository  (gestion cycle de vie)
# ---------------------------------------------------------------------------

class VariantRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def sync_variants(self, product_id: int, incoming: list[dict]) -> list[dict]:
        """
        Synchronise les variantes d'un produit :
          - Crée les nouvelles variantes
          - Met à jour les existantes (dispo, prix, last_seen)
          - Marque les absentes comme supprimées (removed_at)
          - Détecte les retours en stock

        Retourne les événements de changement détectés :
          [{event_type, old_value, new_value, field_name}, ...]
        """
        now = datetime.utcnow()
        events: list[dict] = []

        # Index des variantes existantes par (color, size)
        existing_map: dict[tuple, Variant] = {
            (v.color, v.size): v
            for v in self._db.query(Variant).filter_by(product_id=product_id).all()
        }
        incoming_keys: set[tuple] = set()

        for inc in incoming:
            color = inc.get("color")
            size  = inc.get("size")
            key   = (color, size)
            incoming_keys.add(key)

            existing = existing_map.get(key)

            if existing is None:
                # Nouvelle variante
                variant = Variant(
                    product_id=product_id,
                    color=color,
                    size=size,
                    sku=inc.get("sku"),
                    price=inc.get("price"),
                    original_price=inc.get("original_price"),
                    on_sale=inc.get("on_sale", False),
                    available=inc.get("available", False),
                    first_seen=now,
                    last_seen=now,
                )
                self._db.add(variant)
                events.append({
                    "event_type": "variant.added",
                    "field_name": f"{color} / {size}",
                    "old_value": None,
                    "new_value": "added",
                })
            else:
                # Variante existante — détecter les changements
                new_avail = inc.get("available", False)

                # Retour en stock
                if not existing.available and new_avail and existing.removed_at:
                    existing.back_in_stock_at = now
                    existing.removed_at = None
                    events.append({
                        "event_type": "variant.back_in_stock",
                        "field_name": f"{color} / {size}",
                        "old_value": "unavailable",
                        "new_value": "in_stock",
                    })
                # Passage hors stock
                elif existing.available and not new_avail:
                    events.append({
                        "event_type": "availability.changed",
                        "field_name": f"{color} / {size}",
                        "old_value": "in_stock",
                        "new_value": "out_of_stock",
                    })

                # Mettre à jour
                existing.available      = new_avail
                existing.price          = inc.get("price", existing.price)
                existing.original_price = inc.get("original_price", existing.original_price)
                existing.on_sale        = inc.get("on_sale", existing.on_sale)
                existing.last_seen      = now
                existing.removed_at     = None   # réapparu

        # Variantes disparues
        for key, variant in existing_map.items():
            if key not in incoming_keys and variant.removed_at is None:
                variant.removed_at = now
                events.append({
                    "event_type": "variant.removed",
                    "field_name": f"{variant.color} / {variant.size}",
                    "old_value": "available" if variant.available else "unavailable",
                    "new_value": "removed",
                })

        self._db.flush()
        return events

    def get_by_product(self, product_id: int) -> list[Variant]:
        return self._db.query(Variant).filter_by(product_id=product_id).all()


# ---------------------------------------------------------------------------
# SnapshotRepository
# ---------------------------------------------------------------------------

class SnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def save(self, snapshot_data: dict[str, Any]) -> ProductSnapshot:
        snapshot = ProductSnapshot(**{
            k: v for k, v in snapshot_data.items() if hasattr(ProductSnapshot, k)
        })
        self._db.add(snapshot)
        self._db.flush()
        return snapshot

    def get_latest(self, product_id: int) -> ProductSnapshot | None:
        return (
            self._db.query(ProductSnapshot)
            .filter_by(product_id=product_id)
            .order_by(ProductSnapshot.crawled_at.desc())
            .first()
        )

    def get_price_history(self, product_id: int, days: int = 90) -> list[ProductSnapshot]:
        since = datetime.utcnow() - timedelta(days=days)
        return (
            self._db.query(ProductSnapshot)
            .filter(
                ProductSnapshot.product_id == product_id,
                ProductSnapshot.crawled_at >= since,
            )
            .order_by(ProductSnapshot.crawled_at.asc())
            .all()
        )


# ---------------------------------------------------------------------------
# ChangeEventRepository
# ---------------------------------------------------------------------------

class ChangeEventRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def save(self, event_data: dict[str, Any]) -> ChangeEvent:
        event = ChangeEvent(**{k: v for k, v in event_data.items() if hasattr(ChangeEvent, k)})
        self._db.add(event)
        self._db.flush()
        return event

    def get_recent(self, hours: int = 24) -> list[ChangeEvent]:
        since = datetime.utcnow() - timedelta(hours=hours)
        return (
            self._db.query(ChangeEvent)
            .filter(ChangeEvent.detected_at >= since)
            .order_by(ChangeEvent.detected_at.desc())
            .all()
        )

    def count_by_type(self, session_id: int) -> dict[str, int]:
        from sqlalchemy import func as sqlfunc
        rows = (
            self._db.query(ChangeEvent.event_type, sqlfunc.count(ChangeEvent.id))
            .filter_by(session_id=session_id)
            .group_by(ChangeEvent.event_type)
            .all()
        )
        return {r[0]: r[1] for r in rows}


# ---------------------------------------------------------------------------
# CrawlSessionRepository
# ---------------------------------------------------------------------------

class CrawlSessionRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def create(self, brand_id: int) -> CrawlSession:
        cs = CrawlSession(brand_id=brand_id, status="running")
        self._db.add(cs)
        self._db.flush()
        log.info("Session de crawl créée", brand_id=brand_id, session_id=cs.id)
        return cs

    def complete(self, session_id: int, stats: dict[str, Any]) -> None:
        cs = self._db.query(CrawlSession).filter_by(id=session_id).first()
        if cs:
            cs.status   = "completed"
            cs.ended_at = datetime.utcnow()
            for k, v in stats.items():
                if hasattr(cs, k):
                    setattr(cs, k, v)
            self._db.flush()

    def fail(self, session_id: int) -> None:
        cs = self._db.query(CrawlSession).filter_by(id=session_id).first()
        if cs:
            cs.status   = "failed"
            cs.ended_at = datetime.utcnow()
            self._db.flush()

    def get_last(self, brand_id: int) -> CrawlSession | None:
        return (
            self._db.query(CrawlSession)
            .filter_by(brand_id=brand_id, status="completed")
            .order_by(CrawlSession.started_at.desc())
            .first()
        )
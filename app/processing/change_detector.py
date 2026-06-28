"""
ChangeDetector v2 — détecte aussi best_seller.gained / lost.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.events import event_bus
from app.core.logger import get_logger
from app.processing.normalizer import NormalizedProduct
from app.storage.models import ProductSnapshot

log = get_logger(__name__)

PRICE_THRESHOLD = 0.01


@dataclass
class DetectedChange:
    event_type: str
    field_name: str | None
    old_value: Any
    new_value: Any
    detected_at: datetime = None

    def __post_init__(self):
        if self.detected_at is None:
            self.detected_at = datetime.utcnow()

    def to_dict(self) -> dict:
        return {
            "event_type":  self.event_type,
            "field_name":  self.field_name,
            "old_value":   str(self.old_value) if self.old_value is not None else None,
            "new_value":   str(self.new_value) if self.new_value is not None else None,
            "detected_at": self.detected_at,
        }


class ChangeDetector:

    def __init__(self, session_id: int | None = None) -> None:
        self._session_id = session_id

    def compare(
        self,
        product: NormalizedProduct,
        last_snapshot: ProductSnapshot | None,
        product_id: int | None = None,
    ) -> list[DetectedChange]:
        changes: list[DetectedChange] = []

        if last_snapshot is None:
            changes.append(DetectedChange("product.new", None, None, product.name))
            event_bus.emit("change.detected", change_type="product.new",
                           product_id=product_id, brand=product.brand_slug,
                           name=product.name, session_id=self._session_id)
            return changes

        # Prix
        if self._price_changed(product.price, last_snapshot.price):
            changes.append(DetectedChange("price.changed", "price",
                                          last_snapshot.price, product.price))
            event_bus.emit("change.detected", change_type="price.changed",
                           product_id=product_id, session_id=self._session_id)

        # Promotion
        if not last_snapshot.on_sale and product.on_sale:
            changes.append(DetectedChange("sale.started", "on_sale", False, True))
            event_bus.emit("change.detected", change_type="sale.started",
                           product_id=product_id, session_id=self._session_id)
        elif last_snapshot.on_sale and not product.on_sale:
            changes.append(DetectedChange("sale.ended", "on_sale", True, False))
            event_bus.emit("change.detected", change_type="sale.ended",
                           product_id=product_id, session_id=self._session_id)

        # Disponibilité
        if product.availability != last_snapshot.availability:
            changes.append(DetectedChange("availability.changed", "availability",
                                          last_snapshot.availability, product.availability))

        # Best Seller
        was_bs = getattr(last_snapshot, "is_best_seller", False)
        is_bs  = product.is_best_seller
        if not was_bs and is_bs:
            changes.append(DetectedChange("best_seller.gained", "is_best_seller", False, True))
            event_bus.emit("change.detected", change_type="best_seller.gained",
                           product_id=product_id, session_id=self._session_id)
        elif was_bs and not is_bs:
            changes.append(DetectedChange("best_seller.lost", "is_best_seller", True, False))
            event_bus.emit("change.detected", change_type="best_seller.lost",
                           product_id=product_id, session_id=self._session_id)

        return changes

    def detect_removals(self, active_ids: list[int], crawled_ids: list[int]) -> list[int]:
        crawled_set = set(crawled_ids)
        return [pid for pid in active_ids if pid not in crawled_set]

    def _price_changed(self, new, old) -> bool:
        if new is None and old is None: return False
        if new is None or old is None:  return True
        return abs(new - old) >= PRICE_THRESHOLD
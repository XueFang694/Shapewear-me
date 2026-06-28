"""
Tests unitaires — ChangeDetector.

Couvre :
- Détection nouveau produit (last_snapshot = None)
- Détection changement de prix
- Détection début / fin de promotion
- Détection changement de disponibilité
- Aucun changement si rien ne diffère
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from app.processing.change_detector import ChangeDetector, DetectedChange
from app.storage.models import ProductSnapshot
from tests.fixtures.sample_products import NORMALIZED_BODYSUIT, make_normalized_product


def make_snapshot(
    price: float = 68.00,
    original_price: float | None = None,
    on_sale: bool = False,
    availability: str = "in_stock",
    product_id: int = 1,
    session_id: int = 1,
) -> ProductSnapshot:
    snapshot = ProductSnapshot()
    snapshot.product_id = product_id
    snapshot.session_id = session_id
    snapshot.price = price
    snapshot.original_price = original_price
    snapshot.on_sale = on_sale
    snapshot.availability = availability
    snapshot.crawled_at = datetime(2026, 6, 25, 12, 0, 0)
    return snapshot


@pytest.fixture
def detector() -> ChangeDetector:
    return ChangeDetector(session_id=1)


# ---------------------------------------------------------------------------
# Nouveau produit
# ---------------------------------------------------------------------------

class TestNewProduct:
    def test_new_product_detected(self, detector):
        changes = detector.compare(NORMALIZED_BODYSUIT, last_snapshot=None)
        assert len(changes) == 1
        assert changes[0].event_type == "product.new"

    def test_new_product_has_no_old_value(self, detector):
        changes = detector.compare(NORMALIZED_BODYSUIT, last_snapshot=None)
        assert changes[0].old_value is None

    def test_new_product_new_value_is_name(self, detector):
        changes = detector.compare(NORMALIZED_BODYSUIT, last_snapshot=None)
        assert changes[0].new_value == NORMALIZED_BODYSUIT.name


# ---------------------------------------------------------------------------
# Changement de prix
# ---------------------------------------------------------------------------

class TestPriceChange:
    def test_price_increase_detected(self, detector):
        product = make_normalized_product(price=78.00)
        snapshot = make_snapshot(price=68.00)
        changes = detector.compare(product, snapshot)
        price_changes = [c for c in changes if c.event_type == "price.changed"]
        assert len(price_changes) == 1
        # to_dict() convertit en str, mais old_value brut est float
        d = price_changes[0].to_dict()
        assert d["old_value"] == "68.0"
        assert d["new_value"] == "78.0"

    def test_price_decrease_detected(self, detector):
        product = make_normalized_product(price=55.00)
        snapshot = make_snapshot(price=68.00)
        changes = detector.compare(product, snapshot)
        price_changes = [c for c in changes if c.event_type == "price.changed"]
        assert len(price_changes) == 1

    def test_same_price_no_change(self, detector):
        product = make_normalized_product(price=68.00)
        snapshot = make_snapshot(price=68.00)
        changes = detector.compare(product, snapshot)
        price_changes = [c for c in changes if c.event_type == "price.changed"]
        assert len(price_changes) == 0

    def test_tiny_price_difference_ignored(self, detector):
        """Différence inférieure au seuil (0.01) ne doit pas déclencher d'alerte."""
        product = make_normalized_product(price=68.001)
        snapshot = make_snapshot(price=68.00)
        changes = detector.compare(product, snapshot)
        price_changes = [c for c in changes if c.event_type == "price.changed"]
        assert len(price_changes) == 0

    def test_price_none_to_value_detected(self, detector):
        product = make_normalized_product(price=68.00)
        snapshot = make_snapshot(price=None)
        changes = detector.compare(product, snapshot)
        price_changes = [c for c in changes if c.event_type == "price.changed"]
        assert len(price_changes) == 1

    def test_price_field_name_set(self, detector):
        product = make_normalized_product(price=78.00)
        snapshot = make_snapshot(price=68.00)
        changes = detector.compare(product, snapshot)
        price_changes = [c for c in changes if c.event_type == "price.changed"]
        assert price_changes[0].field_name == "price"


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

class TestSaleDetection:
    def test_sale_started_detected(self, detector):
        product = make_normalized_product(price=45.00, on_sale=True)
        snapshot = make_snapshot(on_sale=False)
        changes = detector.compare(product, snapshot)
        sale_changes = [c for c in changes if c.event_type == "sale.started"]
        assert len(sale_changes) == 1

    def test_sale_ended_detected(self, detector):
        product = make_normalized_product(price=68.00, on_sale=False)
        snapshot = make_snapshot(on_sale=True)
        changes = detector.compare(product, snapshot)
        sale_end = [c for c in changes if c.event_type == "sale.ended"]
        assert len(sale_end) == 1

    def test_no_sale_change_when_stable(self, detector):
        product = make_normalized_product(on_sale=False)
        snapshot = make_snapshot(on_sale=False)
        changes = detector.compare(product, snapshot)
        sale_events = [c for c in changes if "sale" in c.event_type]
        assert len(sale_events) == 0

    def test_sale_field_name(self, detector):
        product = make_normalized_product(price=45.00, on_sale=True)
        snapshot = make_snapshot(on_sale=False)
        changes = detector.compare(product, snapshot)
        sale_changes = [c for c in changes if c.event_type == "sale.started"]
        assert sale_changes[0].field_name == "on_sale"


# ---------------------------------------------------------------------------
# Disponibilité
# ---------------------------------------------------------------------------

class TestAvailabilityChange:
    def test_in_stock_to_out_detected(self, detector):
        product = make_normalized_product(availability="out_of_stock")
        snapshot = make_snapshot(availability="in_stock")
        changes = detector.compare(product, snapshot)
        avail = [c for c in changes if c.event_type == "availability.changed"]
        assert len(avail) == 1
        assert avail[0].old_value == "in_stock"
        assert avail[0].new_value == "out_of_stock"

    def test_same_availability_no_change(self, detector):
        product = make_normalized_product(availability="in_stock")
        snapshot = make_snapshot(availability="in_stock")
        changes = detector.compare(product, snapshot)
        avail = [c for c in changes if c.event_type == "availability.changed"]
        assert len(avail) == 0


# ---------------------------------------------------------------------------
# Détection des suppressions
# ---------------------------------------------------------------------------

class TestRemovalDetection:
    def test_detects_missing_products(self, detector):
        active = ["prod-001", "prod-002", "prod-003"]
        crawled = ["prod-001", "prod-003"]
        removed = detector.detect_removals(active, crawled)
        assert "prod-002" in removed
        assert "prod-001" not in removed

    def test_no_removals_when_all_present(self, detector):
        active = ["prod-001", "prod-002"]
        crawled = ["prod-001", "prod-002", "prod-003"]
        removed = detector.detect_removals(active, crawled)
        assert len(removed) == 0

    def test_empty_crawl_marks_all_removed(self, detector):
        active = ["prod-001", "prod-002"]
        removed = detector.detect_removals(active, [])
        assert set(removed) == {"prod-001", "prod-002"}


# ---------------------------------------------------------------------------
# Aucun changement
# ---------------------------------------------------------------------------

class TestNoChange:
    def test_no_changes_when_identical(self, detector):
        product = make_normalized_product(
            price=68.00,
            on_sale=False,
            availability="in_stock",
        )
        snapshot = make_snapshot(
            price=68.00,
            on_sale=False,
            availability="in_stock",
        )
        changes = detector.compare(product, snapshot)
        assert len(changes) == 0

    def test_multiple_changes_in_one_comparison(self, detector):
        """Prix + promotion peuvent changer en même temps."""
        product = make_normalized_product(price=45.00, on_sale=True)
        snapshot = make_snapshot(price=68.00, on_sale=False)
        changes = detector.compare(product, snapshot)
        event_types = {c.event_type for c in changes}
        assert "price.changed" in event_types
        assert "sale.started" in event_types


# ---------------------------------------------------------------------------
# DetectedChange dataclass
# ---------------------------------------------------------------------------

class TestDetectedChangeDataclass:
    def test_to_dict_serializable(self, detector):
        change = DetectedChange(
            event_type="price.changed",
            field_name="price",
            old_value=68.0,
            new_value=45.0,
        )
        d = change.to_dict()
        assert d["event_type"] == "price.changed"
        assert d["old_value"] == "68.0"
        assert d["new_value"] == "45.0"
        assert d["detected_at"] is not None

    def test_detected_at_auto_set(self):
        change = DetectedChange(
            event_type="product.new",
            field_name=None,
            old_value=None,
            new_value="Test",
        )
        assert change.detected_at is not None
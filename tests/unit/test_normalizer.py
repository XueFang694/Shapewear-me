"""
Tests unitaires — Normalizer.

Couvre :
- Nettoyage des prix ($, virgules)
- Calcul de on_sale et discount_pct
- Nettoyage du HTML dans la description
- Validation des champs obligatoires
- Normalisation des couleurs et tailles
"""
from __future__ import annotations

import pytest

from app.processing.normalizer import Normalizer, NormalizedProduct
from app.core.exceptions import NormalizationError
from tests.fixtures.sample_products import (
    RAW_PRODUCT_BODYSUIT,
    RAW_PRODUCT_ON_SALE,
    RAW_PRODUCT_MINIMAL,
    RAW_PRODUCT_HTML_DESCRIPTION,
    RAW_PRODUCT_DIRTY_PRICE,
    make_raw_product,
)


@pytest.fixture
def normalizer() -> Normalizer:
    return Normalizer()


# ---------------------------------------------------------------------------
# Tests de base
# ---------------------------------------------------------------------------

class TestNormalizerBasic:
    def test_normalizes_bodysuit(self, normalizer):
        result = normalizer.process(RAW_PRODUCT_BODYSUIT)
        assert isinstance(result, NormalizedProduct)
        assert result.name == "Mid-Thigh Bodysuit"
        assert result.brand_slug == "spanx"
        assert result.external_id == "spanx-001"
        assert result.price == 68.00
        assert result.currency == "USD"

    def test_normalizes_minimal_product(self, normalizer):
        """Un produit avec seulement les champs obligatoires doit être accepté."""
        result = normalizer.process(RAW_PRODUCT_MINIMAL)
        assert result.name == "Basic Shaper"
        assert result.price is None

    def test_strips_whitespace_from_name(self, normalizer):
        raw = make_raw_product(name="  Padded Bodysuit  ")
        result = normalizer.process(raw)
        assert result.name == "Padded Bodysuit"

    def test_lowercases_brand_slug(self, normalizer):
        raw = make_raw_product(brand_slug="SPANX")
        result = normalizer.process(raw)
        assert result.brand_slug == "spanx"


# ---------------------------------------------------------------------------
# Tests de prix
# ---------------------------------------------------------------------------

class TestPriceNormalization:
    def test_float_price_preserved(self, normalizer):
        raw = make_raw_product(price=68.00)
        result = normalizer.process(raw)
        assert result.price == 68.00

    def test_price_rounded_to_2_decimals(self, normalizer):
        raw = make_raw_product(price=68.999)
        result = normalizer.process(raw)
        assert result.price == 69.00

    def test_none_price_accepted(self, normalizer):
        result = normalizer.process(RAW_PRODUCT_DIRTY_PRICE)
        assert result.price is None

    def test_on_sale_computed_from_prices(self, normalizer):
        raw = make_raw_product(price=45.00, original_price=68.00)
        result = normalizer.process(raw)
        assert result.on_sale is True
        assert result.discount_pct == pytest.approx(33.8, abs=0.5)

    def test_no_sale_when_prices_equal(self, normalizer):
        raw = make_raw_product(price=68.00, original_price=68.00)
        result = normalizer.process(raw)
        assert result.on_sale is False
        assert result.discount_pct is None

    def test_no_sale_when_original_lower(self, normalizer):
        """original_price < price ne doit pas déclencher on_sale."""
        raw = make_raw_product(price=68.00, original_price=50.00)
        result = normalizer.process(raw)
        assert result.on_sale is False

    def test_on_sale_fixture(self, normalizer):
        result = normalizer.process(RAW_PRODUCT_ON_SALE)
        assert result.on_sale is True
        assert result.price == 45.00
        assert result.original_price == 68.00


# ---------------------------------------------------------------------------
# Tests de nettoyage HTML
# ---------------------------------------------------------------------------

class TestHtmlCleaning:
    def test_strips_html_tags(self, normalizer):
        result = normalizer.process(RAW_PRODUCT_HTML_DESCRIPTION)
        assert "<" not in result.description
        assert ">" not in result.description

    def test_keeps_text_content(self, normalizer):
        result = normalizer.process(RAW_PRODUCT_HTML_DESCRIPTION)
        assert "tummy control" in result.description.lower()

    def test_none_description_stays_none(self, normalizer):
        raw = make_raw_product(description=None)
        result = normalizer.process(raw)
        assert result.description is None

    def test_empty_description_becomes_none(self, normalizer):
        raw = make_raw_product(description="   ")
        result = normalizer.process(raw)
        assert result.description is None


# ---------------------------------------------------------------------------
# Tests de validation des champs obligatoires
# ---------------------------------------------------------------------------

class TestValidation:
    def test_raises_when_external_id_missing(self, normalizer):
        from app.connectors.base import RawProduct
        raw = RawProduct(
            external_id="",
            url="https://example.com",
            name="Test",
            brand_slug="spanx",
        )
        with pytest.raises(NormalizationError, match="external_id"):
            normalizer.process(raw)

    def test_raises_when_url_missing(self, normalizer):
        from app.connectors.base import RawProduct
        raw = RawProduct(
            external_id="test-001",
            url="",
            name="Test",
            brand_slug="spanx",
        )
        with pytest.raises(NormalizationError, match="url"):
            normalizer.process(raw)

    def test_raises_when_name_missing(self, normalizer):
        from app.connectors.base import RawProduct
        raw = RawProduct(
            external_id="test-001",
            url="https://example.com",
            name="",
            brand_slug="spanx",
        )
        with pytest.raises(NormalizationError, match="name"):
            normalizer.process(raw)

    def test_raises_when_brand_missing(self, normalizer):
        from app.connectors.base import RawProduct
        raw = RawProduct(
            external_id="test-001",
            url="https://example.com",
            name="Test",
            brand_slug="",
        )
        with pytest.raises(NormalizationError, match="brand_slug"):
            normalizer.process(raw)


# ---------------------------------------------------------------------------
# Tests de normalisation des tailles et couleurs
# ---------------------------------------------------------------------------

class TestSizesAndColors:
    def test_sizes_passed_through(self, normalizer):
        raw = make_raw_product(sizes=["XS", "S", "M", "L", "XL"])
        result = normalizer.process(raw)
        assert "S" in result.sizes
        assert "M" in result.sizes

    def test_empty_sizes_gives_empty_list(self, normalizer):
        from app.connectors.base import RawProduct
        raw = RawProduct(
            external_id="test-empty-sizes",
            url="https://example.com/p",
            name="No Sizes Product",
            brand_slug="spanx",
            sizes=[],
        )
        result = normalizer.process(raw)
        assert result.sizes == []

    def test_colors_have_canonical_name(self, normalizer):
        raw = make_raw_product(colors=[{"name": "black", "available": True}])
        result = normalizer.process(raw)
        assert len(result.colors) == 1
        assert "canonical_name" in result.colors[0]
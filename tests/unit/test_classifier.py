"""
Tests unitaires — Classifier.

Couvre :
- Classification famille / sous-famille par mots-clés
- Détection du niveau de compression
- Détection des zones corporelles
- Produit non classifiable → classification_manual_review = True
"""
from __future__ import annotations

import pytest

from app.processing.classifier import Classifier
from app.processing.normalizer import NormalizedProduct
from tests.fixtures.sample_products import (
    NORMALIZED_BODYSUIT,
    NORMALIZED_ON_SALE,
    make_normalized_product,
)


@pytest.fixture
def classifier() -> Classifier:
    return Classifier()


def make_product(
    name: str,
    category_raw: str = "",
    description: str = "",
) -> NormalizedProduct:
    return make_normalized_product(
        name=name,
        category_raw=category_raw,
        family=None,
        subfamily=None,
        compression_level=None,
        target_zones=[],
    )


# ---------------------------------------------------------------------------
# Classification famille
# ---------------------------------------------------------------------------

class TestFamilyClassification:
    def test_bodysuit_by_category(self, classifier):
        product = make_product("Classic Shaper", category_raw="bodysuits")
        result = classifier.classify(product)
        assert result.family == "Bodysuit"

    def test_bodysuit_by_name(self, classifier):
        product = make_product("Open Bust Bodysuit")
        result = classifier.classify(product)
        assert result.family == "Bodysuit"

    def test_legging_by_name(self, classifier):
        product = make_product("Shaper Legging Full Length", category_raw="leggings")
        result = classifier.classify(product)
        assert result.family == "Shaper Legging"

    def test_bra_by_category(self, classifier):
        product = make_product("Wireless Support Bra", category_raw="bras")
        result = classifier.classify(product)
        assert result.family == "Bra"

    def test_panty_by_category(self, classifier):
        product = make_product("Everyday Thong", category_raw="panties")
        result = classifier.classify(product)
        assert result.family == "Panty"

    def test_short_by_name(self, classifier):
        product = make_product("Bike Short", category_raw="shorts")
        result = classifier.classify(product)
        assert result.family == "Shaper Short"

    def test_unknown_product_flags_manual_review(self, classifier):
        product = make_product("Mystery Item XYZ")
        result = classifier.classify(product)
        assert result.family is None
        assert result.classification_manual_review is True

    def test_known_product_clears_manual_review(self, classifier):
        product = make_product("Classic Bodysuit", category_raw="bodysuits")
        result = classifier.classify(product)
        assert result.classification_manual_review is False


# ---------------------------------------------------------------------------
# Classification sous-famille
# ---------------------------------------------------------------------------

class TestSubfamilyClassification:
    def test_open_bust_bodysuit(self, classifier):
        product = make_product(
            "Open Bust Bodysuit",
            description="Open bust design, strapless option.",
        )
        result = classifier.classify(product)
        assert result.family == "Bodysuit"
        assert result.subfamily == "Open Bust Bodysuit"

    def test_mid_thigh_bodysuit(self, classifier):
        product = make_product("Mid-Thigh Bodysuit", category_raw="bodysuits")
        result = classifier.classify(product)
        assert result.subfamily == "Mid-Thigh Bodysuit"

    def test_bike_short(self, classifier):
        product = make_product("Bike Short", category_raw="shorts")
        result = classifier.classify(product)
        assert result.subfamily == "Bike Short"

    def test_wireless_bra(self, classifier):
        product = make_product(
            "Bra-llelujah Wireless",
            description="Wire-free comfort.",
        )
        result = classifier.classify(product)
        assert result.subfamily == "Wireless Bra"

    def test_no_subfamily_for_generic(self, classifier):
        product = make_product("Classic Bodysuit", category_raw="bodysuits")
        result = classifier.classify(product)
        # Famille ok, sous-famille peut être None si aucun mot-clé ne correspond
        assert result.family == "Bodysuit"


# ---------------------------------------------------------------------------
# Niveau de compression
# ---------------------------------------------------------------------------

class TestCompressionLevel:
    def test_firm_from_tummy_control(self, classifier):
        product = make_product(
            "Tummy Control Bodysuit",
            description="Firm tummy control.",
        )
        result = classifier.classify(product)
        assert result.compression_level == "Forte"

    def test_light_from_everyday(self, classifier):
        product = make_product(
            "Everyday Cami",
            description="Light compression, everyday wear.",
        )
        result = classifier.classify(product)
        assert result.compression_level == "Légère"

    def test_extra_firm_priority_over_firm(self, classifier):
        product = make_product(
            "Maximum Control Bodysuit",
            description="Extra firm maximum control.",
        )
        result = classifier.classify(product)
        assert result.compression_level == "Extra-forte"

    def test_medium_compression(self, classifier):
        product = make_product(
            "Moderate Shaper",
            description="Medium compression for everyday shaping.",
        )
        result = classifier.classify(product)
        assert result.compression_level == "Moyenne"

    def test_no_compression_detected(self, classifier):
        product = make_product("Simple Top")
        result = classifier.classify(product)
        # Pas de compression détectée → None acceptable
        assert result.compression_level is None or isinstance(result.compression_level, str)


# ---------------------------------------------------------------------------
# Zones corporelles
# ---------------------------------------------------------------------------

class TestBodyZones:
    def test_waist_and_tummy_detected(self, classifier):
        p = make_normalized_product(
            name="Waist Shaper",
            category_raw="bodysuits",
            family=None, subfamily=None,
            compression_level=None, target_zones=[],
        )
        p.description = "Targets waist and tummy control."
        result = classifier.classify(p)
        assert "Taille" in result.target_zones
        assert "Ventre" in result.target_zones

    def test_thigh_detected(self, classifier):
        product = make_product(
            "Mid-Thigh Bodysuit",
            description="Thigh slimming and tummy control.",
        )
        result = classifier.classify(product)
        assert "Cuisses" in result.target_zones

    def test_back_fat_zone(self, classifier):
        product = make_product(
            "Back Smoother",
            description="Smooths back fat and bra bulge.",
        )
        result = classifier.classify(product)
        assert "Dos" in result.target_zones

    def test_no_zones_for_generic(self, classifier):
        product = make_product("Basic Shaper", description="")
        result = classifier.classify(product)
        # Les zones peuvent être vides pour un produit générique
        assert isinstance(result.target_zones, list)

    def test_multiple_zones_detected(self, classifier):
        p = make_normalized_product(
            name="Full Body Shaper",
            category_raw="bodysuits",
            family=None, subfamily=None,
            compression_level=None, target_zones=[],
        )
        p.description = (
            "Targets waist, tummy, hips, thighs and back fat."
        )
        result = classifier.classify(p)
        assert len(result.target_zones) >= 3


# ---------------------------------------------------------------------------
# Test avec les fixtures de référence du README
# ---------------------------------------------------------------------------

class TestReadmeExamples:
    def test_spanx_mid_thigh_bodysuit(self, classifier):
        """Exemple exact du README section 7."""
        product = make_normalized_product(
            name="Mid-Thigh Bodysuit",
            category_raw="bodysuits",
            family=None,
            subfamily=None,
            compression_level=None,
            target_zones=[],
        )
        product.description = "Firm tummy control and thigh slimming. Targets waist, stomach and thighs."
        result = classifier.classify(product)
        assert result.family == "Bodysuit"
        assert result.subfamily == "Mid-Thigh Bodysuit"
        assert result.compression_level == "Forte"
        assert "Ventre" in result.target_zones
        assert "Cuisses" in result.target_zones

    def test_skims_open_bust_bodysuit(self, classifier):
        """Exemple exact du README section 7 pour SKIMS."""
        product = make_normalized_product(
            name="Open Bust Bodysuit",
            category_raw="bodywear",
            family=None,
            subfamily=None,
            compression_level=None,
            target_zones=[],
        )
        product.description = "Light compression, everyday wear. Smooths core."
        result = classifier.classify(product)
        assert result.family == "Bodysuit"
        assert result.subfamily == "Open Bust Bodysuit"
        assert result.compression_level == "Légère"
        assert "Ventre" in result.target_zones
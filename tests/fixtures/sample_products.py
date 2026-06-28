"""
Fixtures de test — Produits et données simulées pour les tests unitaires.
"""
from __future__ import annotations

from datetime import datetime

from app.connectors.base import RawProduct
from app.processing.normalizer import NormalizedProduct


# ---------------------------------------------------------------------------
# RawProducts de test
# ---------------------------------------------------------------------------

def make_raw_product(
    external_id: str = "prod-001",
    url: str = "https://www.spanx.com/products/mid-thigh-bodysuit.json",
    name: str = "Mid-Thigh Bodysuit",
    brand_slug: str = "spanx",
    price: float = 68.00,
    original_price: float | None = None,
    on_sale: bool = False,
    category_raw: str = "bodysuits",
    description: str = "Firm tummy control and thigh slimming. Targets waist, stomach and thighs.",
    sizes: list[str] | None = None,
    colors: list[dict] | None = None,
    availability: str = "in_stock",
    **kwargs,
) -> RawProduct:
    return RawProduct(
        external_id=external_id,
        url=url,
        name=name,
        brand_slug=brand_slug,
        price=price,
        original_price=original_price,
        on_sale=on_sale,
        category_raw=category_raw,
        description=description,
        sizes=sizes or ["XS", "S", "M", "L", "XL"],
        colors=colors or [{"name": "Black", "available": True}],
        availability=availability,
        crawled_at=datetime(2026, 6, 26, 12, 0, 0),
        **kwargs,
    )


RAW_PRODUCT_BODYSUIT = make_raw_product(
    external_id="spanx-001",
    name="Mid-Thigh Bodysuit",
    category_raw="bodysuits",
    description="Firm tummy control and thigh slimming. Targets waist, stomach and thighs.",
    price=68.00,
)

RAW_PRODUCT_ON_SALE = make_raw_product(
    external_id="spanx-002",
    name="Open Bust Bodysuit",
    category_raw="bodysuits",
    description="Light, everyday shaping. Smooths core.",
    price=45.00,
    original_price=68.00,
    on_sale=True,
)

RAW_PRODUCT_LEGGING = make_raw_product(
    external_id="spanx-003",
    name="Shaper Legging Full Length",
    category_raw="leggings",
    description="Medium compression legging for everyday wear.",
    price=88.00,
)

RAW_PRODUCT_BRA = make_raw_product(
    external_id="spanx-004",
    name="Bra-llelujah! Wireless Bra",
    category_raw="bras",
    description="Wire-free comfort with light compression.",
    price=58.00,
)

RAW_PRODUCT_MINIMAL = RawProduct(
    external_id="spanx-005",
    url="https://www.spanx.com/products/minimal.json",
    name="Basic Shaper",
    brand_slug="spanx",
)

RAW_PRODUCT_HTML_DESCRIPTION = make_raw_product(
    external_id="spanx-006",
    name="Sculpting Bodysuit",
    description="<p>Firm <strong>tummy control</strong>.</p><ul><li>Seamless</li></ul>",
)

RAW_PRODUCT_DIRTY_PRICE = RawProduct(
    external_id="spanx-007",
    url="https://www.spanx.com/products/test.json",
    name="Test Shaper",
    brand_slug="spanx",
    price=None,   # prix manquant
    category_raw="bodysuits",
)


# ---------------------------------------------------------------------------
# NormalizedProducts de test
# ---------------------------------------------------------------------------

def make_normalized_product(
    external_id: str = "prod-001",
    name: str = "Mid-Thigh Bodysuit",
    brand_slug: str = "spanx",
    price: float = 68.00,
    original_price: float | None = None,
    on_sale: bool = False,
    discount_pct: float | None = None,
    category_raw: str = "bodysuits",
    family: str = "Bodysuit",
    subfamily: str = "Mid-Thigh Bodysuit",
    compression_level: str = "Forte",
    target_zones: list[str] | None = None,
    availability: str = "in_stock",
) -> NormalizedProduct:
    return NormalizedProduct(
        external_id=external_id,
        url=f"https://www.spanx.com/products/{external_id}",
        name=name,
        brand_slug=brand_slug,
        price=price,
        original_price=original_price,
        on_sale=on_sale,
        discount_pct=discount_pct,
        category_raw=category_raw,
        family=family,
        subfamily=subfamily,
        compression_level=compression_level,
        target_zones=target_zones or ["Taille", "Ventre", "Cuisses"],
        availability=availability,
        crawled_at=datetime(2026, 6, 26, 12, 0, 0),
    )


NORMALIZED_BODYSUIT = make_normalized_product(
    external_id="spanx-001",
    name="Mid-Thigh Bodysuit",
    family="Bodysuit",
    subfamily="Mid-Thigh Bodysuit",
    compression_level="Forte",
    target_zones=["Taille", "Ventre", "Cuisses"],
)

NORMALIZED_ON_SALE = make_normalized_product(
    external_id="spanx-002",
    name="Open Bust Bodysuit",
    price=45.00,
    original_price=68.00,
    on_sale=True,
    discount_pct=33.8,
    family="Bodysuit",
    subfamily="Open Bust Bodysuit",
    compression_level="Légère",
    target_zones=["Ventre"],
)
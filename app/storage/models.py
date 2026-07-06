"""
Modèles SQLAlchemy — v2 (Phase 1.1)

Nouveautés :
  - Product : is_best_seller, best_seller_first_seen, best_seller_last_seen,
              removed_at, back_in_stock_at, material_*, rating, review_count
  - Variant  : price, original_price, on_sale (granularité couleur × taille)
               first_seen, last_seen, removed_at, back_in_stock_at
  - ProductSnapshot : is_best_seller
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index,
    Integer, String, Text, func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Brand
# ---------------------------------------------------------------------------

class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    connector_id: Mapped[str | None] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, server_default=func.now()
    )

    crawl_sessions: Mapped[list["CrawlSession"]] = relationship(
        "CrawlSession", back_populates="brand", cascade="all, delete-orphan"
    )
    products: Mapped[list["Product"]] = relationship(
        "Product", back_populates="brand", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"Brand(slug={self.slug!r})"


# ---------------------------------------------------------------------------
# CrawlSession
# ---------------------------------------------------------------------------

class CrawlSession(Base):
    __tablename__ = "crawl_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="running", nullable=False)

    products_found: Mapped[int] = mapped_column(Integer, default=0)
    products_new: Mapped[int] = mapped_column(Integer, default=0)
    products_changed: Mapped[int] = mapped_column(Integer, default=0)
    products_removed: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)

    brand: Mapped["Brand"] = relationship("Brand", back_populates="crawl_sessions")
    snapshots: Mapped[list["ProductSnapshot"]] = relationship(
        "ProductSnapshot", back_populates="session", cascade="all, delete-orphan"
    )
    change_events: Mapped[list["ChangeEvent"]] = relationship(
        "ChangeEvent", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"CrawlSession(id={self.id}, status={self.status!r})"


# ---------------------------------------------------------------------------
# Product
# ---------------------------------------------------------------------------

class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brand_id: Mapped[int] = mapped_column(ForeignKey("brands.id"), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)

    # Classification
    category_raw: Mapped[str | None] = mapped_column(String(256))
    family: Mapped[str | None] = mapped_column(String(128))
    subfamily: Mapped[str | None] = mapped_column(String(128))
    compression_level: Mapped[str | None] = mapped_column(String(64))
    target_zones: Mapped[str | None] = mapped_column(Text)  # JSON list

    # ── Statut & cycle de vie ──────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # Date approximative de disparition du site (= date du crawl où il n'a plus été vu)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Date de retour en stock après une absence
    back_in_stock_at: Mapped[datetime | None] = mapped_column(DateTime)

    # ── Best Seller ─────────────────────────────────────────────────────────
    is_best_seller: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    best_seller_first_seen: Mapped[datetime | None] = mapped_column(DateTime)
    best_seller_last_seen: Mapped[datetime | None] = mapped_column(DateTime)

    # ── Avis clients ────────────────────────────────────────────────────────
    rating: Mapped[float | None] = mapped_column(Float)
    review_count: Mapped[int | None] = mapped_column(Integer)
    # ── Avis clients (textes) ────────────────────────────────────────────────────
    # JSON : [{"rating": 4, "title": "...", "body": "...", "date": "...", "variant": "..."}, ...]
    reviews_text_json: Mapped[str | None] = mapped_column(Text)

    # ── Matériaux (colonnes distinctes) ─────────────────────────────────────
    # Composition principale (ex: "73% Nylon, 27% Elastane")
    material_main: Mapped[str | None] = mapped_column(String(256))
    # Doublure (ex: "100% Cotton")
    material_lining: Mapped[str | None] = mapped_column(String(256))
    # Pourcentages individuels normalisés (JSON : {"nylon": 73, "elastane": 27})
    material_composition_json: Mapped[str | None] = mapped_column(Text)
    # Texte brut complet de la composition (tel que sur le site)
    material_raw: Mapped[str | None] = mapped_column(Text)

    # ── Divers ──────────────────────────────────────────────────────────────
    classification_manual_review: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relations
    brand: Mapped["Brand"] = relationship("Brand", back_populates="products")
    snapshots: Mapped[list["ProductSnapshot"]] = relationship(
        "ProductSnapshot", back_populates="product", cascade="all, delete-orphan"
    )
    variants: Mapped[list["Variant"]] = relationship(
        "Variant", back_populates="product", cascade="all, delete-orphan"
    )
    change_events: Mapped[list["ChangeEvent"]] = relationship(
        "ChangeEvent", back_populates="product", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_product_brand_external", "brand_id", "external_id", unique=True),
        Index("ix_product_brand_family", "brand_id", "family"),
        Index("ix_product_brand_active", "brand_id", "is_active"),
        Index("ix_product_best_seller", "is_best_seller"),
    )

    @property
    def target_zones_list(self) -> list[str]:
        if not self.target_zones:
            return []
        try:
            return json.loads(self.target_zones)
        except (json.JSONDecodeError, TypeError):
            return []

    @target_zones_list.setter
    def target_zones_list(self, value: list[str]) -> None:
        self.target_zones = json.dumps(value) if value else None

    @property
    def material_composition(self) -> dict:
        if not self.material_composition_json:
            return {}
        try:
            return json.loads(self.material_composition_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    @material_composition.setter
    def material_composition(self, value: dict) -> None:
        self.material_composition_json = json.dumps(value) if value else None

    def __repr__(self) -> str:
        return f"Product(id={self.id}, name={self.name!r})"


# ---------------------------------------------------------------------------
# ProductSnapshot
# ---------------------------------------------------------------------------

class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("crawl_sessions.id"), nullable=False, index=True)

    price: Mapped[float | None] = mapped_column(Float)
    original_price: Mapped[float | None] = mapped_column(Float)
    on_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    discount_pct: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    availability: Mapped[str] = mapped_column(String(32), default="unknown", nullable=False)
    is_best_seller: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    crawled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    product: Mapped["Product"] = relationship("Product", back_populates="snapshots")
    session: Mapped["CrawlSession"] = relationship("CrawlSession", back_populates="snapshots")

    __table_args__ = (
        Index("ix_snapshot_product_date", "product_id", "crawled_at"),
    )

    def __repr__(self) -> str:
        return f"ProductSnapshot(product_id={self.product_id}, price={self.price})"


# ---------------------------------------------------------------------------
# Variant  (granularité couleur × taille)
# ---------------------------------------------------------------------------

class Variant(Base):
    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)

    color: Mapped[str | None] = mapped_column(String(128))
    color_canonical: Mapped[str | None] = mapped_column(String(128))
    size: Mapped[str | None] = mapped_column(String(64))
    sku: Mapped[str | None] = mapped_column(String(128))

    # Prix propre à la variante (peut différer du prix produit pour les soldes)
    price: Mapped[float | None] = mapped_column(Float)
    original_price: Mapped[float | None] = mapped_column(Float)
    on_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Disponibilité granulaire
    available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Cycle de vie de la variante
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    # Date où la variante a disparu (taille/couleur retirée)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Date de retour en stock de la variante
    back_in_stock_at: Mapped[datetime | None] = mapped_column(DateTime)

    product: Mapped["Product"] = relationship("Product", back_populates="variants")

    __table_args__ = (
        Index("ix_variant_product_color_size", "product_id", "color", "size"),
        Index("ix_variant_available", "product_id", "available"),
    )

    def __repr__(self) -> str:
        return f"Variant(product_id={self.product_id}, color={self.color!r}, size={self.size!r}, available={self.available})"


# ---------------------------------------------------------------------------
# ChangeEvent
# ---------------------------------------------------------------------------

class ChangeEvent(Base):
    __tablename__ = "change_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("crawl_sessions.id"), nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # product.new | product.removed | product.back_in_stock
    # price.changed | sale.started | sale.ended
    # best_seller.gained | best_seller.lost
    # availability.changed | variant.added | variant.removed | variant.back_in_stock

    field_name: Mapped[str | None] = mapped_column(String(128))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    product: Mapped["Product"] = relationship("Product", back_populates="change_events")
    session: Mapped["CrawlSession"] = relationship("CrawlSession", back_populates="change_events")

    __table_args__ = (
        Index("ix_change_type_date", "event_type", "detected_at"),
    )

    def __repr__(self) -> str:
        return f"ChangeEvent(type={self.event_type!r}, product_id={self.product_id})"
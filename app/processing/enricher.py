"""
Enricher — Calcule les champs dérivés à partir de l'historique des snapshots.

Métriques produites :
  - promo_frequency_pct   : % des crawls où le produit était en promotion
  - price_min / price_max : prix min et max observés
  - price_avg             : prix moyen sur la période
  - price_stability       : coefficient de variation (0 = stable, >0.2 = volatile)
  - days_since_first_seen : ancienneté du produit en jours
  - days_on_sale          : nombre total de jours détectés en promo
  - avg_discount_pct      : remise moyenne quand en promo

L'Enricher est appelé par le runner après le stockage du snapshot.
Il met à jour les champs calculés sur le produit en base.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from app.core.logger import get_logger

if TYPE_CHECKING:
    from app.storage.models import Product, ProductSnapshot

log = get_logger(__name__)


@dataclass
class EnrichedMetrics:
    """Métriques dérivées calculées pour un produit."""
    promo_frequency_pct: float | None = None
    price_min: float | None = None
    price_max: float | None = None
    price_avg: float | None = None
    price_stability: float | None = None
    days_since_first_seen: int | None = None
    days_on_sale: int | None = None
    avg_discount_pct: float | None = None


class Enricher:
    """Enrichit un produit avec des métriques dérivées de l'historique."""

    def __init__(self, lookback_days: int = 90) -> None:
        self._lookback_days = lookback_days

    def compute(
        self,
        product: "Product",
        snapshots: list["ProductSnapshot"],
    ) -> EnrichedMetrics:
        """
        Calcule les métriques dérivées depuis la liste de snapshots.

        Args:
            product:   Entité Product SQLAlchemy.
            snapshots: Snapshots triés par crawled_at ASC (derniers 90 jours).

        Returns:
            EnrichedMetrics avec tous les champs calculés.
        """
        if not snapshots:
            return EnrichedMetrics()

        try:
            return self._compute(product, snapshots)
        except Exception as exc:
            log.warning(
                "Erreur enrichissement produit",
                product_id=product.id,
                error=str(exc),
            )
            return EnrichedMetrics()

    def _compute(self, product: "Product", snapshots: list) -> EnrichedMetrics:
        now = datetime.utcnow()
        metrics = EnrichedMetrics()

        # ── Ancienneté ────────────────────────────────────────────────────
        if product.first_seen:
            metrics.days_since_first_seen = max(0, (now - product.first_seen).days)

        # ── Snapshots avec prix valide ─────────────────────────────────────
        prices = [s.price for s in snapshots if s.price is not None and s.price > 0]

        if prices:
            metrics.price_min = round(min(prices), 2)
            metrics.price_max = round(max(prices), 2)
            metrics.price_avg = round(statistics.mean(prices), 2)

            # Coefficient de variation (écart-type / moyenne)
            if len(prices) >= 2 and metrics.price_avg > 0:
                std = statistics.stdev(prices)
                metrics.price_stability = round(std / metrics.price_avg, 3)
            else:
                metrics.price_stability = 0.0

        # ── Promotions ────────────────────────────────────────────────────
        total = len(snapshots)
        on_sale_snaps = [s for s in snapshots if s.on_sale]
        n_on_sale = len(on_sale_snaps)

        if total > 0:
            metrics.promo_frequency_pct = round(n_on_sale / total * 100, 1)

        if on_sale_snaps:
            discounts = [
                s.discount_pct for s in on_sale_snaps
                if s.discount_pct is not None
            ]
            if discounts:
                metrics.avg_discount_pct = round(statistics.mean(discounts), 1)

            # Approximation des jours en promo (gap moyen entre crawls × nb snapshots promo)
            if len(snapshots) >= 2:
                total_span_days = (
                    snapshots[-1].crawled_at - snapshots[0].crawled_at
                ).days or 1
                crawl_interval_days = total_span_days / (total - 1)
                metrics.days_on_sale = max(
                    1, round(n_on_sale * crawl_interval_days)
                )
            else:
                metrics.days_on_sale = 1 if n_on_sale > 0 else 0

        return metrics

    def to_product_update_dict(self, metrics: EnrichedMetrics) -> dict:
        """Convertit les métriques en dict pour ProductRepository.save()."""
        # Les métriques enrichies sont stockées dans extra_json
        # On les retourne pour que le runner puisse les logger/exporter
        return {
            "promo_frequency_pct":  metrics.promo_frequency_pct,
            "price_min":            metrics.price_min,
            "price_max":            metrics.price_max,
            "price_avg":            metrics.price_avg,
            "price_stability":      metrics.price_stability,
            "days_since_first_seen": metrics.days_since_first_seen,
            "days_on_sale":         metrics.days_on_sale,
            "avg_discount_pct":     metrics.avg_discount_pct,
        }
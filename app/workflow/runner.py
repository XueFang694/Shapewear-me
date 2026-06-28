"""
WorkflowRunner v2 — gestion cycle de vie variantes, best_seller, matériaux.
"""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.connectors.registry import ConnectorRegistry
from app.core.events import event_bus
from app.core.logger import get_logger
from app.processing.change_detector import ChangeDetector
from app.processing.classifier import Classifier
from app.processing.normalizer import Normalizer
from app.scraping.engine import ScrapingEngine
from app.storage.database import get_db, init_db
from app.storage.repository import (
    BrandRepository, ChangeEventRepository, CrawlSessionRepository,
    ProductRepository, SnapshotRepository, VariantRepository,
)

log = get_logger(__name__)


@dataclass
class RunResult:
    brand_slug: str
    session_id: int | None
    status: str = "pending"
    products_found: int = 0
    products_new: int = 0
    products_changed: int = 0
    errors: int = 0
    duration_s: float = 0.0
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    error_message: str = ""


class WorkflowRunner:

    def __init__(self) -> None:
        self._registry   = ConnectorRegistry()
        self._normalizer = Normalizer()
        self._classifier = Classifier()
        self._cancelled  = False
        init_db()

    def run(self, brand_slugs: list[str] | None = None, **_) -> list[RunResult]:
        if brand_slugs is None:
            brand_slugs = self._registry.list_connectors()
        results: list[RunResult] = []
        event_bus.emit("crawl.session.started", brands=brand_slugs)
        log.info("Démarrage session", brands=brand_slugs)

        for slug in brand_slugs:
            if self._cancelled: break
            results.append(self._run_brand(slug))

        event_bus.emit("crawl.session.completed", summary={r.brand_slug: r.status for r in results})
        log.info("Session terminée", brands=brand_slugs, total_products=sum(r.products_found for r in results))
        return results

    def _run_brand(self, brand_slug: str) -> RunResult:
        result = RunResult(brand_slug=brand_slug, session_id=None)
        t0 = time.monotonic()
        try:
            connector = self._registry.get(brand_slug)
            meta      = connector.get_metadata()

            with get_db() as db:
                brand = BrandRepository(db).get_or_create(slug=meta.slug, name=meta.name, base_url=meta.base_url)
                brand_id = brand.id
                cs   = CrawlSessionRepository(db).create(brand_id=brand_id)
                result.session_id = cs.id

            log.info("Crawl marque démarré", brand=brand_slug, session_id=result.session_id)

            with get_db() as db:
                active_before = [p.external_id for p in ProductRepository(db).find_active_by_brand(brand_id)]

            crawled_ids: list[str] = []
            for raw in ScrapingEngine(connector, session_id=result.session_id).crawl():
                if self._cancelled: break
                try:
                    self._process_product(raw, result, brand_id)
                    crawled_ids.append(raw.extra.get("handle") or raw.external_id)
                except Exception as exc:
                    result.errors += 1
                    log.error("Erreur pipeline", brand=brand_slug, error=str(exc), traceback=traceback.format_exc())

            removed = self._detect_removals(brand_id, brand_slug, active_before, crawled_ids, result)

            result.status    = "completed"
            result.duration_s = round(time.monotonic() - t0, 1)
            result.ended_at  = datetime.utcnow()

            with get_db() as db:
                CrawlSessionRepository(db).complete(result.session_id, {
                    "products_found": result.products_found, "products_new": result.products_new,
                    "products_changed": result.products_changed,
                    "products_removed": len(removed), "errors_count": result.errors,
                })
            log.info("Crawl marque terminé", brand=brand_slug, found=result.products_found,
                     new=result.products_new, changed=result.products_changed,
                     removed=len(removed), errors=result.errors, duration_s=result.duration_s)

        except Exception as exc:
            result.status       = "failed"
            result.error_message = str(exc)
            result.duration_s   = round(time.monotonic() - t0, 1)
            log.error("Crawl marque échoué", brand=brand_slug, error=str(exc), traceback=traceback.format_exc())
            if result.session_id:
                try:
                    with get_db() as db: CrawlSessionRepository(db).fail(result.session_id)
                except Exception: pass
        return result

    def _process_product(self, raw: Any, result: RunResult, brand_id: int) -> None:
        normalized = self._normalizer.process(raw)
        normalized = self._classifier.classify(normalized)

        with get_db() as db:
            prod_repo    = ProductRepository(db)
            snap_repo    = SnapshotRepository(db)
            change_repo  = ChangeEventRepository(db)
            variant_repo = VariantRepository(db)

            existing      = prod_repo.find_by_external_id(brand_id, normalized.external_id)
            is_new        = existing is None
            last_snapshot = snap_repo.get_latest(existing.id) if existing else None

            # Gérer best_seller dates
            now = datetime.utcnow()
            product_dict = normalized.to_product_dict()
            product_dict["brand_id"] = brand_id
            if normalized.is_best_seller:
                if existing is None or not existing.is_best_seller:
                    product_dict["best_seller_first_seen"] = now
                product_dict["best_seller_last_seen"] = now
            elif existing and existing.is_best_seller:
                # Perte du badge Best Seller → ne pas écraser best_seller_first_seen
                pass

            # Gérer retour en stock (produit était inactif)
            if existing and not existing.is_active:
                product_dict["back_in_stock_at"] = now
                product_dict["removed_at"]       = None

            changes = ChangeDetector(session_id=result.session_id).compare(
                normalized, last_snapshot,
                product_id=existing.id if existing else None,
            )

            product = prod_repo.save(product_dict)

            # Snapshot
            snap_dict = normalized.to_snapshot_dict()
            snap_dict["product_id"] = product.id
            snap_dict["session_id"] = result.session_id
            snap_repo.save(snap_dict)

            # Variantes granulaires (cycle de vie)
            detailed_variants: list[dict] = normalized.variants or []
            if detailed_variants:
                variant_events = variant_repo.sync_variants(product.id, detailed_variants)
                for ve in variant_events:
                    ve["product_id"] = product.id
                    ve["session_id"] = result.session_id
                    change_repo.save(ve)

            # Événements produit
            for ch in changes:
                cd = ch.to_dict()
                cd["product_id"] = product.id
                cd["session_id"] = result.session_id
                change_repo.save(cd)

            result.products_found += 1
            if is_new: result.products_new += 1
            elif changes: result.products_changed += 1

        status = "NOUVEAU" if is_new else "MÀJ"
        bs_tag = " ⭐" if normalized.is_best_seller else ""
        event_bus.emit("product.saved", product_id=None, brand=normalized.brand_slug,
                       name=f"[{status}]{bs_tag} {normalized.brand_slug} — {normalized.name}",
                       is_new=is_new, session_id=result.session_id)

    def _detect_removals(self, brand_id, brand_slug, before_ids, crawled_ids, result):
        crawled_set   = set(crawled_ids)
        removed_eids  = [eid for eid in before_ids if eid not in crawled_set]
        if not removed_eids: return []
        with get_db() as db:
            prod_repo   = ProductRepository(db)
            change_repo = ChangeEventRepository(db)
            to_remove   = []
            for eid in removed_eids:
                p = prod_repo.find_by_external_id(brand_id, eid)
                if p and p.is_active:
                    to_remove.append(p.id)
                    if result.session_id:
                        change_repo.save({"product_id": p.id, "session_id": result.session_id,
                                          "event_type": "product.removed", "field_name": None,
                                          "old_value": p.name, "new_value": None})
            if to_remove:
                prod_repo.mark_as_removed(to_remove)
                log.info("Produits supprimés", brand=brand_slug, count=len(to_remove))
        return removed_eids

    def cancel(self) -> None:
        self._cancelled = True
        log.info("Annulation demandée")
"""
Reporter — Génère un rapport de synthèse à la fin d'une session d'analyse.

Contenu du rapport :
  - Résumé exécutif (produits analysés, nouveautés, suppressions, changements)
  - Tableau des nouveaux produits par marque
  - Tableau des changements de prix (plus fortes hausses et baisses)
  - Promotions actives avec durée estimée
  - Produits disparus du catalogue
  - Métriques de crawl (durée, erreurs, taux de succès)

Formats de sortie : texte (CLI), HTML (UI), délègue PDF/Excel aux exporters.

Usage :
    reporter = Reporter()
    report   = reporter.generate(session_id=42, brand_slugs=["spanx"])
    html     = reporter.render_html(session_id=42)
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

_BRAND_COLORS = {
    "spanx":      "#1B3A6B",
    "skims":      "#C8A882",
    "honeylove":  "#C0392B",
    "shapermint": "#27AE60",
}
_DEFAULT_COLOR = "#2C3E50"


class Reporter:
    """
    Génère des rapports de session depuis la base de données.

    Façade légère qui agrège les données et délègue le rendu aux exporters.
    """

    def __init__(self) -> None:
        self._export_dir = settings.EXPORT_DIR
        self._export_dir.mkdir(parents=True, exist_ok=True)

    # ── Interface publique ────────────────────────────────────────────────

    def generate(
        self,
        session_id: int | None = None,
        brand_slugs: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Génère un rapport structuré (dict) depuis la base.

        Returns:
            Dictionnaire complet avec toutes les métriques de la session.
        """
        data = self._load_data(session_id, brand_slugs)
        return self._build_report(data, session_id)

    def render_text(
        self,
        session_id: int | None = None,
        brand_slugs: list[str] | None = None,
    ) -> str:
        """Génère un rapport texte pour la CLI."""
        report = self.generate(session_id, brand_slugs)
        return self._format_text(report)

    def render_html(
        self,
        session_id: int | None = None,
        brand_slugs: list[str] | None = None,
        filename: str | None = None,
    ) -> Path:
        """Génère un rapport HTML et le sauvegarde sur disque."""
        from app.exports.pdf_exporter import PdfExporter
        exporter = PdfExporter(export_dir=self._export_dir)
        return exporter.export_html(brand_slugs=brand_slugs, filename=filename)

    def render_pdf(
        self,
        session_id: int | None = None,
        brand_slugs: list[str] | None = None,
        filename: str | None = None,
    ) -> Path:
        """Génère un rapport PDF."""
        from app.exports.pdf_exporter import PdfExporter
        exporter = PdfExporter(export_dir=self._export_dir)
        return exporter.export_from_db(
            brand_slugs=brand_slugs,
            session_id=session_id,
            filename=filename,
        )

    def render_excel(
        self,
        brand_slugs: list[str] | None = None,
        session_id: int | None = None,
        filename: str | None = None,
    ) -> Path:
        """Génère un rapport Excel (6 feuilles)."""
        from app.exports.excel_exporter import ExcelExporter
        exporter = ExcelExporter(export_dir=self._export_dir)
        return exporter.export_from_db(
            brand_slugs=brand_slugs,
            session_id=session_id,
            filename=filename,
        )

    # ── Chargement des données ────────────────────────────────────────────

    def _load_data(
        self,
        session_id: int | None,
        brand_slugs: list[str] | None,
    ) -> dict:
        from app.storage.database import get_db
        from app.storage.models import Brand, ChangeEvent, CrawlSession, Product
        from app.storage.repository import SnapshotRepository

        result: dict = {
            "session": None,
            "brands": [],
            "products": [],
            "snapshots": {},
            "events": [],
            "brand_map": {},
        }

        with get_db() as db:
            # Session
            if session_id:
                result["session"] = (
                    db.query(CrawlSession).filter_by(id=session_id).first()
                )
            else:
                result["session"] = (
                    db.query(CrawlSession)
                    .order_by(CrawlSession.started_at.desc())
                    .first()
                )

            # Marques
            brands = db.query(Brand).filter_by(active=True).all()
            if brand_slugs:
                brands = [b for b in brands if b.slug in brand_slugs]
            result["brands"]    = brands
            result["brand_map"] = {b.id: b for b in brands}
            brand_ids = [b.id for b in brands]

            if not brand_ids:
                return result

            # Produits
            products = db.query(Product).filter(
                Product.brand_id.in_(brand_ids)
            ).all()
            result["products"] = products

            # Snapshots
            snap_repo = SnapshotRepository(db)
            for p in products:
                snap = snap_repo.get_latest(p.id)
                if snap:
                    result["snapshots"][p.id] = snap

            # Événements de la session active
            target_session = result["session"]
            if target_session:
                result["events"] = (
                    db.query(ChangeEvent)
                    .filter_by(session_id=target_session.id)
                    .all()
                )

        return result

    # ── Construction du rapport ───────────────────────────────────────────

    def _build_report(self, data: dict, session_id: int | None) -> dict[str, Any]:
        products  = data["products"]
        brands    = data["brands"]
        events    = data["events"]
        snapshots = data["snapshots"]
        session   = data["session"]
        brand_map = data["brand_map"]

        # KPIs globaux
        active   = [p for p in products if p.is_active]
        on_sale  = [p for p in active if snapshots.get(p.id) and snapshots[p.id].on_sale]
        bs_prods = [p for p in active if p.is_best_seller]
        removed  = [p for p in products if not p.is_active]

        new_evts    = [e for e in events if e.event_type == "product.new"]
        price_evts  = [e for e in events if e.event_type == "price.changed"]
        sale_evts   = [e for e in events if e.event_type == "sale.started"]
        remove_evts = [e for e in events if e.event_type == "product.removed"]

        # Résumé par marque
        brands_summary = []
        for b in brands:
            bps = [p for p in products if p.brand_id == b.id]
            brands_summary.append({
                "slug":         b.slug,
                "name":         b.name,
                "active":       len([p for p in bps if p.is_active]),
                "best_sellers": len([p for p in bps if p.is_best_seller]),
                "on_sale":      len([p for p in bps if snapshots.get(p.id) and snapshots[p.id].on_sale]),
                "removed":      len([p for p in bps if not p.is_active]),
                "color":        _BRAND_COLORS.get(b.slug, _DEFAULT_COLOR),
            })

        # Détail des nouveaux produits
        pmap = {p.id: p for p in products}
        new_products_detail = []
        for ev in new_evts[:100]:
            p = pmap.get(ev.product_id)
            if not p:
                continue
            brand = brand_map.get(p.brand_id)
            snap  = snapshots.get(p.id)
            new_products_detail.append({
                "brand":       brand.slug if brand else "?",
                "brand_name":  brand.name if brand else "?",
                "name":        p.name,
                "family":      p.family or "—",
                "price":       snap.price if snap else None,
                "is_bs":       p.is_best_seller,
                "detected_at": ev.detected_at,
            })

        # Détail des changements de prix (triés par amplitude)
        price_changes_detail = []
        for ev in price_evts[:100]:
            p = pmap.get(ev.product_id)
            if not p:
                continue
            brand = brand_map.get(p.brand_id)
            try:
                old_p  = float(ev.old_value) if ev.old_value else None
                new_p  = float(ev.new_value) if ev.new_value else None
                delta  = round(new_p - old_p, 2) if (old_p and new_p) else None
                pct    = round((new_p - old_p) / old_p * 100, 1) if (old_p and new_p and old_p > 0) else None
            except (ValueError, TypeError):
                old_p = new_p = delta = pct = None
            price_changes_detail.append({
                "brand":       brand.slug if brand else "?",
                "brand_name":  brand.name if brand else "?",
                "name":        p.name,
                "old_price":   old_p,
                "new_price":   new_p,
                "delta":       delta,
                "delta_pct":   pct,
                "detected_at": ev.detected_at,
            })
        price_changes_detail.sort(
            key=lambda x: abs(x.get("delta") or 0), reverse=True
        )

        # Session metadata
        session_meta: dict = {}
        if session:
            duration = None
            if session.started_at and session.ended_at:
                secs = int((session.ended_at - session.started_at).total_seconds())
                duration = f"{secs // 60}m {secs % 60}s"
            session_meta = {
                "id":               session.id,
                "started_at":       session.started_at,
                "ended_at":         session.ended_at,
                "duration":         duration,
                "status":           session.status,
                "products_found":   session.products_found,
                "products_new":     session.products_new,
                "products_changed": session.products_changed,
                "products_removed": session.products_removed,
                "errors_count":     session.errors_count,
            }

        return {
            "generated_at": datetime.utcnow(),
            "session":      session_meta,
            "kpis": {
                "active_products":   len(active),
                "new_products":      len(new_evts),
                "price_changes":     len(price_evts),
                "sales_started":     len(sale_evts),
                "products_removed":  len(remove_evts),
                "on_sale":           len(on_sale),
                "best_sellers":      len(bs_prods),
            },
            "brands_summary":       brands_summary,
            "new_products":         new_products_detail,
            "price_changes":        price_changes_detail,
            "removed_products": [
                {
                    "brand": brand_map.get(p.brand_id, {}).name if brand_map.get(p.brand_id) else "?",
                    "name":  p.name,
                    "family": p.family or "—",
                    "last_seen": p.last_seen,
                    "removed_at": p.removed_at,
                }
                for p in removed[:100]
            ],
        }

    # ── Rendu texte CLI ───────────────────────────────────────────────────

    def _format_text(self, report: dict) -> str:
        """Formate le rapport en texte pour la CLI."""
        lines: list[str] = []
        sep  = "=" * 60
        sep2 = "-" * 60

        lines.append(sep)
        lines.append("  RAPPORT DE SESSION — Market Intelligence Shapewear US")
        lines.append(sep)

        # Session info
        sess = report.get("session", {})
        if sess:
            lines.append(f"\nSession #{sess.get('id', '?')} | {sess.get('status', '?')}")
            if sess.get("started_at"):
                lines.append(f"Démarrage : {sess['started_at'].strftime('%d/%m/%Y %H:%M')}")
            if sess.get("duration"):
                lines.append(f"Durée     : {sess['duration']}")
        lines.append("")

        # KPIs
        kpis = report.get("kpis", {})
        lines.append(sep2)
        lines.append("RÉSUMÉ EXÉCUTIF")
        lines.append(sep2)
        lines.append(f"  Produits actifs       : {kpis.get('active_products', 0):>6}")
        lines.append(f"  Nouveaux produits     : {kpis.get('new_products', 0):>6}")
        lines.append(f"  Changements de prix   : {kpis.get('price_changes', 0):>6}")
        lines.append(f"  Nouvelles promotions  : {kpis.get('sales_started', 0):>6}")
        lines.append(f"  Suppressions          : {kpis.get('products_removed', 0):>6}")
        lines.append(f"  Best Sellers suivis   : {kpis.get('best_sellers', 0):>6}")
        lines.append("")

        # Par marque
        brands = report.get("brands_summary", [])
        if brands:
            lines.append(sep2)
            lines.append("PAR MARQUE")
            lines.append(sep2)
            lines.append(f"  {'Marque':<14} {'Actifs':>8} {'BS':>6} {'Promo':>7} {'Supp.':>7}")
            lines.append(f"  {'-'*14} {'-'*8} {'-'*6} {'-'*7} {'-'*7}")
            for b in brands:
                lines.append(
                    f"  {b['name']:<14} {b['active']:>8} "
                    f"{b['best_sellers']:>6} {b['on_sale']:>7} {b['removed']:>7}"
                )
            lines.append("")

        # Nouveaux produits
        new_prods = report.get("new_products", [])
        if new_prods:
            lines.append(sep2)
            lines.append(f"NOUVEAUX PRODUITS ({len(new_prods)})")
            lines.append(sep2)
            for p in new_prods[:20]:
                bs_tag = " ⭐" if p.get("is_bs") else ""
                price  = f"${p['price']:.2f}" if p.get("price") else "—"
                lines.append(f"  [{p['brand'].upper():<10}] {p['name'][:45]:<45} {price}{bs_tag}")
            if len(new_prods) > 20:
                lines.append(f"  … et {len(new_prods) - 20} autres")
            lines.append("")

        # Changements de prix
        price_chgs = report.get("price_changes", [])
        if price_chgs:
            lines.append(sep2)
            lines.append(f"CHANGEMENTS DE PRIX ({len(price_chgs)})")
            lines.append(sep2)
            for p in price_chgs[:15]:
                direction = "▲" if (p.get("delta") or 0) > 0 else "▼"
                delta_str = ""
                if p.get("delta") is not None:
                    delta_str = f"{direction} ${abs(p['delta']):.2f}"
                    if p.get("delta_pct"):
                        delta_str += f" ({abs(p['delta_pct']):.1f}%)"
                old_s = f"${p['old_price']:.2f}" if p.get("old_price") else "—"
                new_s = f"${p['new_price']:.2f}" if p.get("new_price") else "—"
                lines.append(
                    f"  [{p['brand'].upper():<10}] {p['name'][:35]:<35}"
                    f" {old_s} → {new_s}  {delta_str}"
                )
            if len(price_chgs) > 15:
                lines.append(f"  … et {len(price_chgs) - 15} autres")
            lines.append("")

        lines.append(sep)
        lines.append(f"  Rapport généré le {datetime.utcnow().strftime('%d/%m/%Y à %H:%M UTC')}")
        lines.append(sep)

        return "\n".join(lines)

    def print_summary(
        self,
        session_id: int | None = None,
        brand_slugs: list[str] | None = None,
    ) -> None:
        """Affiche le rapport texte dans la console."""
        report = self.generate(session_id=session_id, brand_slugs=brand_slugs)
        print(self._format_text(report))
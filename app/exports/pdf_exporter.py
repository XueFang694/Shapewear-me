"""
PdfExporter — Génère un rapport PDF de veille concurrentielle via WeasyPrint.

Structure du rapport :
  1. Page de garde  (date, marques, période)
  2. Résumé exécutif (KPIs, infographies textuelles)
  3. Nouveaux produits
  4. Changements de prix
  5. Promotions actives
  6. Produits supprimés
  7. Annexe métriques de crawl

Usage :
    exporter = PdfExporter()
    path = exporter.export_from_db(brand_slugs=["spanx", "skims"])
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

_DATE_FMT = "%Y-%m-%d %H:%M"
_DATE_ONLY = "%d/%m/%Y"

# Couleurs marques
_BRAND_COLORS = {
    "spanx":      "#1B3A6B",
    "skims":      "#C8A882",
    "honeylove":  "#C0392B",
    "shapermint": "#27AE60",
}
_DEFAULT_COLOR = "#2C3E50"


def _d(dt, fmt: str = _DATE_ONLY) -> str:
    return dt.strftime(fmt) if dt else "—"


def _pct(v) -> str:
    return f"{v:.1f}%" if v is not None else "—"


def _price(v) -> str:
    return f"${v:.2f}" if v is not None else "—"


class PdfExporter:
    """Génère un rapport PDF depuis la base de données."""

    def __init__(self, export_dir: Path | None = None) -> None:
        self._export_dir = export_dir or settings.EXPORT_DIR
        self._export_dir.mkdir(parents=True, exist_ok=True)

    # ── Interface publique ────────────────────────────────────────────────

    def export_from_db(
        self,
        brand_slugs: list[str] | None = None,
        session_id: int | None = None,
        filename: str | None = None,
    ) -> Path:
        """
        Génère un rapport PDF depuis la base de données.

        Returns:
            Chemin du fichier PDF créé.
        """
        data = self._load_data(brand_slugs)
        html = self._render_html(data, brand_slugs)

        if not filename:
            ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
            sfx = f"_session{session_id}" if session_id else ""
            filename = f"report_{ts}{sfx}.pdf"

        path = self._export_dir / filename
        self._html_to_pdf(html, path)

        log.info("Export PDF créé", path=str(path))
        return path

    # ── Chargement des données ────────────────────────────────────────────

    def _load_data(self, brand_slugs: list[str] | None) -> dict:
        from app.storage.database import get_db
        from app.storage.models import Brand, ChangeEvent, CrawlSession, Product
        from app.storage.repository import SnapshotRepository

        result: dict = {
            "brands": [], "products": [], "snapshots": {},
            "sessions": [], "events": [], "brand_map": {},
        }

        with get_db() as db:
            brands = db.query(Brand).filter_by(active=True).all()
            if brand_slugs:
                brands = [b for b in brands if b.slug in brand_slugs]
            result["brands"]    = brands
            result["brand_map"] = {b.id: b for b in brands}
            brand_ids = [b.id for b in brands]
            if not brand_ids:
                return result

            products = db.query(Product).filter(Product.brand_id.in_(brand_ids)).all()
            result["products"] = products

            snap_repo = SnapshotRepository(db)
            for p in products:
                snap = snap_repo.get_latest(p.id)
                if snap:
                    result["snapshots"][p.id] = snap

            sessions = (
                db.query(CrawlSession)
                .filter(CrawlSession.brand_id.in_(brand_ids))
                .order_by(CrawlSession.started_at.desc())
                .limit(20)
                .all()
            )
            result["sessions"] = sessions

            if sessions:
                events = (
                    db.query(ChangeEvent)
                    .filter(ChangeEvent.session_id == sessions[0].id)
                    .all()
                )
                result["events"] = events

        return result

    # ── Rendu HTML ────────────────────────────────────────────────────────

    def _render_html(self, data: dict, brand_slugs: list[str] | None) -> str:
        products  = data["products"]
        brands    = data["brands"]
        sessions  = data["sessions"]
        events    = data["events"]
        snapshots = data["snapshots"]
        brand_map = data["brand_map"]

        active    = [p for p in products if p.is_active]
        on_sale   = [p for p in products if snapshots.get(p.id) and snapshots[p.id].on_sale]
        new_evts  = [e for e in events if e.event_type == "product.new"]
        removed   = [p for p in products if not p.is_active]
        price_chg = [e for e in events if e.event_type == "price.changed"]
        bs_prods  = [p for p in products if p.is_best_seller]

        now_str = datetime.now().strftime("%d/%m/%Y à %H:%M")
        brands_str = ", ".join(b.name for b in brands) or "Toutes"

        # ── page de garde + styles ─────────────────────────────────────────
        html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<style>
  @page {{ margin: 2cm 2cm 2.5cm 2cm; }}
  body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; font-size: 10pt;
          color: #2C3E50; margin: 0; }}
  h1   {{ font-size: 22pt; color: #1B3A6B; margin-bottom: 4px; }}
  h2   {{ font-size: 14pt; color: #1B3A6B; border-bottom: 2px solid #1B3A6B;
          padding-bottom: 4px; margin-top: 20px; }}
  h3   {{ font-size: 11pt; color: #2C3E50; margin-top: 14px; }}
  p    {{ margin: 4px 0 8px; line-height: 1.5; }}
  table{{ width: 100%; border-collapse: collapse; font-size: 9pt; margin: 10px 0; }}
  th   {{ background: #1B3A6B; color: white; padding: 6px 8px; text-align: left; }}
  td   {{ padding: 5px 8px; border-bottom: 1px solid #E8ECF0; vertical-align: top; }}
  tr:nth-child(even) td {{ background: #F7F9FC; }}
  .cover {{ text-align: center; padding: 60px 20px; }}
  .cover .subtitle {{ font-size: 13pt; color: #7F8C8D; margin-top: 6px; }}
  .cover .meta {{ font-size: 10pt; color: #95A5A6; margin-top: 30px; }}
  .kpi-grid {{ display: table; width: 100%; margin: 16px 0; }}
  .kpi-cell {{ display: table-cell; width: 20%; padding: 12px; text-align: center;
               background: #F7F9FC; border: 1px solid #E8ECF0; }}
  .kpi-value {{ font-size: 22pt; font-weight: bold; }}
  .kpi-label {{ font-size: 8pt; color: #7F8C8D; text-transform: uppercase; }}
  .badge-bs  {{ color: #F39C12; }}
  .badge-new {{ color: #27AE60; font-weight: bold; }}
  .page-break {{ page-break-before: always; }}
  .footer    {{ text-align: center; font-size: 8pt; color: #BDC3C7; margin-top: 20px; }}
  .brand-pill {{ display: inline-block; padding: 2px 8px; border-radius: 10px;
                 color: white; font-size: 8pt; font-weight: bold; }}
  .green {{ color: #27AE60; font-weight: bold; }}
  .red   {{ color: #C0392B; font-weight: bold; }}
</style>
</head>
<body>

<!-- ══ PAGE DE GARDE ══════════════════════════════════════════════════ -->
<div class="cover">
  <h1>Market Intelligence Platform</h1>
  <div class="subtitle">Rapport de Veille Concurrentielle — Shapewear US</div>
  <div class="meta">
    <p>Marques analysées : <strong>{brands_str}</strong></p>
    <p>Généré le {now_str}</p>
    {f'<p>Dernière session : {_d(sessions[0].started_at, _DATE_FMT)}</p>' if sessions else ''}
  </div>
</div>

<!-- ══ RÉSUMÉ EXÉCUTIF ════════════════════════════════════════════════ -->
<div class="page-break"></div>
<h2>Résumé Exécutif</h2>
<div class="kpi-grid">
  <div class="kpi-cell">
    <div class="kpi-value" style="color:#1B3A6B">{len(active)}</div>
    <div class="kpi-label">Produits actifs</div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-value" style="color:#27AE60">{len(new_evts)}</div>
    <div class="kpi-label">Nouveautés</div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-value" style="color:#E67E22">{len(on_sale)}</div>
    <div class="kpi-label">En promotion</div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-value" style="color:#8E44AD">{len(bs_prods)}</div>
    <div class="kpi-label">Best Sellers</div>
  </div>
  <div class="kpi-cell">
    <div class="kpi-value" style="color:#C0392B">{len(removed)}</div>
    <div class="kpi-label">Suppressions</div>
  </div>
</div>

{self._render_brand_summary(brands, products, snapshots)}

<!-- ══ NOUVEAUX PRODUITS ══════════════════════════════════════════════ -->
<div class="page-break"></div>
<h2>Nouveaux Produits ({len(new_evts)})</h2>
{self._render_new_products(new_evts, products, snapshots, brand_map)}

<!-- ══ CHANGEMENTS DE PRIX ═══════════════════════════════════════════ -->
<div class="page-break"></div>
<h2>Changements de Prix ({len(price_chg)})</h2>
{self._render_price_changes(price_chg, products, brand_map)}

<!-- ══ PROMOTIONS ACTIVES ════════════════════════════════════════════ -->
<div class="page-break"></div>
<h2>Promotions Actives ({len(on_sale)})</h2>
{self._render_promotions(on_sale, snapshots, brand_map)}

<!-- ══ PRODUITS SUPPRIMÉS ════════════════════════════════════════════ -->
<div class="page-break"></div>
<h2>Produits Supprimés ({len(removed)})</h2>
{self._render_removed(removed, brand_map)}

<!-- ══ ANNEXE MÉTRIQUES ══════════════════════════════════════════════ -->
<div class="page-break"></div>
<h2>Annexe — Métriques de Crawl</h2>
{self._render_sessions(sessions, brand_map)}

<div class="footer">
  Market Intelligence Platform — Shapewear US — {now_str}
</div>
</body>
</html>"""
        return html

    # ── Sections HTML ────────────────────────────────────────────────────

    def _brand_pill(self, brand) -> str:
        color = _BRAND_COLORS.get(brand.slug if hasattr(brand, "slug") else brand, _DEFAULT_COLOR)
        name  = brand.name if hasattr(brand, "name") else str(brand)
        return f'<span class="brand-pill" style="background:{color}">{name}</span>'

    def _render_brand_summary(self, brands, products, snapshots) -> str:
        if not brands:
            return "<p><em>Aucune marque.</em></p>"
        rows = ""
        for b in brands:
            bps    = [p for p in products if p.brand_id == b.id]
            active = sum(1 for p in bps if p.is_active)
            sale   = sum(1 for p in bps if snapshots.get(p.id) and snapshots[p.id].on_sale)
            bs     = sum(1 for p in bps if p.is_best_seller)
            removed = sum(1 for p in bps if not p.is_active)
            color  = _BRAND_COLORS.get(b.slug, _DEFAULT_COLOR)
            rows += (
                f"<tr><td><strong style='color:{color}'>{b.name}</strong></td>"
                f"<td>{active}</td><td>{bs}</td><td>{sale}</td><td>{removed}</td></tr>"
            )
        return f"""
<h3>Synthèse par marque</h3>
<table>
  <tr><th>Marque</th><th>Actifs</th><th>Best Sellers</th><th>En promo</th><th>Supprimés</th></tr>
  {rows}
</table>"""

    def _render_new_products(self, new_evts, products, snapshots, brand_map) -> str:
        if not new_evts:
            return "<p><em>Aucun nouveau produit lors de cette session.</em></p>"
        pmap = {p.id: p for p in products}
        rows = ""
        for ev in new_evts[:50]:  # limite à 50 lignes
            p = pmap.get(ev.product_id)
            if not p:
                continue
            brand = brand_map.get(p.brand_id)
            snap  = snapshots.get(p.id)
            pill  = self._brand_pill(brand) if brand else p.brand_id
            bs    = "⭐" if p.is_best_seller else ""
            rows += (
                f"<tr><td>{pill}</td>"
                f"<td>{p.name} {bs}</td>"
                f"<td>{p.family or '—'}</td>"
                f"<td>{_price(snap.price if snap else None)}</td>"
                f"<td>{_d(ev.detected_at, _DATE_FMT)}</td></tr>"
            )
        total_str = f" (les 50 premiers affichés)" if len(new_evts) > 50 else ""
        return f"""
<table>
  <tr><th>Marque</th><th>Produit</th><th>Famille</th><th>Prix</th><th>Détection</th></tr>
  {rows}
</table>
<p style="font-size:8pt;color:#95A5A6">{len(new_evts)} nouveau(x) produit(s){total_str}.</p>"""

    def _render_price_changes(self, price_chg, products, brand_map) -> str:
        if not price_chg:
            return "<p><em>Aucun changement de prix lors de cette session.</em></p>"
        pmap = {p.id: p for p in products}
        rows = ""
        for ev in sorted(price_chg, key=lambda e: abs(
            (float(e.new_value or 0) - float(e.old_value or 0))
        ), reverse=True)[:50]:
            p = pmap.get(ev.product_id)
            if not p:
                continue
            brand = brand_map.get(p.brand_id)
            pill  = self._brand_pill(brand) if brand else "—"
            try:
                old_p  = float(ev.old_value) if ev.old_value else None
                new_p  = float(ev.new_value) if ev.new_value else None
                delta  = round(new_p - old_p, 2) if (old_p and new_p) else None
                pct_v  = round((new_p - old_p) / old_p * 100, 1) if (old_p and new_p and old_p > 0) else None
                cls    = "red" if (delta and delta > 0) else "green"
                sign   = "▲" if (delta and delta > 0) else "▼"
                delta_str = f'<span class="{cls}">{sign} ${abs(delta):.2f} ({_pct(abs(pct_v) if pct_v else None)})</span>'
            except Exception:
                old_p = new_p = delta_str = None
            rows += (
                f"<tr><td>{pill}</td>"
                f"<td>{p.name}</td>"
                f"<td>{_price(old_p)}</td>"
                f"<td>{_price(new_p)}</td>"
                f"<td>{delta_str or '—'}</td>"
                f"<td>{_d(ev.detected_at, _DATE_FMT)}</td></tr>"
            )
        return f"""
<table>
  <tr><th>Marque</th><th>Produit</th><th>Avant</th><th>Après</th><th>Variation</th><th>Date</th></tr>
  {rows}
</table>"""

    def _render_promotions(self, on_sale_products, snapshots, brand_map) -> str:
        if not on_sale_products:
            return "<p><em>Aucun produit en promotion actuellement.</em></p>"
        rows = ""
        sorted_prods = sorted(
            on_sale_products,
            key=lambda p: snapshots[p.id].discount_pct or 0,
            reverse=True,
        )
        for p in sorted_prods[:60]:
            snap  = snapshots.get(p.id)
            brand = brand_map.get(p.brand_id)
            pill  = self._brand_pill(brand) if brand else "—"
            bs    = "⭐" if p.is_best_seller else ""
            rows += (
                f"<tr><td>{pill}</td>"
                f"<td>{p.name} {bs}</td>"
                f"<td>{p.family or '—'}</td>"
                f"<td>{_price(snap.price if snap else None)}</td>"
                f"<td>{_price(snap.original_price if snap else None)}</td>"
                f"<td><strong style='color:#E67E22'>{_pct(snap.discount_pct if snap else None)}</strong></td></tr>"
            )
        return f"""
<table>
  <tr><th>Marque</th><th>Produit</th><th>Famille</th><th>Prix promo</th><th>Prix original</th><th>Remise</th></tr>
  {rows}
</table>"""

    def _render_removed(self, removed, brand_map) -> str:
        if not removed:
            return "<p><em>Aucun produit supprimé.</em></p>"
        rows = ""
        for p in sorted(removed, key=lambda x: x.removed_at or datetime.min, reverse=True)[:60]:
            brand = brand_map.get(p.brand_id)
            pill  = self._brand_pill(brand) if brand else "—"
            rows += (
                f"<tr><td>{pill}</td>"
                f"<td>{p.name}</td>"
                f"<td>{p.family or '—'}</td>"
                f"<td>{_d(p.last_seen)}</td>"
                f"<td>{_d(p.removed_at)}</td></tr>"
            )
        return f"""
<table>
  <tr><th>Marque</th><th>Produit</th><th>Famille</th><th>Dernière vue</th><th>Supprimé le</th></tr>
  {rows}
</table>"""

    def _render_sessions(self, sessions, brand_map) -> str:
        if not sessions:
            return "<p><em>Aucune session enregistrée.</em></p>"
        rows = ""
        for s in sessions[:20]:
            brand = brand_map.get(s.brand_id)
            brand_name = brand.name if brand else f"Brand #{s.brand_id}"
            duration = "—"
            if s.started_at and s.ended_at:
                secs = int((s.ended_at - s.started_at).total_seconds())
                duration = f"{secs // 60}m {secs % 60}s"
            status_color = "#27AE60" if s.status == "completed" else "#C0392B"
            rows += (
                f"<tr><td>{brand_name}</td>"
                f"<td>{_d(s.started_at, _DATE_FMT)}</td>"
                f"<td>{duration}</td>"
                f"<td>{s.products_found}</td>"
                f"<td style='color:#27AE60'>{s.products_new}</td>"
                f"<td style='color:#E67E22'>{s.products_changed}</td>"
                f"<td style='color:#C0392B'>{s.products_removed}</td>"
                f"<td>{s.errors_count}</td>"
                f"<td><strong style='color:{status_color}'>{s.status}</strong></td></tr>"
            )
        return f"""
<table>
  <tr><th>Marque</th><th>Démarrage</th><th>Durée</th>
      <th>Produits</th><th>Nouveaux</th><th>Modifiés</th><th>Supprimés</th>
      <th>Erreurs</th><th>Statut</th></tr>
  {rows}
</table>"""

    # ── Conversion HTML → PDF ─────────────────────────────────────────────

    def _html_to_pdf(self, html: str, output_path: Path) -> None:
        """Convertit le HTML en PDF via WeasyPrint."""
        try:
            from weasyprint import HTML as WP_HTML
            WP_HTML(string=html).write_pdf(str(output_path))
            return
        except ImportError:
            log.warning("weasyprint non installé — sauvegarde en HTML")
        except Exception as exc:
            log.error("Erreur WeasyPrint", error=str(exc))
            log.warning("Sauvegarde en HTML à la place")

        # Fallback : sauvegarder en HTML
        html_path = output_path.with_suffix(".html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info("Rapport HTML sauvegardé (fallback)", path=str(html_path))

    # ── Export mémoire ────────────────────────────────────────────────────

    def export_html(
        self,
        brand_slugs: list[str] | None = None,
        filename: str | None = None,
    ) -> Path:
        """Exporte uniquement le HTML (sans conversion PDF)."""
        data = self._load_data(brand_slugs)
        html = self._render_html(data, brand_slugs)

        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"report_{ts}.html"

        path = self._export_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        log.info("Rapport HTML créé", path=str(path))
        return path
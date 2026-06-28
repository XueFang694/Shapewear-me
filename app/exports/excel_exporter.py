"""
ExcelExporter — Export Excel structuré en 6 feuilles via openpyxl.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

_BRAND_COLORS = {"spanx": "1B3A6B", "skims": "C8A882", "honeylove": "C0392B", "shapermint": "27AE60"}
_DEFAULT_COLOR = "2C3E50"
_ALT_ROW = "F7F9FC"


class ExcelExporter:

    def __init__(self, export_dir: Path | None = None):
        self._export_dir = export_dir or settings.EXPORT_DIR
        self._export_dir.mkdir(parents=True, exist_ok=True)

    def export_from_db(self, brand_slugs=None, session_id=None, filename=None) -> Path:
        try:
            from openpyxl import Workbook
        except ImportError:
            raise ImportError("openpyxl requis : pip install openpyxl")

        wb = Workbook()
        wb.remove(wb.active)
        data = self._load_data(brand_slugs)
        self._sheet_synthese(wb, data)
        self._sheet_produits(wb, data)
        self._sheet_nouveautes(wb, data)
        self._sheet_prix(wb, data)
        self._sheet_promotions(wb, data)
        self._sheet_suppressions(wb, data)

        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            sfx = f"_session{session_id}" if session_id else ""
            filename = f"export_{ts}{sfx}.xlsx"
        path = self._export_dir / filename
        wb.save(str(path))
        log.info("Export Excel créé", path=str(path))
        return path

    def _load_data(self, brand_slugs) -> dict:
        from app.storage.database import get_db
        from app.storage.models import Brand, ChangeEvent, CrawlSession, Product, ProductSnapshot, Variant
        from app.storage.repository import SnapshotRepository
        result = {"brands": [], "products": [], "variants": [], "snapshots": {},
                  "sessions": [], "events": [], "brand_map": {}}
        with get_db() as db:
            brands = db.query(Brand).filter_by(active=True).all()
            if brand_slugs:
                brands = [b for b in brands if b.slug in brand_slugs]
            result["brands"] = brands
            result["brand_map"] = {b.id: b.slug for b in brands}
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
            result["variants"] = db.query(Variant).filter(
                Variant.product_id.in_([p.id for p in products])).all()
            sessions = db.query(CrawlSession).filter(
                CrawlSession.brand_id.in_(brand_ids)).order_by(
                CrawlSession.started_at.desc()).limit(50).all()
            result["sessions"] = sessions
            if sessions:
                events = db.query(ChangeEvent).filter(
                    ChangeEvent.session_id == sessions[0].id).all()
                result["events"] = events
        return result

    def _hdr(self, ws, headers, row=1, color="2C3E50"):
        from openpyxl.styles import Font, PatternFill, Alignment
        for col, h in enumerate(headers, 1):
            c = ws.cell(row=row, column=col, value=h)
            c.font = Font(bold=True, color="FFFFFF", size=10)
            c.fill = PatternFill("solid", fgColor=color)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def _row(self, ws, vals, row, alt=False):
        from openpyxl.styles import PatternFill, Alignment
        fill = PatternFill("solid", fgColor=_ALT_ROW) if alt else None
        for col, val in enumerate(vals, 1):
            c = ws.cell(row=row, column=col, value=val)
            if fill: c.fill = fill
            c.alignment = Alignment(vertical="center")

    def _widths(self, ws, widths):
        from openpyxl.utils import get_column_letter
        for col, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(col)].width = w

    def _sheet_synthese(self, wb, data):
        from openpyxl.styles import Font, PatternFill, Alignment
        ws = wb.create_sheet("Synthèse")
        ws.sheet_view.showGridLines = False
        ws["A1"] = "Market Intelligence Platform — Shapewear US"
        ws["A1"].font = Font(bold=True, size=16, color="1B3A6B")
        ws["A2"] = f"Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}"
        ws["A2"].font = Font(italic=True, size=10, color="7F8C8D")
        ws.merge_cells("A1:F1"); ws.merge_cells("A2:F2")

        products = data["products"]
        active   = [p for p in products if p.is_active]
        on_sale  = [pid for pid, s in data["snapshots"].items() if s.on_sale]
        new_evts = [e for e in data["events"] if e.event_type == "product.new"]
        removed  = [p for p in products if not p.is_active]
        bs_prods = [p for p in products if p.is_best_seller]

        kpis = [
            ("Produits actifs", len(active), "1B3A6B"),
            ("Nouveautés (session)", len(new_evts), "27AE60"),
            ("En promotion", len(on_sale), "E67E22"),
            ("Best Sellers", len(bs_prods), "8E44AD"),
            ("Suppressions", len(removed), "C0392B"),
        ]
        row = 4
        ws[f"A{row}"] = "KPIs Globaux"
        ws[f"A{row}"].font = Font(bold=True, size=12)
        row += 1
        for label, value, color in kpis:
            ws[f"B{row}"] = label; ws[f"B{row}"].font = Font(size=11)
            ws[f"C{row}"] = value; ws[f"C{row}"].font = Font(bold=True, size=14, color=color)
            row += 1

        row += 1
        ws[f"A{row}"] = "Par marque"; ws[f"A{row}"].font = Font(bold=True, size=12)
        row += 1
        self._hdr(ws, ["Marque","Actifs","Best Sellers","En promo","Supprimés"], row=row)
        row += 1
        for brand in data["brands"]:
            bps  = [p for p in products if p.brand_id == brand.id]
            color = _BRAND_COLORS.get(brand.slug, _DEFAULT_COLOR)
            ws.cell(row=row, column=1, value=brand.name).font = Font(bold=True, color=color)
            ws.cell(row=row, column=2, value=len([p for p in bps if p.is_active]))
            ws.cell(row=row, column=3, value=len([p for p in bps if p.is_best_seller]))
            ws.cell(row=row, column=4, value=len([p for p in bps if data["snapshots"].get(p.id) and data["snapshots"][p.id].on_sale]))
            ws.cell(row=row, column=5, value=len([p for p in bps if not p.is_active]))
            row += 1
        self._widths(ws, [22,18,15,12,12])

    def _sheet_produits(self, wb, data):
        ws = wb.create_sheet("Produits")
        headers = ["Marque","Nom","Famille","Sous-famille","Couleur","Taille","SKU",
                   "Prix","Prix original","Remise %","Disponible","Best Seller",
                   "Matière","Doublure","Nylon%","Elastane%","1ère vue","Dernière vue","URL"]
        self._hdr(ws, headers)
        vbp: dict[int, list] = {}
        for v in data["variants"]: vbp.setdefault(v.product_id, []).append(v)
        bm = data["brand_map"]; row = 2
        for p in data["products"]:
            snap = data["snapshots"].get(p.id)
            vv = vbp.get(p.id, [])
            comp = {}
            if p.material_composition_json:
                try: comp = json.loads(p.material_composition_json)
                except: pass
            pp = snap.price if snap else None
            po = snap.original_price if snap else None
            pd = snap.discount_pct if snap else None
            if vv:
                for v in vv:
                    vp = v.price if v.price is not None else pp
                    vo = v.original_price if v.original_price is not None else po
                    vd = round((1-vp/vo)*100,1) if (v.on_sale and vp and vo) else pd
                    self._row(ws, [bm.get(p.brand_id,""),p.name,p.family or "",p.subfamily or "",
                        v.color or "",v.size or "",v.sku or "",vp,vo if v.on_sale else None,vd,
                        "Oui" if v.available else "Non","⭐" if p.is_best_seller else "",
                        p.material_main or "",p.material_lining or "",
                        comp.get("nylon",""),comp.get("elastane",""),
                        p.first_seen.strftime("%Y-%m-%d") if p.first_seen else "",
                        p.last_seen.strftime("%Y-%m-%d") if p.last_seen else "",p.url], row, row%2==0)
                    row += 1
            else:
                self._row(ws, [bm.get(p.brand_id,""),p.name,p.family or "",p.subfamily or "",
                    "","","",pp,po,pd,"","⭐" if p.is_best_seller else "",
                    p.material_main or "",p.material_lining or "",
                    comp.get("nylon",""),comp.get("elastane",""),
                    p.first_seen.strftime("%Y-%m-%d") if p.first_seen else "",
                    p.last_seen.strftime("%Y-%m-%d") if p.last_seen else "",p.url], row, row%2==0)
                row += 1
        self._widths(ws, [12,40,18,20,18,8,14,10,12,10,12,12,30,25,8,8,14,14,50])
        ws.freeze_panes = "A2"

    def _sheet_nouveautes(self, wb, data):
        ws = wb.create_sheet("Nouveautés")
        self._hdr(ws, ["Marque","Nom","Famille","Prix","Best Seller","Détection","URL"], color="27AE60")
        bm = data["brand_map"]; pmap = {p.id: p for p in data["products"]}
        row = 2
        for ev in [e for e in data["events"] if e.event_type == "product.new"]:
            p = pmap.get(ev.product_id)
            if not p: continue
            snap = data["snapshots"].get(p.id)
            self._row(ws, [bm.get(p.brand_id,""),p.name,p.family or "",
                snap.price if snap else "","⭐" if p.is_best_seller else "",
                ev.detected_at.strftime("%Y-%m-%d %H:%M") if ev.detected_at else "",p.url], row, row%2==0)
            row += 1
        self._widths(ws, [12,45,18,10,12,18,50]); ws.freeze_panes = "A2"

    def _sheet_prix(self, wb, data):
        from openpyxl.styles import Font
        ws = wb.create_sheet("Changements prix")
        self._hdr(ws, ["Marque","Nom","Famille","Avant","Après","Variation $","Variation %","Date","URL"], color="E67E22")
        bm = data["brand_map"]; pmap = {p.id: p for p in data["products"]}
        row = 2
        for ev in [e for e in data["events"] if e.event_type == "price.changed"]:
            p = pmap.get(ev.product_id)
            if not p: continue
            try:
                old_p = float(ev.old_value) if ev.old_value else None
                new_p = float(ev.new_value) if ev.new_value else None
                delta = round(new_p - old_p, 2) if (old_p and new_p) else None
                delta_pct = round((new_p-old_p)/old_p*100,1) if (old_p and new_p and old_p>0) else None
            except: old_p=new_p=delta=delta_pct=None
            self._row(ws, [bm.get(p.brand_id,""),p.name,p.family or "",old_p,new_p,delta,delta_pct,
                ev.detected_at.strftime("%Y-%m-%d %H:%M") if ev.detected_at else "",p.url], row, row%2==0)
            if delta is not None:
                color = "C0392B" if delta > 0 else "27AE60"
                for col in [6,7]: ws.cell(row=row,column=col).font = Font(bold=True,color=color)
            row += 1
        self._widths(ws, [12,45,18,12,12,12,12,18,50]); ws.freeze_panes = "A2"

    def _sheet_promotions(self, wb, data):
        ws = wb.create_sheet("Promotions")
        self._hdr(ws, ["Marque","Nom","Famille","Prix promo","Original","Remise %","Best Seller","URL"], color="8E44AD")
        bm = data["brand_map"]
        prods_sale = [(p, data["snapshots"][p.id]) for p in data["products"]
                      if p.id in data["snapshots"] and data["snapshots"][p.id].on_sale]
        row = 2
        for p, snap in sorted(prods_sale, key=lambda x: x[1].discount_pct or 0, reverse=True):
            self._row(ws, [bm.get(p.brand_id,""),p.name,p.family or "",snap.price,
                snap.original_price,snap.discount_pct,"⭐" if p.is_best_seller else "",p.url], row, row%2==0)
            row += 1
        self._widths(ws, [12,45,18,12,14,10,12,50]); ws.freeze_panes = "A2"

    def _sheet_suppressions(self, wb, data):
        ws = wb.create_sheet("Suppressions")
        self._hdr(ws, ["Marque","Nom","Famille","Dernière vue","Date suppression","URL"], color="C0392B")
        bm = data["brand_map"]
        removed = [p for p in data["products"] if not p.is_active]
        row = 2
        for p in sorted(removed, key=lambda x: x.removed_at or datetime.min, reverse=True):
            self._row(ws, [bm.get(p.brand_id,""),p.name,p.family or "",
                p.last_seen.strftime("%Y-%m-%d") if p.last_seen else "",
                p.removed_at.strftime("%Y-%m-%d") if p.removed_at else "",p.url], row, row%2==0)
            row += 1
        self._widths(ws, [12,45,18,14,20,50]); ws.freeze_panes = "A2"
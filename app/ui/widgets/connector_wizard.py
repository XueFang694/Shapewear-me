"""
ConnectorWizard — Wizard guidé de création d'un nouveau connecteur.

Phase 3 : permet à l'utilisateur de créer un connecteur complet sans
éditer de fichiers manuellement.

Étapes :
  1. Saisie de l'URL du site cible
  2. Détection automatique du moteur (Shopify JSON / HTML)
  3. Découverte des collections disponibles
  4. Configuration (rate-limit, délais, collections cibles)
  5. Test de connexion et validation
  6. Génération des fichiers (config.yml, mappings.py, connector.py)

Usage :
    wizard = ConnectorWizardDialog(parent=main_window)
    if wizard.exec():
        slug = wizard.get_slug()
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, Signal, QObject, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.logger import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

class DetectionWorker(QObject):
    """Détecte le moteur du site et liste les collections disponibles."""

    finished = Signal(dict)  # {"engine": str, "collections": [...], "error": str|None}

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url.rstrip("/")

    def run(self) -> None:
        result: dict[str, Any] = {
            "engine":      "shopify_json",
            "collections": [],
            "base_url":    self._url,
            "error":       None,
        }
        try:
            import httpx

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
                ),
                "Accept": "application/json, text/html",
            }
            client = httpx.Client(
                headers=headers,
                follow_redirects=True,
                timeout=15.0,
            )

            # Test Shopify JSON
            shopify_url = f"{self._url}/products.json?limit=1"
            try:
                resp = client.get(shopify_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if "products" in data:
                        result["engine"] = "shopify_json"
                        # Récupérer les collections
                        coll_url = f"{self._url}/collections.json?limit=100"
                        cr = client.get(coll_url)
                        if cr.status_code == 200:
                            colls = cr.json().get("collections", [])
                            result["collections"] = [
                                {"slug": c.get("handle", ""), "name": c.get("title", "")}
                                for c in colls
                                if c.get("handle")
                            ]
                        client.close()
                        self.finished.emit(result)
                        return
            except Exception:
                pass

            # Fallback HTML
            try:
                resp = client.get(self._url)
                if resp.status_code == 200:
                    result["engine"] = "html"
                    result["collections"] = []
                else:
                    result["error"] = f"Site inaccessible (HTTP {resp.status_code})"
            except Exception as exc:
                result["error"] = f"Impossible d'atteindre le site : {exc}"

            client.close()

        except Exception as exc:
            result["error"] = str(exc)

        self.finished.emit(result)


class GenerationWorker(QObject):
    """Génère les fichiers du connecteur."""

    finished = Signal(bool, str)  # (success, message)

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config

    def run(self) -> None:
        try:
            self._generate()
            self.finished.emit(True, "Connecteur créé avec succès !")
        except Exception as exc:
            log.error("Erreur génération connecteur", error=str(exc))
            self.finished.emit(False, str(exc))

    def _generate(self) -> None:
        slug       = self._config["slug"]
        name       = self._config["name"]
        base_url   = self._config["base_url"]
        engine     = self._config["engine"]
        delay_min  = self._config.get("delay_min", 2.0)
        delay_max  = self._config.get("delay_max", 5.0)
        rps        = self._config.get("rate_limit_rps", 0.4)
        collections = self._config.get("collections", [])

        # Dossier connecteur
        project_root = Path(__file__).resolve().parents[4]
        connector_dir = project_root / "app" / "connectors" / slug
        connector_dir.mkdir(parents=True, exist_ok=True)

        # __init__.py
        (connector_dir / "__init__.py").write_text("", encoding="utf-8")

        # config.yml
        coll_yaml = "\n".join(f"  - {c}" for c in collections) if collections else "  - all"
        config_yml = f"""name: {name}
slug: {slug}
base_url: {base_url}
version: "1.0"
engine: {engine}
rate_limit_rps: {rps}
delay_min: {delay_min}
delay_max: {delay_max}

pagination:
  type: offset
  param: page
  page_size: 250
  max_pages: 100

product_list_endpoint: /products.json

target_collections:
{coll_yaml}

best_seller_tags:
  - "best seller"
  - bestseller
  - "best-seller"
  - "top seller"

headers:
  Accept-Language: en-US,en;q=0.9
  Accept: text/html,application/json
  User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
"""
        (connector_dir / "config.yml").write_text(config_yml, encoding="utf-8")

        # mappings.py
        name_cap = name.replace(" ", "").title()
        mappings_py = f'''"""Mappings {name}."""
from __future__ import annotations
from app.connectors.spanx.mappings import (
    normalize_price, normalize_availability, extract_variants_detailed,
    extract_sizes, extract_colors, extract_materials, clean_description,
)

CATEGORY_MAPPINGS: dict[str, str] = {{
    "bodysuits": "Bodysuit",
    "bras":      "Bra",
    "shorts":    "Shaper Short",
    "leggings":  "Shaper Legging",
    "underwear": "Panty",
    "tanks":     "Tank",
    "swim":      "Swimwear",
}}

_BS_TAGS = {{"best seller", "bestseller", "best-seller", "top seller"}}


def extract_best_seller_{slug.replace("-", "_")}(tags: list[str] | str, config_tags: list[str] | None = None) -> bool:
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",")]
    check = _BS_TAGS
    if config_tags:
        check = check | {{t.lower() for t in config_tags}}
    return any(t.strip().lower() in check for t in tags)


def map_category_{slug.replace("-", "_")}(raw: str | None) -> str | None:
    if not raw:
        return None
    return CATEGORY_MAPPINGS.get(raw.lower().strip())
'''
        (connector_dir / "mappings.py").write_text(mappings_py, encoding="utf-8")

        # connector.py
        fn_suffix = slug.replace("-", "_")
        connector_py = f'''"""Connecteur {name} — moteur {engine}."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from app.connectors.base import BaseConnector, Category, ConnectorMeta, RawProduct
from app.connectors.{slug}.mappings import (
    extract_best_seller_{fn_suffix}, map_category_{fn_suffix},
)
from app.connectors.spanx.mappings import (
    clean_description, extract_colors, extract_materials,
    extract_rating_and_reviews, extract_sizes, extract_variants_detailed,
    normalize_availability, normalize_price,
)
from app.core.exceptions import ConnectorParseError
from app.core.logger import get_logger

log = get_logger(__name__)
_CONFIG_PATH = Path(__file__).parent / "config.yml"


class {name_cap}Connector(BaseConnector):
    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path=config_path or _CONFIG_PATH)

    def get_metadata(self) -> ConnectorMeta:
        return ConnectorMeta(
            name="{name}", slug="{slug}", version="1.0",
            engine="{engine}", base_url=self.base_url,
        )

    def get_categories(self) -> list[Category]:
        return [
            Category(
                slug=s,
                name=s.replace("-", " ").title(),
                url=f"{{self.base_url}}/collections/{{s}}",
                brand_slug="{slug}",
            )
            for s in self._config.get("target_collections", [])
        ]

    def get_product_urls(self, category: Category) -> list[str]:
        from app.scraping.http_client import HttpClient
        from app.scraping.pagination import PaginationHandler
        client = HttpClient(
            delay_min=self.delay_min,
            delay_max=self.delay_max,
            headers=self._config.get("headers", {{}}),
        )
        pg = self._config.get("pagination", {{}})
        paginator = PaginationHandler(
            pagination_type=pg.get("type", "offset"),
            page_size=pg.get("page_size", 250),
            max_pages=pg.get("max_pages", 100),
        )
        base = f"{{self.base_url}}/collections/{{category.slug}}/products.json"
        handles: list[str] = []
        for url in paginator.iter_pages(base):
            try:
                r = client.get(url)
                if r.status_code != 200:
                    break
                products = r.json().get("products", [])
                if not products:
                    break
                handles.extend(p["handle"] for p in products if p.get("handle"))
                if len(products) < pg.get("page_size", 250):
                    break
            except Exception as exc:
                log.error("Erreur pagination {name}", url=url, error=str(exc))
                break
        urls = [f"{{self.base_url}}/products/{{h}}.json" for h in handles]
        log.info("URLs {name}", category=category.slug, count=len(urls))
        return urls

    def parse_product(self, url: str, data: str | dict) -> RawProduct:
        if not isinstance(data, dict):
            raise ConnectorParseError("dict attendu", context={{"url": url}})
        try:
            return self._parse(url, data)
        except (KeyError, TypeError, ValueError) as exc:
            raise ConnectorParseError(
                f"Erreur {name}: {{exc}}", context={{"url": url}}
            ) from exc

    def _parse(self, url: str, p: dict[str, Any]) -> RawProduct:
        variants = p.get("variants", [])
        options  = p.get("options", [])
        tags_raw = p.get("tags", [])
        tags = (
            [t.strip() for t in tags_raw.split(",")]
            if isinstance(tags_raw, str)
            else list(tags_raw)
        )
        fv      = variants[0] if variants else {{}}
        price   = normalize_price(fv.get("price"))
        compare = normalize_price(fv.get("compare_at_price"))
        on_sale = bool(compare and price and compare > price)
        category_raw = p.get("product_type") or next(
            (t for t in tags if map_category_{fn_suffix}(t)), None
        )
        materials = extract_materials(p.get("body_html"))
        rating, review_count = extract_rating_and_reviews(p.get("metafields"))
        return RawProduct(
            external_id=str(p.get("id", p.get("handle", ""))),
            url=url.replace(".json", ""),
            name=p.get("title", "").strip(),
            brand_slug="{slug}",
            price=price,
            original_price=compare if on_sale else None,
            currency="USD",
            on_sale=on_sale,
            category_raw=category_raw,
            description=clean_description(p.get("body_html")),
            images=[img["src"] for img in p.get("images", []) if img.get("src")],
            sizes=extract_sizes(variants),
            colors=extract_colors(variants),
            variants=extract_variants_detailed(variants, options),
            availability=normalize_availability(variants),
            rating=rating,
            review_count=review_count,
            extra={{
                "handle":       p.get("handle"),
                "tags":         tags,
                "vendor":       p.get("vendor"),
                "is_best_seller": extract_best_seller_{fn_suffix}(
                    tags, self._config.get("best_seller_tags")
                ),
                "materials":    materials,
                "detailed_variants": extract_variants_detailed(variants, options),
            }},
        )
'''
        (connector_dir / "connector.py").write_text(connector_py, encoding="utf-8")
        log.info("Connecteur généré", slug=slug, path=str(connector_dir))


# ---------------------------------------------------------------------------
# Pages du wizard
# ---------------------------------------------------------------------------

def _sep() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setStyleSheet("color: #E2E8F0;")
    return f


def _title(text: str, size: int = 13) -> QLabel:
    lbl = QLabel(text)
    f = QFont()
    f.setPointSize(size)
    f.setBold(True)
    lbl.setFont(f)
    lbl.setStyleSheet("color: #1E293B;")
    return lbl


def _subtitle(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("color: #64748B; font-size: 10pt;")
    return lbl


class PageUrl(QWidget):
    """Étape 1 — URL du site cible."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(_title("Nouveau connecteur"))
        lay.addWidget(_subtitle(
            "Entrez l'URL de base du site e-commerce à analyser. "
            "Le wizard détecte automatiquement s'il s'agit d'un site Shopify."
        ))
        lay.addWidget(_sep())

        url_grp = QGroupBox("URL du site")
        url_grp.setStyleSheet(
            "QGroupBox { border: 1px solid #E2E8F0; border-radius: 8px; "
            "background: white; padding: 12px; margin-top: 8px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px; color: #475569; }"
        )
        g_lay = QVBoxLayout(url_grp)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://www.exemple.com")
        self.url_input.setMinimumHeight(36)
        self.url_input.setStyleSheet(
            "QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; "
            "padding: 6px 10px; font-size: 10pt; }"
            "QLineEdit:focus { border-color: #2563EB; }"
        )
        g_lay.addWidget(self.url_input)

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Nom de la marque (ex: Maidenform)")
        self.name_input.setMinimumHeight(36)
        self.name_input.setStyleSheet(self.url_input.styleSheet())
        g_lay.addWidget(QLabel("Nom de la marque :"))
        g_lay.addWidget(self.name_input)

        self.slug_input = QLineEdit()
        self.slug_input.setPlaceholderText("slug-minuscules-sans-espaces (ex: maidenform)")
        self.slug_input.setMinimumHeight(36)
        self.slug_input.setStyleSheet(self.url_input.styleSheet())
        g_lay.addWidget(QLabel("Identifiant technique (slug) :"))
        g_lay.addWidget(self.slug_input)

        # Auto-slug depuis le nom
        self.name_input.textChanged.connect(self._auto_slug)

        lay.addWidget(url_grp)
        lay.addStretch()

        tip = QLabel(
            "💡 Comment savoir si un site est Shopify ? "
            "Testez https://site.com/products.json dans votre navigateur. "
            "Si vous obtenez du JSON, c'est Shopify."
        )
        tip.setWordWrap(True)
        tip.setStyleSheet(
            "background: #EFF6FF; border: 1px solid #BFDBFE; border-radius: 6px; "
            "padding: 8px 12px; color: #1D4ED8; font-size: 9pt;"
        )
        lay.addWidget(tip)

    def _auto_slug(self, text: str) -> None:
        slug = re.sub(r"[^a-z0-9-]", "-", text.lower().strip())
        slug = re.sub(r"-+", "-", slug).strip("-")
        self.slug_input.setText(slug)


class PageDetection(QWidget):
    """Étape 2 — Détection automatique du moteur."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(_title("Détection du moteur"))
        lay.addWidget(_subtitle("Analyse du site en cours…"))
        lay.addWidget(_sep())

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(12)
        self.progress.setStyleSheet(
            "QProgressBar { border: 1px solid #CBD5E1; border-radius: 6px; background: #F1F5F9; }"
            "QProgressBar::chunk { background: #2563EB; border-radius: 6px; }"
        )
        lay.addWidget(self.progress)

        self.result_label = QLabel("")
        self.result_label.setWordWrap(True)
        self.result_label.setStyleSheet("font-size: 10pt; padding: 8px 0;")
        lay.addWidget(self.result_label)

        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        self.detail_text.setMaximumHeight(120)
        self.detail_text.setStyleSheet(
            "QTextEdit { background: #F8FAFC; border: 1px solid #E2E8F0; "
            "border-radius: 6px; font-family: monospace; font-size: 8pt; }"
        )
        lay.addWidget(self.detail_text)
        lay.addStretch()

    def set_detecting(self, url: str) -> None:
        self.progress.setRange(0, 0)
        self.result_label.setText(f"Analyse de {url}…")
        self.detail_text.clear()

    def set_result(self, result: dict) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if result.get("error"):
            self.result_label.setText(f"❌ Erreur : {result['error']}")
            self.result_label.setStyleSheet("color: #DC2626; font-size: 10pt;")
        else:
            engine = result.get("engine", "inconnu")
            icon = "🟢" if engine == "shopify_json" else "🟡"
            label = "Shopify JSON API (recommandé)" if engine == "shopify_json" else "HTML"
            self.result_label.setText(f"{icon} Moteur détecté : {label}")
            self.result_label.setStyleSheet("color: #16A34A; font-size: 10pt; font-weight: bold;")
            n_coll = len(result.get("collections", []))
            self.detail_text.setPlainText(
                f"URL de base : {result.get('base_url', '—')}\n"
                f"Moteur      : {engine}\n"
                f"Collections : {n_coll} trouvées"
            )


class PageCollections(QWidget):
    """Étape 3 — Sélection des collections à crawler."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(_title("Collections à cibler"))
        lay.addWidget(_subtitle(
            "Sélectionnez les collections (catégories) à inclure dans le crawl. "
            "Pour un site shapewear, ciblez typiquement : bodysuits, shorts, bras…"
        ))
        lay.addWidget(_sep())

        btn_row = QHBoxLayout()
        btn_all  = QPushButton("Tout sélectionner")
        btn_none = QPushButton("Tout désélectionner")
        for btn in (btn_all, btn_none):
            btn.setStyleSheet(
                "QPushButton { border: 1px solid #CBD5E1; border-radius: 4px; "
                "padding: 3px 10px; font-size: 9pt; }"
            )
        btn_all.clicked.connect(self._select_all)
        btn_none.clicked.connect(self._select_none)
        btn_row.addWidget(btn_all)
        btn_row.addWidget(btn_none)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(
            "QListWidget { border: 1px solid #E2E8F0; border-radius: 6px; background: white; }"
            "QListWidget::item { padding: 5px 8px; }"
            "QListWidget::item:selected { background: #EFF6FF; color: #1E293B; }"
        )
        lay.addWidget(self.list_widget)

        # Champ pour ajouter manuellement
        add_row = QHBoxLayout()
        self.manual_input = QLineEdit()
        self.manual_input.setPlaceholderText("Ajouter une collection manuellement…")
        self.manual_input.setStyleSheet(
            "QLineEdit { border: 1px solid #CBD5E1; border-radius: 6px; padding: 4px 8px; }"
        )
        btn_add = QPushButton("+ Ajouter")
        btn_add.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 4px; padding: 4px 10px; }"
        )
        btn_add.clicked.connect(self._add_manual)
        self.manual_input.returnPressed.connect(self._add_manual)
        add_row.addWidget(self.manual_input, 1)
        add_row.addWidget(btn_add)
        lay.addLayout(add_row)

    def load_collections(self, collections: list[dict]) -> None:
        self.list_widget.clear()
        for coll in collections:
            item = QListWidgetItem(f"{coll['name']}  ({coll['slug']})")
            item.setData(Qt.UserRole, coll["slug"])
            item.setCheckState(Qt.Unchecked)
            self.list_widget.addItem(item)

    def get_selected(self) -> list[str]:
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                result.append(item.data(Qt.UserRole))
        return result

    def _select_all(self) -> None:
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Checked)

    def _select_none(self) -> None:
        for i in range(self.list_widget.count()):
            self.list_widget.item(i).setCheckState(Qt.Unchecked)

    def _add_manual(self) -> None:
        slug = self.manual_input.text().strip().lower().replace(" ", "-")
        if not slug:
            return
        item = QListWidgetItem(f"{slug}  (manuel)")
        item.setData(Qt.UserRole, slug)
        item.setCheckState(Qt.Checked)
        self.list_widget.addItem(item)
        self.manual_input.clear()


class PageConfig(QWidget):
    """Étape 4 — Configuration du rate-limiting et des délais."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(_title("Configuration du crawl"))
        lay.addWidget(_subtitle(
            "Définissez les délais entre requêtes. "
            "Des valeurs trop faibles risquent un blocage ; trop élevées ralentissent le crawl."
        ))
        lay.addWidget(_sep())

        grp = QGroupBox("Paramètres de scraping")
        grp.setStyleSheet(
            "QGroupBox { border: 1px solid #E2E8F0; border-radius: 8px; "
            "background: white; padding: 12px; margin-top: 8px; }"
        )
        g = QVBoxLayout(grp)

        def spin_row(label: str, default: float, mn: float, mx: float, step: float, suffix: str):
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setMinimumWidth(200)
            spin = QDoubleSpinBox()
            spin.setRange(mn, mx)
            spin.setSingleStep(step)
            spin.setSuffix(suffix)
            spin.setValue(default)
            spin.setFixedWidth(100)
            row.addWidget(lbl)
            row.addWidget(spin)
            row.addStretch()
            w = QWidget()
            w.setLayout(row)
            return w, spin

        row1, self.delay_min = spin_row("Délai minimum entre requêtes", 2.0, 0.5, 20.0, 0.5, " s")
        row2, self.delay_max = spin_row("Délai maximum entre requêtes", 5.0, 1.0, 30.0, 0.5, " s")
        row3, self.rps       = spin_row("Requêtes par seconde (max)", 0.4, 0.1, 2.0, 0.1, " req/s")
        g.addWidget(row1)
        g.addWidget(row2)
        g.addWidget(row3)
        lay.addWidget(grp)

        # Recommandations
        tip = QLabel(
            "📋 Recommandations :\n"
            "• Shopify standard : 1.5–4 s\n"
            "• Site protégé (Cloudflare) : 3–8 s\n"
            "• Site sans protection détectée : 1–3 s"
        )
        tip.setStyleSheet(
            "background: #F0FDF4; border: 1px solid #BBF7D0; border-radius: 6px; "
            "padding: 10px 14px; color: #14532D; font-size: 9pt;"
        )
        lay.addWidget(tip)
        lay.addStretch()


class PageGenerate(QWidget):
    """Étape 5 — Génération et résumé."""

    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(_title("Génération du connecteur"))
        lay.addWidget(_sep())

        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setStyleSheet(
            "QTextEdit { background: #F8FAFC; border: 1px solid #E2E8F0; "
            "border-radius: 6px; font-family: monospace; font-size: 9pt; }"
        )
        lay.addWidget(self.summary)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 10pt; font-weight: bold; padding: 8px 0;")
        lay.addWidget(self.status_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(12)
        self.progress.setStyleSheet(
            "QProgressBar { border: 1px solid #CBD5E1; border-radius: 6px; background: #F1F5F9; }"
            "QProgressBar::chunk { background: #16A34A; border-radius: 6px; }"
        )
        self.progress.hide()
        lay.addWidget(self.progress)
        lay.addStretch()

    def set_summary(self, config: dict) -> None:
        slug        = config.get("slug", "?")
        name        = config.get("name", "?")
        base_url    = config.get("base_url", "?")
        engine      = config.get("engine", "?")
        collections = config.get("collections", [])
        coll_str    = ", ".join(collections) or "(aucune)"
        self.summary.setPlainText(
            f"Connecteur   : {name}\n"
            f"Slug         : {slug}\n"
            f"URL          : {base_url}\n"
            f"Moteur       : {engine}\n"
            f"Collections  : {coll_str}\n"
            f"Délai        : {config.get('delay_min', 2)}–{config.get('delay_max', 5)} s\n\n"
            f"Fichiers qui seront créés :\n"
            f"  app/connectors/{slug}/__init__.py\n"
            f"  app/connectors/{slug}/config.yml\n"
            f"  app/connectors/{slug}/mappings.py\n"
            f"  app/connectors/{slug}/connector.py\n"
        )
        self.status_label.setText("")

    def set_generating(self) -> None:
        self.progress.show()
        self.status_label.setText("Génération en cours…")
        self.status_label.setStyleSheet("color: #2563EB; font-size: 10pt;")

    def set_done(self, success: bool, message: str) -> None:
        self.progress.hide()
        if success:
            self.status_label.setText(f"✅ {message}")
            self.status_label.setStyleSheet("color: #16A34A; font-size: 10pt; font-weight: bold;")
        else:
            self.status_label.setText(f"❌ Erreur : {message}")
            self.status_label.setStyleSheet("color: #DC2626; font-size: 10pt;")


# ---------------------------------------------------------------------------
# Dialog principal
# ---------------------------------------------------------------------------

class ConnectorWizardDialog(QDialog):
    """Dialog wizard multi-étapes pour créer un nouveau connecteur."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouveau connecteur — Wizard")
        self.setMinimumSize(640, 520)
        self.setStyleSheet("QDialog { background: #F8FAFC; }")

        self._detection_result: dict = {}
        self._final_config:     dict = {}
        self._gen_worker:  GenerationWorker | None = None
        self._gen_thread:  QThread | None = None
        self._det_worker:  DetectionWorker | None = None
        self._det_thread:  QThread | None = None

        self._build_ui()

    # ── Construction ─────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Barre de progression des étapes
        steps_bar = self._build_steps_bar()
        root.addWidget(steps_bar)

        # Stack des pages
        self._stack = QStackedWidget()
        self._page_url    = PageUrl()
        self._page_detect = PageDetection()
        self._page_colls  = PageCollections()
        self._page_cfg    = PageConfig()
        self._page_gen    = PageGenerate()
        for page in (self._page_url, self._page_detect, self._page_colls,
                     self._page_cfg, self._page_gen):
            self._stack.addWidget(page)
        root.addWidget(self._stack, 1)

        # Boutons navigation
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(20, 12, 20, 16)
        self._btn_back = QPushButton("← Précédent")
        self._btn_back.setEnabled(False)
        self._btn_back.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 6px 16px; }"
            "QPushButton:hover { background: #F1F5F9; }"
            "QPushButton:disabled { color: #94A3B8; }"
        )
        self._btn_next = QPushButton("Suivant →")
        self._btn_next.setStyleSheet(
            "QPushButton { background: #2563EB; color: white; border-radius: 6px; "
            "font-weight: bold; padding: 6px 20px; }"
            "QPushButton:hover { background: #1D4ED8; }"
        )
        self._btn_cancel = QPushButton("Annuler")
        self._btn_cancel.setStyleSheet(
            "QPushButton { border: 1px solid #CBD5E1; border-radius: 6px; padding: 6px 14px; }"
        )
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_back.clicked.connect(self._go_back)
        self._btn_next.clicked.connect(self._go_next)
        btn_row.addWidget(self._btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self._btn_back)
        btn_row.addWidget(self._btn_next)
        root.addLayout(btn_row)

        self._update_steps(0)

    def _build_steps_bar(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(48)
        w.setStyleSheet("background: #1E293B;")
        lay = QHBoxLayout(w)
        lay.setContentsMargins(20, 0, 20, 0)
        steps = ["URL", "Détection", "Collections", "Configuration", "Génération"]
        self._step_labels: list[QLabel] = []
        for i, s in enumerate(steps):
            lbl = QLabel(f"{i+1}. {s}")
            lbl.setStyleSheet("color: #475569; font-size: 9pt; padding: 0 8px;")
            lay.addWidget(lbl)
            self._step_labels.append(lbl)
            if i < len(steps) - 1:
                sep = QLabel("›")
                sep.setStyleSheet("color: #334155;")
                lay.addWidget(sep)
        return w

    def _update_steps(self, current: int) -> None:
        for i, lbl in enumerate(self._step_labels):
            if i < current:
                lbl.setStyleSheet("color: #16A34A; font-size: 9pt; padding: 0 8px;")
            elif i == current:
                lbl.setStyleSheet(
                    "color: white; font-weight: bold; font-size: 9pt; padding: 0 8px;"
                )
            else:
                lbl.setStyleSheet("color: #475569; font-size: 9pt; padding: 0 8px;")

    # ── Navigation ────────────────────────────────────────────────────────

    def _go_next(self) -> None:
        idx = self._stack.currentIndex()
        if idx == 0:
            self._validate_url_and_detect()
        elif idx == 1:
            if not self._detection_result or self._detection_result.get("error"):
                QMessageBox.warning(self, "Erreur", "La détection n'a pas réussi. Vérifiez l'URL.")
                return
            self._stack.setCurrentIndex(2)
            self._page_colls.load_collections(self._detection_result.get("collections", []))
            self._btn_back.setEnabled(True)
            self._update_steps(2)
        elif idx == 2:
            self._stack.setCurrentIndex(3)
            self._update_steps(3)
        elif idx == 3:
            config = self._build_config()
            self._final_config = config
            self._page_gen.set_summary(config)
            self._stack.setCurrentIndex(4)
            self._btn_next.setText("Créer le connecteur")
            self._update_steps(4)
        elif idx == 4:
            self._generate()

    def _go_back(self) -> None:
        idx = self._stack.currentIndex()
        if idx > 0:
            self._stack.setCurrentIndex(idx - 1)
            self._update_steps(idx - 1)
            self._btn_back.setEnabled(idx - 1 > 0)
            if idx - 1 < 4:
                self._btn_next.setText("Suivant →")

    # ── Étape 1 : validation + détection ─────────────────────────────────

    def _validate_url_and_detect(self) -> None:
        url  = self._page_url.url_input.text().strip()
        name = self._page_url.name_input.text().strip()
        slug = self._page_url.slug_input.text().strip()

        if not url.startswith("http"):
            QMessageBox.warning(self, "URL invalide", "L'URL doit commencer par http:// ou https://")
            return
        if not name:
            QMessageBox.warning(self, "Nom manquant", "Veuillez saisir le nom de la marque.")
            return
        if not slug or not re.match(r"^[a-z0-9-]+$", slug):
            QMessageBox.warning(self, "Slug invalide",
                                "Le slug ne peut contenir que des lettres minuscules, "
                                "chiffres et tirets.")
            return

        # Vérifier que le slug n'existe pas déjà
        project_root = Path(__file__).resolve().parents[4]
        target = project_root / "app" / "connectors" / slug
        if target.exists():
            QMessageBox.warning(self, "Slug existant",
                                f"Un connecteur '{slug}' existe déjà. Choisissez un autre slug.")
            return

        self._stack.setCurrentIndex(1)
        self._btn_back.setEnabled(True)
        self._btn_next.setEnabled(False)
        self._update_steps(1)
        self._page_detect.set_detecting(url)

        # Lancer la détection
        self._det_worker = DetectionWorker(url)
        self._det_thread = QThread()
        self._det_worker.moveToThread(self._det_thread)
        self._det_thread.started.connect(self._det_worker.run)
        self._det_worker.finished.connect(self._on_detection_done)
        self._det_worker.finished.connect(self._det_thread.quit)
        self._det_thread.finished.connect(self._det_worker.deleteLater)
        self._det_thread.finished.connect(self._det_thread.deleteLater)
        self._det_thread.start()

    def _on_detection_done(self, result: dict) -> None:
        self._detection_result = result
        self._page_detect.set_result(result)
        self._btn_next.setEnabled(True)

    # ── Étape finale : génération ─────────────────────────────────────────

    def _build_config(self) -> dict:
        return {
            "slug":         self._page_url.slug_input.text().strip(),
            "name":         self._page_url.name_input.text().strip(),
            "base_url":     self._page_url.url_input.text().strip().rstrip("/"),
            "engine":       self._detection_result.get("engine", "shopify_json"),
            "collections":  self._page_colls.get_selected(),
            "delay_min":    self._page_cfg.delay_min.value(),
            "delay_max":    self._page_cfg.delay_max.value(),
            "rate_limit_rps": self._page_cfg.rps.value(),
        }

    def _generate(self) -> None:
        self._page_gen.set_generating()
        self._btn_next.setEnabled(False)
        self._btn_back.setEnabled(False)

        self._gen_worker = GenerationWorker(self._final_config)
        self._gen_thread = QThread()
        self._gen_worker.moveToThread(self._gen_thread)
        self._gen_thread.started.connect(self._gen_worker.run)
        self._gen_worker.finished.connect(self._on_generation_done)
        self._gen_worker.finished.connect(self._gen_thread.quit)
        self._gen_thread.finished.connect(self._gen_worker.deleteLater)
        self._gen_thread.finished.connect(self._gen_thread.deleteLater)
        self._gen_thread.start()

    def _on_generation_done(self, success: bool, message: str) -> None:
        self._page_gen.set_done(success, message)
        if success:
            self._btn_next.setText("Fermer")
            self._btn_next.setEnabled(True)
            self._btn_next.clicked.disconnect()
            self._btn_next.clicked.connect(self.accept)
        else:
            self._btn_back.setEnabled(True)
            self._btn_next.setEnabled(True)

    def get_slug(self) -> str:
        return self._final_config.get("slug", "")
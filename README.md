# Market Intelligence Platform — Shapewear US

Plateforme de veille concurrentielle dédiée au marché américain du shapewear.
Collecte automatisée, normalisation, classification et analyse des catalogues produits de plusieurs marques.

---

## Sommaire

1. [Vision du projet](#1-vision-du-projet)
2. [Architecture logicielle](#2-architecture-logicielle)
3. [Arborescence du projet](#3-arborescence-du-projet)
4. [Description des modules](#4-description-des-modules)
5. [Modèle de données](#5-modèle-de-données)
6. [Modèle des connecteurs](#6-modèle-des-connecteurs)
7. [Taxonomie et classification](#7-taxonomie-et-classification)
8. [Moteur de scraping](#8-moteur-de-scraping)
9. [Moteur de workflow](#9-moteur-de-workflow)
10. [Interface utilisateur](#10-interface-utilisateur)
11. [Exports](#11-exports)
12. [Plan de développement](#12-plan-de-développement)
13. [Stack technique](#13-stack-technique)
14. [Installation et lancement](#14-installation-et-lancement)
15. [Créer un nouveau connecteur](#15-créer-un-nouveau-connecteur)
16. [Risques et mitigations](#16-risques-et-mitigations)
17. [Évolutions prévues](#17-évolutions-prévues)

---

## 1. Vision du projet

### Objectif

Construire une **plateforme d'intelligence marché** desktop, utilisable par un utilisateur métier non technique,
capable de collecter automatiquement les données produits de plusieurs marques de shapewear américaines,
de les normaliser dans un référentiel commun, et de produire des analyses exploitables.

### Ce que la plateforme permet de faire

- Suivre les collections et gammes de chaque marque
- Comparer les prix entre marques sur des produits comparables
- Détecter les nouveaux produits et les suppressions de catalogue
- Suivre les promotions (fréquence, amplitude, durée)
- Classifier automatiquement les produits dans une nomenclature commune
- Produire des tableaux de bord et des rapports exportables
- Historiser toutes les données pour une analyse dans le temps

### Périmètre initial

Marques couvertes au lancement :

- SPANX (spanx.com)
- SKIMS (skims.com)
- Honeylove (honeylove.com)
- Shapermint (shapermint.com)

Le système est conçu pour s'étendre à d'autres marques et d'autres secteurs
sans modifier le cœur de l'application.

### Principe fondamental

Le scraping est un composant parmi d'autres.
La valeur réelle est dans la **couche de normalisation** qui transforme
des catalogues hétérogènes en un référentiel commun exploitable dans le temps.

---

## 2. Architecture logicielle

### Cinq couches indépendantes

```
┌─────────────────────────────────────────────────────────┐
│                 COUCHE PRÉSENTATION                     │
│     Interface PySide6 · Dashboards · Exports            │
├─────────────────────────────────────────────────────────┤
│                 COUCHE ORCHESTRATION                    │
│     Workflow Engine · Session Manager · Reporter        │
├─────────────────────────────────────────────────────────┤
│                 COUCHE TRAITEMENT                       │
│     Scraping Engine · Normalizer · Classifier           │
│     Change Detector · Enricher                          │
├─────────────────────────────────────────────────────────┤
│                 COUCHE CONNECTEURS                      │
│     SPANX · SKIMS · Honeylove · Shapermint · …          │
├─────────────────────────────────────────────────────────┤
│                 COUCHE STOCKAGE                         │
│     SQLAlchemy ORM · SQLite / PostgreSQL · Alembic      │
└─────────────────────────────────────────────────────────┘
```

### Règle d'isolation

Chaque couche ne connaît que sa voisine directe.
Les connecteurs ne parlent qu'au Scraping Engine.
L'UI ne touche jamais la base de données directement.
Les connecteurs ignorent tout de la normalisation.

### Pattern architectural

- **MVP (Model-View-Presenter)** pour l'interface utilisateur
- **Repository pattern** pour l'accès aux données
- **Strategy pattern** pour les connecteurs (interchangeables)
- **Observer pattern** via bus d'événements pour la progression en temps réel
- **Pipeline pattern** pour la chaîne de traitement (normalize → classify → detect → store)

---

## 3. Arborescence du projet

```
Shapewear me/
│
├── app/
│   ├── __init__.py
│   │
│   ├── core/                          # Services transversaux
│   │   ├── __init__.py
│   │   ├── config.py                  # Paramètres globaux, chemins
│   │   ├── exceptions.py              # Hiérarchie d'exceptions métier
│   │   ├── logger.py                  # Logger centralisé, rotation fichiers
│   │   └── events.py                  # Bus pub/sub interne
│   │
│   ├── connectors/                    # Un dossier par marque
│   │   ├── __init__.py
│   │   ├── base.py                    # BaseConnector (ABC) — contrat
│   │   ├── registry.py                # Auto-découverte des connecteurs
│   │   ├── spanx/
│   │   │   ├── __init__.py
│   │   │   ├── connector.py           # Implémentation SPANX
│   │   │   ├── config.yml             # URLs, sélecteurs, rate-limit
│   │   │   └── mappings.py            # Champs bruts → champs normalisés
│   │   ├── skims/
│   │   │   ├── __init__.py
│   │   │   ├── connector.py
│   │   │   ├── config.yml
│   │   │   └── mappings.py
│   │   ├── honeylove/
│   │   │   ├── __init__.py
│   │   │   ├── connector.py
│   │   │   ├── config.yml
│   │   │   └── mappings.py
│   │   └── shapermint/
│   │       ├── __init__.py
│   │       ├── connector.py
│   │       ├── config.yml
│   │       └── mappings.py
│   │
│   ├── scraping/                      # Moteur de collecte
│   │   ├── __init__.py
│   │   ├── engine.py                  # Orchestrateur des crawls
│   │   ├── http_client.py             # Session HTTP, retry, rate-limit
│   │   ├── pagination.py              # Détection auto de la pagination
│   │   ├── parser.py                  # Extraction HTML / JSON
│   │   └── anti_block.py              # Rotation UA, délais, proxies
│   │
│   ├── processing/                    # Pipeline de transformation
│   │   ├── __init__.py
│   │   ├── normalizer.py              # RawProduct → NormalizedProduct
│   │   ├── classifier.py              # Classification taxonomique
│   │   ├── change_detector.py         # Diff entre crawls successifs
│   │   └── enricher.py                # Calcul des champs dérivés
│   │
│   ├── storage/                       # Couche persistance
│   │   ├── __init__.py
│   │   ├── models.py                  # Entités SQLAlchemy
│   │   ├── repository.py              # CRUD abstrait par entité
│   │   ├── database.py                # Connexion, session factory
│   │   └── migrations/                # Scripts Alembic
│   │
│   ├── workflow/                      # Orchestration des sessions
│   │   ├── __init__.py
│   │   ├── session.py                 # État d'une session d'analyse
│   │   ├── runner.py                  # Exécution des pipelines
│   │   ├── scheduler.py               # Planification (V2)
│   │   └── reporter.py                # Génération des rapports
│   │
│   ├── ui/                            # Interface PySide6
│   │   ├── __init__.py
│   │   ├── main_window.py             # Fenêtre principale, navigation
│   │   ├── views/
│   │   │   ├── __init__.py
│   │   │   ├── dashboard.py           # Vue synthèse et KPIs
│   │   │   ├── brands.py              # Gestion des connecteurs
│   │   │   ├── results.py             # Tableau des produits avec filtres
│   │   │   ├── history.py             # Historique des sessions
│   │   │   └── settings.py            # Paramètres de l'application
│   │   ├── widgets/
│   │   │   ├── __init__.py
│   │   │   ├── progress_bar.py        # Barre de progression session
│   │   │   ├── product_table.py       # Tableau produits réutilisable
│   │   │   ├── log_viewer.py          # Fenêtre de logs temps réel
│   │   │   └── connector_card.py      # Carte connecteur (statut, actions)
│   │   └── assets/                    # Icônes, fichiers QSS (styles)
│   │
│   └── exports/                       # Formateurs de sortie
│       ├── __init__.py
│       ├── csv_exporter.py
│       ├── excel_exporter.py          # openpyxl — feuilles structurées
│       ├── json_exporter.py
│       └── pdf_exporter.py            # Rapport HTML → PDF via weasyprint
│
├── taxonomies/                        # Fichiers YAML de classification
│   ├── shapewear.yml                  # Familles, sous-familles, attributs
│   ├── compression_levels.yml         # Niveaux de compression normalisés
│   ├── body_zones.yml                 # Zones corporelles ciblées
│   ├── color_normalization.yml        # Couleurs brutes → couleur canonique
│   └── size_normalization.yml         # Tailles brutes → taille normalisée
│
├── data/                              # Base SQLite locale, cache images
│   └── .gitkeep
│
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── __init__.py
│   │   ├── test_normalizer.py
│   │   ├── test_classifier.py
│   │   ├── test_change_detector.py
│   │   ├── test_connector_base.py
│   │   └── test_pagination.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── test_spanx_connector.py
│   │   ├── test_scraping_engine.py
│   │   └── test_workflow.py
│   └── fixtures/
│       ├── __init__.py
│       ├── sample_products.py         # Produits de test (RawProduct, NormalizedProduct)
│       └── mock_html.py               # HTML simulé pour les parsers
│
├── docs/
│   ├── connector_spec.md              # Spécification complète d'un connecteur
│   ├── taxonomy_guide.md              # Guide d'édition des taxonomies
│   ├── development_guide.md           # Guide de contribution
│   └── api_reference.md               # Référence des classes et méthodes
│
├── main.py                            # Point d'entrée application
├── pyproject.toml                     # Dépendances, metadata projet
├── .env.example                       # Variables d'environnement (modèle)
├── .gitignore
└── README.md                          # Ce fichier
```

---

## 4. Description des modules

### `app/core/`

Services transversaux utilisés par tous les autres modules.

**`config.py`**

Centralise tous les paramètres de l'application :
- Chemins vers les répertoires de données, d'exports, de logs
- Paramètres globaux de scraping (timeouts, retry par défaut)
- Chaîne de connexion à la base de données
- Mode d'exécution (dev / prod)

Charge d'abord les valeurs par défaut, puis écrase avec un fichier
`settings.json` utilisateur si présent.

```python
# Exemple d'utilisation
from app.core.config import settings

db_path = settings.DATABASE_URL
log_dir = settings.LOG_DIR
```

**`exceptions.py`**

Hiérarchie d'exceptions métier :

```
MarketIntelException (base)
├── ConnectorException
│   ├── ConnectorConfigError
│   ├── ConnectorParseError
│   └── ConnectorBlockedError
├── ScrapingException
│   ├── RateLimitError
│   ├── NetworkError
│   └── PaginationError
├── ProcessingException
│   ├── NormalizationError
│   └── ClassificationError
└── StorageException
    ├── DatabaseError
    └── MigrationError
```

**`logger.py`**

Configure un logger structuré avec :
- Rotation automatique des fichiers de logs (un fichier par session)
- Niveaux : DEBUG (fichier uniquement), INFO/WARNING/ERROR (fichier + UI)
- Émission des logs vers l'UI via le bus d'événements (pas de couplage direct)

```python
from app.core.logger import get_logger
log = get_logger(__name__)
log.info("Crawl démarré", brand="spanx", category="bodysuits")
```

**`events.py`**

Bus pub/sub interne léger.
Les modules émettent des événements, l'UI s'y abonne sans couplage direct.

Événements prévus :

```
crawl.session.started       { session_id, brands }
crawl.task.started          { task_id, brand, category }
crawl.task.progress         { task_id, current, total }
crawl.task.completed        { task_id, products_count }
crawl.task.failed           { task_id, error }
crawl.session.completed     { session_id, summary }
product.saved               { product_id, brand, is_new }
change.detected             { change_type, product_id, old_value, new_value }
log.message                 { level, message, context }
```

---

### `app/connectors/`

**`base.py` — BaseConnector**

Classe abstraite définissant le contrat que tout connecteur doit respecter.

Méthodes abstraites (obligatoires dans chaque connecteur) :

```python
@abstractmethod
def get_categories(self) -> list[Category]:
    """Retourne la liste des catégories du site."""

@abstractmethod
def get_product_urls(self, category: Category) -> list[str]:
    """Retourne toutes les URLs produits d'une catégorie (pagination incluse)."""

@abstractmethod
def parse_product(self, url: str, html: str) -> RawProduct:
    """Extrait les données d'une page produit et retourne un RawProduct."""

@abstractmethod
def get_metadata(self) -> ConnectorMeta:
    """Retourne les métadonnées du connecteur (nom, version, moteur)."""
```

Méthodes fournies par la base (non à réimplémenter) :

```python
def crawl_all(self, categories: list[Category]) -> Generator[RawProduct, None, None]:
    """Itère sur toutes les catégories et retourne les produits un par un."""

def validate_config(self) -> bool:
    """Valide que le config.yml du connecteur est bien formé."""

def test_connection(self) -> ConnectionStatus:
    """Teste la connectivité vers le site cible."""
```

**`registry.py` — ConnectorRegistry**

Auto-découverte des connecteurs disponibles.
Scanne les sous-dossiers de `app/connectors/` au démarrage.
Un dossier est reconnu comme connecteur s'il contient `connector.py` et `config.yml`.

```python
from app.connectors.registry import ConnectorRegistry

registry = ConnectorRegistry()
available = registry.list_connectors()     # ['spanx', 'skims', 'honeylove', ...]
connector = registry.get('spanx')          # Instance du connecteur SPANX
```

**Structure d'un connecteur — `config.yml`**

```yaml
name: SPANX
slug: spanx
base_url: https://www.spanx.com
version: "1.0"

engine: shopify_json          # shopify_json | html | graphql
rate_limit_rps: 0.5           # requêtes par seconde
delay_min: 1.5                # délai minimum entre requêtes (s)
delay_max: 4.0                # délai maximum (aléatoire)

pagination:
  type: offset                # offset | cursor | page_number | infinite_scroll
  param: page
  page_size: 250
  max_pages: 100

categories_url: /collections
product_list_endpoint: /products.json

# Sélecteurs CSS (utilisés en mode html uniquement)
selectors:
  product_name: h1.product__title
  price: span.price__current
  original_price: span.price__compare
  description: div.product__description
  images: div.product__media img[src]
  sizes: select.variant-selector[data-option="Size"] option
  colors: div.color-swatch[data-color]

headers:
  Accept-Language: en-US,en;q=0.9
  Accept: text/html,application/json
```

**Structure d'un connecteur — `mappings.py`**

Correspondances entre les champs bruts du site et les champs du modèle normalisé.
Peut contenir des fonctions de transformation simples.

```python
FIELD_MAPPINGS = {
    "title":         "name",
    "handle":        "external_id",
    "body_html":     "description",
    "vendor":        "brand_slug",
    "product_type":  "category_raw",
}

CATEGORY_MAPPINGS = {
    "bodysuits":      "Bodysuit",
    "shorts":         "Shaper Short",
    "leggings":       "Shaper Legging",
    "bras":           "Bra",
    "intimates":      "Panty",
}

def normalize_price(raw_price: str) -> float:
    """Supprime $ et virgules, retourne un float."""
    return float(raw_price.replace("$", "").replace(",", "").strip())
```

**Dataclass `RawProduct`**

Contrat de sortie de tout connecteur.
Tous les champs sont optionnels sauf `external_id`, `url`, `name`, `brand_slug`.

```python
@dataclass
class RawProduct:
    # Obligatoires
    external_id: str
    url: str
    name: str
    brand_slug: str

    # Prix
    price: float | None = None
    original_price: float | None = None
    currency: str = "USD"
    on_sale: bool = False

    # Classification brute
    category_raw: str | None = None
    subcategory_raw: str | None = None

    # Contenu
    description: str | None = None
    composition: str | None = None
    size_guide: str | None = None
    images: list[str] = field(default_factory=list)

    # Variantes
    sizes: list[str] = field(default_factory=list)
    colors: list[dict] = field(default_factory=list)
    variants: list[dict] = field(default_factory=list)

    # Disponibilité
    availability: str = "unknown"   # in_stock | out_of_stock | unknown

    # Avis
    rating: float | None = None
    review_count: int | None = None

    # Compression (si disponible brut)
    compression_level_raw: str | None = None
    target_zones_raw: list[str] = field(default_factory=list)

    # Extension libre
    extra: dict = field(default_factory=dict)

    # Métadonnées
    crawled_at: datetime = field(default_factory=datetime.utcnow)
```

---

### `app/scraping/`

**`engine.py` — ScrapingEngine**

Orchestrateur principal des crawls.
Reçoit un connecteur et une liste de catégories,
retourne un flux de `RawProduct` via un générateur.

Responsabilités :
- Itérer sur les catégories dans l'ordre
- Appeler `get_product_urls()` pour chaque catégorie
- Appeler `parse_product()` pour chaque URL
- Gérer le pool de threads (2–4 workers par défaut)
- Émettre les événements de progression
- Collecter les statistiques (total, erreurs, durée)

**`http_client.py` — HttpClient**

Encapsule `httpx` avec :
- Sessions persistantes avec gestion automatique des cookies
- Retry exponentiel (3 tentatives, délai 2× à chaque échec)
- Timeout configurable (connect: 10s, read: 30s)
- Support des proxies rotatifs (V2)
- Logging automatique de chaque requête (URL, statut, durée)

**`pagination.py` — PaginationHandler**

Stratégies de pagination supportées :

| Type | Description | Exemples de sites |
|------|-------------|-------------------|
| `offset` | `?page=1`, `?page=2` | SPANX (Shopify standard) |
| `cursor` | Token de pagination opaque | Sites Shopify avec `page_info` |
| `page_number` | Paramètre numérique classique | Sites WooCommerce |
| `infinite_scroll` | Détection du bouton "Load more" | Certains sites custom |

La détection automatique analyse la page de listing
et choisit la stratégie adaptée.

**`parser.py` — Parser**

Interface uniforme pour l'extraction de données.
Deux modes :

- **Mode JSON** : parsing de l'API Shopify (`/products.json`)
  préféré quand disponible — plus fiable que le scraping HTML
- **Mode HTML** : extraction via sélecteurs CSS définis dans `config.yml`

**`anti_block.py` — AntiBlockManager**

Gestion des protections anti-scraping :
- Pool de User-Agents rotatifs (50+ UA réels de navigateurs courants)
- Délais aléatoires entre requêtes (min/max configurables par connecteur)
- Détection de blocage : codes 403/429, présence de page CAPTCHA
- Stratégie de backoff en cas de détection : pause longue (60–300s) puis reprise
- Support des proxies HTTP/SOCKS (V2)

---

### `app/processing/`

**`normalizer.py` — Normalizer**

Transforme un `RawProduct` (sortie du connecteur) en `NormalizedProduct` (modèle fixe).

Opérations :
1. Application des `FIELD_MAPPINGS` du connecteur
2. Nettoyage des prix (suppression `$`, conversion `float`)
3. Calcul automatique de `on_sale` et `discount_pct`
4. Normalisation des couleurs via `taxonomies/color_normalization.yml`
5. Normalisation des tailles via `taxonomies/size_normalization.yml`
6. Nettoyage du texte (strip, suppression HTML de `description`)

**`classifier.py` — Classifier**

Reçoit un `NormalizedProduct` et assigne la classification taxonomique.

Processus :
1. Lecture de la taxonomie `taxonomies/shapewear.yml`
2. Correspondance `category_raw` → `family` + `subfamily`
3. Détection du `compression_level` (mots-clés dans le nom/description)
4. Détection des `target_zones` (zones corporelles mentionnées)
5. En cas d'absence de correspondance : flag `classification_manual_review = True`

En V2, un fallback LLM (via API Anthropic) traite les cas non couverts.

**`change_detector.py` — ChangeDetector**

Compare le produit entrant avec sa dernière version en base.
Génère des `ChangeEvent` pour chaque différence détectée.

Types de changements détectés :

```
product.new              Produit jamais vu auparavant
product.removed          Produit absent du crawl actuel mais présent précédemment
price.changed            Prix actuel ≠ prix précédent
sale.started             On_sale False → True
sale.ended               On_sale True → False
availability.changed     Disponibilité modifiée
variant.added            Nouvelle taille ou couleur
variant.removed          Taille ou couleur disparue
```

**`enricher.py` — Enricher**

Calcule des champs dérivés sur la base de l'historique :

- `discount_pct` : pourcentage de remise actuel
- `promo_frequency` : % des crawls où le produit était en promo
- `price_stability` : coefficient de variation du prix sur 90 jours
- `days_since_first_seen` : ancienneté du produit dans le catalogue
- `days_on_sale` : nombre de jours cumulés en promotion

---

### `app/storage/`

**`models.py` — Modèles SQLAlchemy**

Entités principales :

```python
class Brand(Base):
    id, slug, name, base_url, connector_id, active, created_at

class CrawlSession(Base):
    id, brand_id, started_at, ended_at, status,
    products_found, products_new, products_changed,
    products_removed, errors_count

class Product(Base):
    id, brand_id, external_id, url, name,
    category_raw, family, subfamily,
    compression_level, target_zones (JSON),
    is_active, first_seen, last_seen,
    classification_manual_review

class ProductSnapshot(Base):
    id, product_id, session_id,
    price, original_price, on_sale, discount_pct,
    currency, availability, crawled_at

class Variant(Base):
    id, product_id, color, size, available, sku

class ChangeEvent(Base):
    id, product_id, session_id, event_type,
    field_name, old_value, new_value, detected_at
```

**`repository.py` — Repositories**

Un repository par entité principale.
Toute requête base de données passe par ces classes.
Aucun SQL direct en dehors de ce module.

```python
class ProductRepository:
    def save(self, product: NormalizedProduct) -> Product
    def find_by_external_id(self, brand_id: int, external_id: str) -> Product | None
    def find_active_by_brand(self, brand_id: int) -> list[Product]
    def mark_as_removed(self, product_ids: list[int]) -> None
    def search(self, filters: ProductFilter) -> list[Product]

class SnapshotRepository:
    def save(self, snapshot: ProductSnapshot) -> ProductSnapshot
    def get_price_history(self, product_id: int, days: int) -> list[ProductSnapshot]
    def get_latest(self, product_id: int) -> ProductSnapshot | None

class ChangeEventRepository:
    def save(self, event: ChangeEvent) -> ChangeEvent
    def get_recent(self, hours: int) -> list[ChangeEvent]
    def get_by_type(self, event_type: str, since: datetime) -> list[ChangeEvent]
```

**`database.py`**

Factory de sessions SQLAlchemy.
Gestion des transactions avec context manager.

```python
from app.storage.database import get_db

with get_db() as db:
    product = db.query(Product).filter_by(id=42).first()
```

---

### `app/workflow/`

**`session.py` — CrawlSession**

Modélise l'état complet d'une session d'analyse en cours.

Attributs :
- `brands` : liste des marques à analyser
- `categories` : filtres de catégories (toutes par défaut)
- `tasks` : liste des `CrawlTask` (une par connecteur × catégorie)
- `status` : pending | running | completed | failed | cancelled
- `progress` : pourcentage global d'avancement
- `stats` : compteurs en temps réel (produits trouvés, erreurs, etc.)
- `started_at`, `ended_at`, `duration`

**`runner.py` — WorkflowRunner**

Exécute une session complète.

Séquence pour chaque tâche (connecteur × catégorie) :

```
1. Fetch URLs produits (get_product_urls)
2. Pour chaque URL → Fetch HTML → Parse (parse_product) → RawProduct
3. Normalize (normalizer.process)
4. Classify (classifier.classify)
5. Detect changes (change_detector.compare)
6. Enrich (enricher.enrich)
7. Store (repository.save + snapshot.save + change_events.save)
8. Émettre événements de progression
```

Parallélisme :
- Collecte HTTP : pool de 2–4 threads (configurable)
- Traitement (normalize/classify/store) : séquentiel pour éviter les conflits en base

**`reporter.py` — Reporter**

Génère un rapport de synthèse à la fin d'une session.

Contenu du rapport :
- Résumé exécutif (produits analysés, nouveautés, suppressions, changements)
- Tableau des nouveaux produits par marque
- Tableau des changements de prix (plus fortes hausses et baisses)
- Promotions actives avec durée
- Produits disparus du catalogue
- Métriques de crawl (durée, erreurs, taux de succès)

Formats de sortie : HTML (rendu dans l'UI), PDF, Excel.

---

### `app/ui/`

Interface desktop PySide6 (Qt6).
Pattern MVP : vues passives, présentateurs avec toute la logique.

**`main_window.py`**

Fenêtre principale avec barre de navigation latérale.
Cinq sections : Dashboard, Marques, Résultats, Historique, Paramètres.
Barre de statut en bas : état de la session en cours.
Bouton "Lancer l'analyse" accessible depuis toutes les vues.

**`views/dashboard.py`**

Vue d'accueil.
Contenu :
- 4 KPI cards (produits suivis, nouveaux, changements de prix, suppressions)
- Liste des marques actives avec leur statut et compteur produits
- Résumé de la dernière session
- Boutons : Lancer l'analyse, Exporter

**`views/brands.py`**

Gestion des connecteurs.
Contenu :
- Liste des connecteurs disponibles avec statut (actif/inactif, dernière connexion)
- Bouton "Tester la connexion" par connecteur
- Bouton "Modifier" (ouvre l'éditeur de config.yml)
- Bouton "Nouveau connecteur" (wizard guidé en V2, édition manuelle en V1)

**`views/results.py`**

Tableau de tous les produits avec filtres.
Filtres disponibles :
- Marque
- Famille / sous-famille
- Fourchette de prix
- Statut (actif, en promo, nouveau, supprimé)
- Session / période de crawl

Colonnes du tableau :
`Produit | Marque | Famille | Prix | Prix original | Remise | Dispo | Statut | Dernière MAJ`

Actions : clic sur une ligne → détail produit avec historique des prix.

**`views/history.py`**

Historique des sessions d'analyse.
Liste chronologique avec : date, marques analysées, nb produits, durée, statut.
Clic sur une session → détail complet + rapport.

**`views/settings.py`**

Paramètres de l'application :
- Délai entre requêtes (slider)
- Répertoire d'export
- Rotation User-Agent (toggle)
- Base de données (chemin, bouton de sauvegarde/restauration)
- Proxy HTTP (champ texte, optionnel)
- Niveau de log

---

### `app/exports/`

**`csv_exporter.py`**

Export CSV simple, une ligne par produit, avec en-têtes.
Encodage UTF-8 BOM pour compatibilité Excel.

**`excel_exporter.py`**

Export Excel structuré via `openpyxl`.

Feuilles générées :
1. **Synthèse** : KPIs globaux, résumé par marque
2. **Produits** : catalogue complet avec tous les champs
3. **Nouveautés** : produits détectés lors du dernier crawl
4. **Changements de prix** : historique des variations
5. **Promotions** : produits en promo avec durée et amplitude
6. **Suppressions** : produits disparus du catalogue

Mise en forme : en-têtes colorés par marque, colonnes de prix formatées en monnaie.

**`json_exporter.py`**

Export JSON structuré, compatible avec des outils d'analyse externes.
Deux formats : `flat` (un objet par produit) et `nested` (avec snapshots et variants imbriqués).

**`pdf_exporter.py`**

Génère un rapport PDF via `weasyprint`.
Utilise un template HTML/CSS (`ui/assets/report_template.html`).

---

## 5. Modèle de données

### Schéma des entités

```
Brand
  id, slug, name, base_url, connector_id, active
  └── CrawlSession (1:N)
        id, brand_id, started_at, ended_at, status, products_found, ...
  └── Product (1:N)
        id, brand_id, external_id, url, name
        family, subfamily, compression_level, target_zones
        is_active, first_seen, last_seen
        └── ProductSnapshot (1:N) — une par session
              id, product_id, session_id
              price, original_price, on_sale, discount_pct
              currency, availability, crawled_at
        └── Variant (1:N)
              id, product_id, color, size, available, sku
        └── ChangeEvent (1:N)
              id, product_id, session_id
              event_type, field_name, old_value, new_value, detected_at
```

### Index essentiels

```sql
-- Séries temporelles de prix
CREATE INDEX ix_snapshot_product_date ON product_snapshots(product_id, crawled_at);

-- Recherche par famille de produit
CREATE INDEX ix_product_brand_family ON products(brand_id, family);

-- Alertes de changements récents
CREATE INDEX ix_change_type_date ON change_events(event_type, detected_at);

-- Produits actifs par marque
CREATE INDEX ix_product_brand_active ON products(brand_id, is_active);
```

### Requêtes analytiques clés

```sql
-- Évolution des prix d'un produit sur 90 jours
SELECT crawled_at, price, on_sale, discount_pct
FROM product_snapshots
WHERE product_id = :id AND crawled_at >= NOW() - INTERVAL '90 days'
ORDER BY crawled_at;

-- Produits actuellement en promotion
SELECT p.name, p.brand_id, s.price, s.original_price, s.discount_pct
FROM products p
JOIN product_snapshots s ON s.product_id = p.id
WHERE s.on_sale = TRUE
  AND s.crawled_at = (SELECT MAX(crawled_at) FROM product_snapshots WHERE product_id = p.id);

-- Nouveaux produits détectés lors du dernier crawl
SELECT * FROM change_events
WHERE event_type = 'product.new'
  AND session_id = :last_session_id;

-- Fréquence de promotion par produit
SELECT product_id,
       COUNT(*) FILTER (WHERE on_sale) * 100.0 / COUNT(*) AS promo_frequency_pct
FROM product_snapshots
GROUP BY product_id
ORDER BY promo_frequency_pct DESC;
```

---

## 6. Modèle des connecteurs

### Ajouter un nouveau connecteur — checklist complète

Pour ajouter la marque `NouvelleMarque` :

1. Créer le dossier `app/connectors/nouvellemarque/`
2. Créer `__init__.py` (vide)
3. Créer `config.yml` (copier depuis un connecteur existant et adapter)
4. Créer `mappings.py` (définir `FIELD_MAPPINGS` et `CATEGORY_MAPPINGS`)
5. Créer `connector.py` (implémenter les 4 méthodes abstraites)
6. Lancer `python -m pytest tests/unit/test_connector_base.py -k nouvellemarque`
7. Le `ConnectorRegistry` détecte automatiquement le nouveau connecteur au démarrage

**Aucune modification du code existant n'est nécessaire.**

### Choisir le moteur (`engine` dans `config.yml`)

| Moteur | Quand l'utiliser | Avantages |
|--------|-----------------|-----------|
| `shopify_json` | Site Shopify (très fréquent) | API JSON stable, peu bloquée |
| `html` | Site custom sans API | Universel, mais fragile aux changements |
| `graphql` | Sites modernes (Headless) | Structuré mais nécessite introspection |

**Comment détecter si un site est Shopify :**
Tenter `https://www.site.com/products.json?limit=1`.
Si la réponse est du JSON avec une clé `products`, c'est Shopify.

### Tester un connecteur en isolation

```bash
python -c "
from app.connectors.registry import ConnectorRegistry
registry = ConnectorRegistry()
connector = registry.get('spanx')

# Test de connexion
status = connector.test_connection()
print('Connexion:', status)

# Test des catégories
cats = connector.get_categories()
print('Catégories:', [c.name for c in cats])

# Test d'un produit
urls = connector.get_product_urls(cats[0])
print('Premier URL:', urls[0])
"
```

---

## 7. Taxonomie et classification

### Structure de `taxonomies/shapewear.yml`

```yaml
families:
  bodysuit:
    label: "Bodysuit"
    keywords: ["bodysuit", "body suit", "bodyshaper"]
    subfamilies:
      open_bust:
        label: "Open Bust Bodysuit"
        keywords: ["open bust", "open-bust"]
      mid_thigh:
        label: "Mid-Thigh Bodysuit"
        keywords: ["mid-thigh", "mid thigh", "thigh"]
      shorts_built_in:
        label: "Bodysuit with Shorts"
        keywords: ["shorts bodysuit"]

  shaper_short:
    label: "Shaper Short"
    keywords: ["short", "shorts", "bermuda"]
    subfamilies:
      bike_short:
        label: "Bike Short"
        keywords: ["bike short", "cycling"]
      mid_thigh_short:
        label: "Mid-Thigh Short"
        keywords: ["mid thigh short"]

  shaper_legging:
    label: "Shaper Legging"
    keywords: ["legging", "tight", "pant"]

  bra:
    label: "Bra"
    keywords: ["bra", "bralette", "bustier"]
```

### Structure de `taxonomies/compression_levels.yml`

```yaml
levels:
  light:
    label: "Légère"
    keywords: ["light", "light compression", "everyday", "comfortable"]
  medium:
    label: "Moyenne"
    keywords: ["medium", "moderate", "firm yet comfortable"]
  firm:
    label: "Forte"
    keywords: ["firm", "strong", "tummy control", "maximum control"]
  extra_firm:
    label: "Extra-forte"
    keywords: ["extra firm", "maximum", "extreme", "surgical"]
```

### Structure de `taxonomies/body_zones.yml`

```yaml
zones:
  waist:
    label: "Taille"
    keywords: ["waist", "waistline", "cinches waist"]
  stomach:
    label: "Ventre"
    keywords: ["tummy", "stomach", "belly", "abdomen", "core"]
  hips:
    label: "Hanches"
    keywords: ["hips", "hip", "hipline"]
  thighs:
    label: "Cuisses"
    keywords: ["thigh", "thighs", "inner thigh"]
  back:
    label: "Dos"
    keywords: ["back", "back fat", "bra bulge"]
  chest:
    label: "Poitrine"
    keywords: ["bust", "chest", "breasts"]
  buttocks:
    label: "Fesses"
    keywords: ["butt", "buttocks", "rear", "booty lift"]
```

### Exemple de classification complète

Entrée (SPANX) :
```
name: "Mid-Thigh Bodysuit"
category_raw: "bodysuits"
description: "Firm tummy control and thigh slimming. Targets waist, stomach and thighs."
```

Sortie après classification :
```
family: "Bodysuit"
subfamily: "Mid-Thigh Bodysuit"
compression_level: "Forte"
target_zones: ["Taille", "Ventre", "Cuisses"]
```

Entrée (SKIMS) :
```
name: "Open Bust Bodysuit"
category_raw: "bodywear"
description: "Light, everyday shaping. Smooths core."
```

Sortie :
```
family: "Bodysuit"
subfamily: "Open Bust Bodysuit"
compression_level: "Légère"
target_zones: ["Ventre"]
```

---

## 8. Moteur de scraping

### Gestion des erreurs

Trois niveaux de retry :

**Niveau 1 — Retry immédiat** (réseau ou timeout)
```
Tentative 1 → échec → attente 2s → tentative 2 → échec → attente 4s → tentative 3
```

**Niveau 2 — Retry différé** (blocage probable, code 429 ou 403)
```
Détection blocage → pause 60–300s → reprise depuis la dernière URL réussie
```

**Niveau 3 — Abandon** (après 3 tentatives infructueuses)
```
URL marquée comme failed → log d'avertissement → passage à l'URL suivante
La session continue avec les autres marques.
```

### Délais recommandés par type de site

| Type de site | delay_min | delay_max | Justification |
|-------------|-----------|-----------|---------------|
| Shopify standard | 1.5s | 4.0s | API JSON tolérante |
| Site custom | 2.0s | 6.0s | Plus prudent |
| Site avec CDN | 1.0s | 3.0s | CDN absorbe mieux |
| Site agressif | 3.0s | 8.0s | Forte protection |

### Détection Shopify

La majorité des sites cibles fonctionnent sur Shopify.
L'endpoint `/products.json?limit=250&page=N` est disponible sur tous les sites Shopify
et retourne les données produits en JSON structuré — bien plus fiable que le HTML.

Champs disponibles via l'API Shopify :
`id, title, handle, body_html, vendor, product_type, tags, variants, images, options`

---

## 9. Moteur de workflow

### Flux d'une session complète

```
Utilisateur → configure session (marques, catégories)
           → clique "Lancer l'analyse"

WorkflowRunner → crée CrawlSession en base
              → génère les CrawlTasks (N connecteurs × M catégories)
              → démarre le pool de threads

Pour chaque CrawlTask (en parallèle, N threads) :
  ScrapingEngine → get_categories()
               → pour chaque catégorie : get_product_urls() [pagination auto]
               → pour chaque URL : parse_product() → RawProduct

  Pipeline (séquentiel) :
  Normalizer → RawProduct → NormalizedProduct
  Classifier → assigne family, subfamily, compression, zones
  ChangeDetector → compare avec version précédente → ChangeEvents
  Enricher → calcule champs dérivés
  Repository → sauvegarde Product, ProductSnapshot, ChangeEvents

  EventBus → émet crawl.task.progress vers l'UI

WorkflowRunner → marque la session completed
Reporter → génère le rapport de synthèse
UI → affiche les résultats, propose l'export
```

### Gestion de la concurrence

- Collecte HTTP : ThreadPoolExecutor, max_workers = 2 par défaut (configurable 1–6)
- Traitement et stockage : séquentiel (file FIFO alimentée par les threads)
- Base de données : une seule session SQLAlchemy partagée, accès sérialisé
- Bus d'événements : thread-safe via `queue.Queue`

---

## 10. Interface utilisateur

### Technologies

- **Framework** : PySide6 (Qt6 pour Python)
- **Style** : QSS (Qt Style Sheets) pour un look moderne
- **Graphiques** : `pyqtgraph` ou `matplotlib` avec backend Qt
- **Tableaux** : `QTableView` avec modèle personnalisé (pagination côté Python)

### Vues principales

| Vue | Accès | Contenu principal |
|-----|-------|-------------------|
| Dashboard | Démarrage | KPIs, résumé dernière session, bouton lancer |
| Marques | Navigation | Liste connecteurs, statut, tests, édition |
| Résultats | Navigation | Tableau produits filtrable, détail produit |
| Historique | Navigation | Liste sessions, rapports archivés |
| Paramètres | Navigation | Configuration globale |

### Fenêtre de progression

Lors d'une session active, une fenêtre de progression s'affiche :
- Barre de progression globale (% des tâches complétées)
- Barre de progression par connecteur actif
- Compteurs en temps réel (produits traités, nouveautés détectées, erreurs)
- Zone de logs défilante (messages INFO et WARNING)
- Bouton "Arrêter" (arrêt propre après la tâche en cours)

---

## 11. Exports

### Formats disponibles

| Format | Fichier | Contenu |
|--------|---------|---------|
| CSV | `export_YYYYMMDD.csv` | Tous les champs, une ligne par produit |
| Excel | `export_YYYYMMDD.xlsx` | 6 feuilles structurées |
| JSON | `export_YYYYMMDD.json` | Format flat ou nested |
| PDF | `report_YYYYMMDD.pdf` | Rapport de synthèse mis en forme |

### Structure du rapport PDF

1. Page de garde (date, marques analysées, période)
2. Résumé exécutif (4 KPIs, infographies)
3. Nouveaux produits (tableau avec miniatures)
4. Changements de prix (tableau trié par amplitude)
5. Promotions actives (tableau avec durée)
6. Produits supprimés (liste)
7. Annexe : métriques de crawl

---

## 12. Plan de développement

### Phase 1 — MVP (semaines 1–6)

**Objectif** : Prouver que le système fonctionne de bout en bout
avec une marque et une interface minimale.

Livrables :
- [x] Structure du projet et `pyproject.toml`
- [ ] `BaseConnector` + ABC + dataclass `RawProduct`
- [ ] `ConnectorRegistry` avec auto-découverte
- [ ] Connecteur SPANX (mode `shopify_json`)
- [ ] `HttpClient` avec retry et logging
- [ ] `PaginationHandler` mode `offset`
- [ ] `ScrapingEngine` single-threaded
- [ ] Modèle de données SQLAlchemy + migrations Alembic
- [ ] `Normalizer` basique (prix, champs obligatoires)
- [ ] `Classifier` avec taxonomie YAML shapewear minimale
- [ ] `ChangeDetector` (nouveau produit, changement de prix)
- [ ] `WorkflowRunner` single-threaded
- [ ] Interface PySide6 minimale : bouton lancer + logs + export CSV
- [ ] Tests unitaires : normalizer, classifier, change_detector

**Critère de succès MVP** :
L'utilisateur clique "Lancer", l'application crawle SPANX,
stocke les produits, et exporte un CSV exploitable.

---

### Phase 2 — Version 1.0 (semaines 7–14)

**Objectif** : Plateforme de veille complète, multi-marques.

Livrables :
- [ ] Connecteurs SKIMS, Honeylove, Shapermint
- [ ] `AntiBlockManager` (rotation UA, délais, backoff)
- [ ] `ScrapingEngine` multi-threaded (pool de 2–4 workers)
- [ ] `Enricher` (discount_pct, promo_frequency)
- [ ] Taxonomie shapewear complète (toutes familles, zones, niveaux de compression)
- [ ] Interface Dashboard avec KPIs réels
- [ ] Vue Résultats avec filtres et détail produit
- [ ] Vue Brands avec test de connexion
- [ ] Vue Historique avec rapport de session
- [ ] Export Excel (6 feuilles)
- [ ] Export PDF (rapport mis en forme)
- [ ] Fenêtre de progression temps réel
- [ ] Tests d'intégration : connecteurs, workflow complet
- [ ] Documentation utilisateur (guide de démarrage)

**Critère de succès V1** :
L'utilisateur analyse 4 marques en un clic,
consulte les nouveautés et changements de prix,
et exporte un rapport Excel/PDF exploitable.

---

### Phase 3 — Version 2.0 (semaines 15–22)

**Objectif** : Intelligence et automatisation avancées.

Livrables :
- [ ] Fallback LLM pour la classification (via API Anthropic)
- [ ] Wizard de création de connecteur dans l'UI
- [ ] Système d'alertes configurables (email, notification desktop)
- [ ] Planification automatique des sessions (`scheduler.py`)
- [ ] Support des proxies rotatifs
- [ ] Vue d'audit de classification (produits mal classifiés)
- [ ] Graphiques d'évolution des prix dans le détail produit
- [ ] Archivage automatique des données anciennes
- [ ] Extension vers d'autres secteurs (lingerie, activewear, swimwear)
- [ ] API REST locale optionnelle (FastAPI, pour intégrations externes)
- [ ] Mode cloud optionnel (PostgreSQL + interface web)
- [ ] Guide de contribution pour nouveaux connecteurs

---

## 13. Stack technique

### Bibliothèques principales

| Domaine | Bibliothèque | Version | Justification |
|---------|-------------|---------|---------------|
| HTTP | `httpx` | ≥0.27 | Async natif, HTTP/2, meilleur que requests |
| HTML | `beautifulsoup4` | ≥4.12 | Standard, excellent support CSS |
| HTML parser | `lxml` | ≥5.0 | 3× plus rapide que html.parser |
| JSON path | `jmespath` | ≥1.0 | Requêtes expressives sur JSON |
| ORM | `sqlalchemy` | ≥2.0 | Standard industriel, sessions async |
| Migrations | `alembic` | ≥1.13 | Migrations robustes avec SQLAlchemy |
| Config | `pydantic-settings` | ≥2.0 | Validation des configs, .env |
| UI | `PySide6` | ≥6.7 | Qt6 officiel, licence LGPL |
| Graphiques | `pyqtgraph` | ≥0.13 | Graphiques rapides dans Qt |
| Excel | `openpyxl` | ≥3.1 | Excel natif sans dépendances Java |
| PDF | `weasyprint` | ≥62 | HTML/CSS → PDF, pas de headless browser |
| Templates | `jinja2` | ≥3.1 | Templates HTML pour rapports |
| Config YAML | `pyyaml` | ≥6.0 | Lecture des taxonomies |
| Tests | `pytest` | ≥8.0 | Standard Python |
| Mock HTTP | `respx` | ≥0.21 | Mock httpx sans serveur |
| Logging | `structlog` | ≥24.0 | Logs structurés (JSON) |

### `pyproject.toml` — structure

```toml
[project]
name = "market-intel"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "httpx>=0.27",
    "beautifulsoup4>=4.12",
    "lxml>=5.0",
    "jmespath>=1.0",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "pydantic-settings>=2.0",
    "PySide6>=6.7",
    "pyqtgraph>=0.13",
    "openpyxl>=3.1",
    "weasyprint>=62",
    "jinja2>=3.1",
    "pyyaml>=6.0",
    "structlog>=24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "respx>=0.21",
    "ruff>=0.4",
    "mypy>=1.10",
]

[project.scripts]
market-intel = "main:main"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.pytest.ini_options]
testpaths = ["tests"]
```

---

## 14. Installation et lancement

### Prérequis

- Python 3.11 ou supérieur
- pip ou uv (recommandé)

### Installation

```bash
# Cloner le projet
git clone <repo>
cd Shapewear me

# Créer un environnement virtuel
python -m venv .venv
source .venv/bin/activate      # Linux/macOS
.venv\Scripts\activate         # Windows

# Installer les dépendances
pip install -e ".[dev]"

# Initialiser la base de données
alembic upgrade head

# Lancer l'application
python main.py
```

### Lancement en mode développement

```bash
# Lancer les tests
pytest

# Lancer les tests avec couverture
pytest --cov=app --cov-report=html

# Tester un connecteur en isolation
python -m app.connectors.spanx.connector --test

# Lint et formatage
ruff check app/
ruff format app/
```

### Variables d'environnement (`.env`)

```bash
# Copier le modèle
cp .env.example .env

# Variables disponibles
DATABASE_URL=sqlite:///data/Shapewear me.db
LOG_LEVEL=INFO
LOG_DIR=data/logs
EXPORT_DIR=data/exports
PROXY_URL=                        # Optionnel, V2
ANTHROPIC_API_KEY=               # Optionnel, V2 (classification LLM)
MAX_WORKERS=2                    # Threads de scraping
```

---

## 15. Créer un nouveau connecteur

Guide pas à pas pour ajouter la marque `Maidenform` (exemple).

### Étape 1 — Vérifier si le site est Shopify

```bash
curl https://www.maidenform.com/products.json?limit=1
```

Si la réponse contient `{"products": [...]}`, c'est Shopify → utiliser `engine: shopify_json`.

### Étape 2 — Créer la structure

```bash
mkdir app/connectors/maidenform
touch app/connectors/maidenform/__init__.py
touch app/connectors/maidenform/connector.py
touch app/connectors/maidenform/config.yml
touch app/connectors/maidenform/mappings.py
```

### Étape 3 — Remplir `config.yml`

```yaml
name: Maidenform
slug: maidenform
base_url: https://www.maidenform.com
version: "1.0"
engine: shopify_json
rate_limit_rps: 0.5
delay_min: 2.0
delay_max: 5.0
pagination:
  type: cursor
  page_size: 250
categories_url: /collections
product_list_endpoint: /products.json
```

### Étape 4 — Remplir `mappings.py`

Inspecter une page produit et un objet JSON de l'API Shopify pour définir les mappings.

### Étape 5 — Implémenter `connector.py`

```python
from app.connectors.base import BaseConnector

class MaidenformConnector(BaseConnector):
    def get_categories(self):
        # Récupérer /collections.json et filtrer les catégories pertinentes
        ...

    def get_product_urls(self, category):
        # Utiliser self.http_client pour paginer /products.json
        ...

    def parse_product(self, url, html):
        # En mode shopify_json, html est le JSON produit déjà parsé
        ...

    def get_metadata(self):
        return ConnectorMeta(name="Maidenform", slug="maidenform", version="1.0")
```

### Étape 6 — Tester

```bash
python -c "from app.connectors.registry import ConnectorRegistry; r = ConnectorRegistry(); print(r.list_connectors())"
# → ['spanx', 'skims', 'honeylove', 'shapermint', 'maidenform']

pytest tests/unit/test_connector_base.py -v
```

---

## 16. Risques et mitigations

| Sévérité | Risque | Mitigation |
|----------|--------|------------|
| **Élevé** | Blocage anti-bot (Cloudflare, Akamai) | Rotation UA · délais aléatoires · proxies rotatifs (V2) · crawl aux heures creuses |
| **Élevé** | Changement de structure HTML sans préavis | Préférer les APIs JSON Shopify · alertes si taux d'échec > 20% · tests automatisés quotidiens |
| **Moyen** | Divergence de nomenclature entre marques | Taxonomie YAML extensible · fallback LLM (V2) · interface d'audit dans l'UI |
| **Moyen** | Croissance de la base (données temporelles) | Archivage des snapshots anciens de > 180 jours · agrégats pré-calculés · purge configurable |
| **Faible** | Responsabilité légale (ToS des sites) | Respect de `robots.txt` · délais respectueux · pas de surcharge · usage strictement interne |
| **Faible** | Performance de l'UI sur grand catalogue | Pagination côté Python dans les tableaux · chargement lazy des données |

---

## 17. Évolutions prévues

### Court terme (après V1)

- **Classification LLM** : intégration de l'API Anthropic comme fallback quand
  la taxonomie YAML ne couvre pas un produit. Prompt structuré avec la nomenclature
  cible et le catalogue de keywords.
- **Alertes** : système de règles configurables (exemple : "m'alerter si le prix
  de SKIMS Open Bust Bodysuit baisse de plus de 20%"). Notification desktop via
  `plyer`, email via `smtplib`.

### Moyen terme (V2)

- **Planification automatique** : sessions programmées (quotidienne, hebdomadaire)
  via `APScheduler` sans intervention manuelle.
- **Wizard de connecteur** : interface guidée dans l'UI pour créer un connecteur
  sans éditer de fichiers manuellement. L'utilisateur saisit l'URL du site,
  l'outil détecte le moteur (Shopify/HTML), propose les sélecteurs CSS automatiquement
  via une analyse de la page de listing.
- **Extension sectorielle** : le système de taxonomies YAML permet d'étendre
  à d'autres secteurs (lingerie, activewear, swimwear) en ajoutant un fichier
  de taxonomie et les connecteurs correspondants.

### Long terme (V3)

- **Mode cloud** : migration vers une architecture client-serveur avec
  FastAPI en backend et interface web React. La couche `processing/` et `storage/`
  restent identiques. Seule l'UI change.
- **API REST** : exposition des données via une API REST locale pour intégration
  avec d'autres outils (Power BI, Google Sheets, outils internes).
- **Analyse prédictive** : prédiction des cycles de promotions, détection
  de patterns saisonniers, recommandations de positionnement prix.

---

## Contacts et contribution

Pour ajouter un connecteur ou modifier la taxonomie, consulter :
- `docs/connector_spec.md` — spécification technique complète des connecteurs
- `docs/taxonomy_guide.md` — guide d'édition des fichiers YAML de classification

---

*Document généré le 26 juin 2026 — Version 1.0*
"""
Classifier — Assigne la classification taxonomique à un NormalizedProduct.

Processus :
    1. Lecture de taxonomies/shapewear.yml
    2. Correspondance category_raw → family + subfamily
    3. Détection du compression_level (mots-clés dans nom/description)
    4. Détection des target_zones (zones corporelles mentionnées)
    5. En cas d'absence : classification_manual_review = True

Usage :
    classifier = Classifier()
    normalized = classifier.classify(normalized_product)
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.core.config import settings
from app.core.exceptions import ClassificationError
from app.core.logger import get_logger
from app.processing.normalizer import NormalizedProduct

log = get_logger(__name__)


class Classifier:
    """Classifie un NormalizedProduct selon la taxonomie shapewear YAML."""

    def __init__(self) -> None:
        self._taxonomy = self._load_taxonomy()
        self._compression_levels = self._load_compression_levels()
        self._body_zones = self._load_body_zones()

    # -------------------------------------------------------------------
    # Méthode principale
    # -------------------------------------------------------------------

    def classify(self, product: NormalizedProduct) -> NormalizedProduct:
        """
        Enrichit le produit avec family, subfamily, compression_level, target_zones.
        Modifie le produit en place et le retourne.
        """
        try:
            # 1. Classification famille / sous-famille
            family, subfamily = self._classify_family(product)
            product.family = family
            product.subfamily = subfamily

            # 2. Niveau de compression
            product.compression_level = self._detect_compression(product)

            # 3. Zones corporelles ciblées
            product.target_zones = self._detect_zones(product)

            # 4. Flag de révision manuelle si non classifié
            if not product.family:
                product.classification_manual_review = True
                log.debug(
                    "Produit non classifié — révision manuelle",
                    name=product.name,
                    category_raw=product.category_raw,
                )
            else:
                product.classification_manual_review = False

            log.debug(
                "Classification terminée",
                name=product.name,
                family=product.family,
                subfamily=product.subfamily,
                compression=product.compression_level,
                zones=product.target_zones,
            )
            return product

        except ClassificationError:
            raise
        except Exception as exc:
            raise ClassificationError(
                f"Erreur de classification : {exc}",
                context={"name": product.name, "brand": product.brand_slug},
            ) from exc

    # -------------------------------------------------------------------
    # Classification famille / sous-famille
    # -------------------------------------------------------------------

    def _classify_family(
        self, product: NormalizedProduct
    ) -> tuple[str | None, str | None]:
        """
        Cherche la famille et la sous-famille dans la taxonomie.
        Stratégie : correspondance sur category_raw, puis sur le nom.
        """
        # Texte à analyser
        search_texts = []
        if product.category_raw:
            search_texts.append(product.category_raw.lower())
        if product.name:
            search_texts.append(product.name.lower())
        if product.description:
            search_texts.append(product.description.lower()[:200])

        for family_key, family_data in self._taxonomy.get("families", {}).items():
            family_keywords: list[str] = [
                kw.lower() for kw in family_data.get("keywords", [])
            ]

            if self._matches_keywords(search_texts, family_keywords):
                family_label = family_data.get("label", family_key)

                # Cherche la sous-famille
                subfamily_label = None
                for sf_key, sf_data in family_data.get("subfamilies", {}).items():
                    sf_keywords = [
                        kw.lower() for kw in sf_data.get("keywords", [])
                    ]
                    if self._matches_keywords(search_texts, sf_keywords):
                        subfamily_label = sf_data.get("label", sf_key)
                        break

                return family_label, subfamily_label

        return None, None

    # -------------------------------------------------------------------
    # Détection du niveau de compression
    # -------------------------------------------------------------------

    def _detect_compression(self, product: NormalizedProduct) -> str | None:
        """Détecte le niveau de compression depuis le nom et la description."""
        search_texts = self._get_search_texts(product)
        levels = self._compression_levels.get("levels", {})

        # Ordre de priorité : du plus fort au plus léger
        priority_order = ["extra_firm", "firm", "medium", "light"]
        for level_key in priority_order:
            level_data = levels.get(level_key, {})
            keywords = [kw.lower() for kw in level_data.get("keywords", [])]
            if self._matches_keywords(search_texts, keywords):
                return level_data.get("label", level_key)

        return None

    # -------------------------------------------------------------------
    # Détection des zones corporelles
    # -------------------------------------------------------------------

    def _detect_zones(self, product: NormalizedProduct) -> list[str]:
        """Détecte les zones corporelles ciblées depuis le nom et la description."""
        search_texts = self._get_search_texts(product)
        zones_data = self._body_zones.get("zones", {})

        detected = []
        for zone_key, zone_info in zones_data.items():
            keywords = [kw.lower() for kw in zone_info.get("keywords", [])]
            if self._matches_keywords(search_texts, keywords):
                label = zone_info.get("label", zone_key)
                detected.append(label)

        return detected

    # -------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------

    def _get_search_texts(self, product: NormalizedProduct) -> list[str]:
        texts = []
        if product.name:
            texts.append(product.name.lower())
        if product.description:
            texts.append(product.description.lower()[:500])
        if product.category_raw:
            texts.append(product.category_raw.lower())
        return texts

    @staticmethod
    def _matches_keywords(texts: list[str], keywords: list[str]) -> bool:
        """Retourne True si au moins un keyword est présent dans au moins un texte."""
        for text in texts:
            for kw in keywords:
                if kw in text:
                    return True
        return False

    # -------------------------------------------------------------------
    # Chargement des taxonomies
    # -------------------------------------------------------------------

    def _load_taxonomy(self) -> dict:
        return self._load_yaml("shapewear.yml")

    def _load_compression_levels(self) -> dict:
        return self._load_yaml("compression_levels.yml")

    def _load_body_zones(self) -> dict:
        return self._load_yaml("body_zones.yml")

    def _load_yaml(self, filename: str) -> dict:
        path = settings.TAXONOMIES_DIR / filename
        if not path.exists():
            log.warning("Fichier taxonomie introuvable", file=filename)
            return {}
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
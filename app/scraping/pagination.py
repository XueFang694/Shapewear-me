"""
PaginationHandler — Gestion des différentes stratégies de pagination.

Stratégies supportées :
    offset       → ?page=1, ?page=2, … (Shopify standard)
    cursor       → ?page_info=<token> (Shopify cursor)
    page_number  → ?p=1, ?p=2 (WooCommerce, etc.)
    infinite_scroll → détection du "Load more" (V2)

Usage :
    handler = PaginationHandler(pagination_type="offset", page_size=250)
    for page_url in handler.iter_pages("https://example.com/products.json"):
        ...
"""
from __future__ import annotations

from typing import Generator
from urllib.parse import urlencode, urlparse, urlunparse, parse_qs

from app.core.exceptions import PaginationError
from app.core.logger import get_logger

log = get_logger(__name__)


class PaginationHandler:
    """
    Génère les URLs de pagination selon la stratégie configurée.
    """

    def __init__(
        self,
        pagination_type: str = "offset",
        page_param: str = "page",
        page_size: int = 250,
        max_pages: int = 100,
    ) -> None:
        self.pagination_type = pagination_type
        self.page_param = page_param
        self.page_size = page_size
        self.max_pages = max_pages

        supported = {"offset", "cursor", "page_number"}
        if pagination_type not in supported:
            raise PaginationError(
                f"Type de pagination non supporté : {pagination_type!r}",
                context={"supported": sorted(supported)},
            )

    def iter_pages(
        self, base_url: str
    ) -> Generator[str, None, None]:
        """
        Génère les URLs de toutes les pages.
        Le générateur s'arrête à max_pages.
        """
        if self.pagination_type in ("offset", "page_number"):
            yield from self._iter_offset(base_url)
        elif self.pagination_type == "cursor":
            yield from self._iter_cursor(base_url)

    # -------------------------------------------------------------------
    # Stratégie offset / page_number
    # -------------------------------------------------------------------

    def _iter_offset(self, base_url: str) -> Generator[str, None, None]:
        """
        Génère les URLs avec paramètre ?page=N.
        L'appelant doit arrêter l'itération quand la page est vide.
        """
        parsed = urlparse(base_url)
        existing_params = parse_qs(parsed.query, keep_blank_values=True)

        for page_num in range(1, self.max_pages + 1):
            params = dict(existing_params)
            params[self.page_param] = [str(page_num)]
            params["limit"] = [str(self.page_size)]

            # Reconstruire l'URL
            query = urlencode(
                {k: v[0] if len(v) == 1 else v for k, v in params.items()}
            )
            url = urlunparse(parsed._replace(query=query))
            log.debug("Page générée", type="offset", page=page_num, url=url)
            yield url

    # -------------------------------------------------------------------
    # Stratégie cursor (Shopify page_info)
    # -------------------------------------------------------------------

    def _iter_cursor(self, base_url: str) -> Generator[str, None, None]:
        """
        Génère les URLs avec curseur opaque (?page_info=<token>).
        Nécessite d'analyser le header Link de chaque réponse.
        Cette stratégie doit être pilotée par le connecteur car elle
        nécessite les réponses HTTP — ici on yield seulement la première page.
        """
        parsed = urlparse(base_url)
        existing_params = parse_qs(parsed.query, keep_blank_values=True)
        params = dict(existing_params)
        params["limit"] = [str(self.page_size)]
        query = urlencode({k: v[0] if len(v) == 1 else v for k, v in params.items()})
        first_url = urlunparse(parsed._replace(query=query))
        log.debug("Première page cursor", url=first_url)
        yield first_url
        # Note : le connecteur doit appeler next_cursor_url() pour les pages suivantes

    def next_cursor_url(self, base_url: str, link_header: str | None) -> str | None:
        """
        Extrait l'URL de la page suivante depuis le header Link de Shopify.
        Retourne None si pas de page suivante.

        Exemple de header :
          Link: <https://...?page_info=abc&limit=250>; rel="next"
        """
        if not link_header:
            return None
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                url_part = part.split(";")[0].strip()
                url = url_part.strip("<>")
                log.debug("Curseur suivant extrait", url=url)
                return url
        return None

    @staticmethod
    def detect_type(listing_url: str) -> str:
        """
        Heuristique simple pour détecter le type de pagination d'une URL.
        Retourne 'offset' par défaut.
        """
        if "page_info=" in listing_url:
            return "cursor"
        if "page=" in listing_url or "p=" in listing_url:
            return "offset"
        # Shopify = offset par défaut
        return "offset"
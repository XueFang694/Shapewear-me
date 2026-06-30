"""
Point d'entrée de l'application Market Intelligence Platform.

Lancement :
    python main.py

En mode ligne de commande (sans UI) :
    python main.py --no-ui --brand spanx
    python main.py --no-ui --brand wacoal --market us
    python main.py --no-ui --market fr --export
"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Market Intelligence Platform",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Lancer en mode ligne de commande sans interface graphique",
    )
    parser.add_argument(
        "--brand",
        nargs="+",
        default=None,
        metavar="SLUG",
        help="Marque(s) à analyser (ex : spanx skims wacoal). Par défaut : toutes.",
    )
    parser.add_argument(
        "--market",
        default=None,
        metavar="SLUG",
        help=(
            "Marché géographique actif (ex : us, fr, it, es, gb, zh). "
            "Surcharge MARKET défini dans .env/settings.json pour cette exécution. "
            "Voir app/core/market.py pour la liste complète."
        ),
    )
    parser.add_argument(
        "--export",
        action="store_true",
        help="Exporter en CSV après le crawl (mode --no-ui uniquement)",
    )
    args = parser.parse_args()

    # Surcharger le marché AVANT tout import qui lirait settings (Settings()
    # est instancié au chargement du module app.core.config). On le fait via
    # la variable d'environnement, lue nativement par pydantic-settings.
    if args.market:
        import os
        os.environ["MARKET"] = args.market.lower().strip()

    if args.no_ui:
        return _run_cli(brand_slugs=args.brand, export_csv=args.export)
    else:
        return _run_gui()


def _run_gui() -> int:
    """Lance l'interface graphique PySide6."""
    from PySide6.QtWidgets import QApplication
    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("Market Intelligence Platform")
    app.setOrganizationName("Shapewear-me")

    window = MainWindow()
    window.show()

    return app.exec()


def _run_cli(
    brand_slugs: list[str] | None = None,
    export_csv: bool = False,
) -> int:
    """Lance le crawl en mode CLI (sans interface graphique)."""
    from app.workflow.runner import WorkflowRunner
    from app.core.config import settings
    from app.core.logger import get_logger

    log = get_logger(__name__)

    print("=== Market Intelligence Platform — Mode CLI ===")
    print(f"Marché  : {settings.MARKET.upper()}")
    print(f"Marques : {brand_slugs or 'toutes'}")
    print()

    try:
        runner = WorkflowRunner()
        results = runner.run(brand_slugs=brand_slugs)

        print("\n=== Résultats ===")
        for result in results:
            status_icon = "✓" if result.status == "completed" else "✗"
            print(
                f"  {status_icon} {result.brand_slug.upper():<12} "
                f"{result.products_found:>4} produits  "
                f"({result.products_new} nouveaux, "
                f"{result.products_changed} changés)  "
                f"{result.duration_s:.1f}s"
            )
            if result.error_message:
                print(f"       Erreur : {result.error_message}")

        total = sum(r.products_found for r in results)
        print(f"\n  Total : {total} produits analysés\n")

        if export_csv:
            from app.exports.csv_exporter import CsvExporter
            exporter = CsvExporter()
            path = exporter.export_from_db(brand_slugs=brand_slugs)
            print(f"  Export CSV : {path}\n")

        return 0

    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur.")
        return 1
    except Exception as exc:
        print(f"\nErreur fatale : {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
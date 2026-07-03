from app.connectors.spanx.connector import SpanxConnector
import requests, json

c = SpanxConnector()

# Prend le premier produit de la première catégorie
url = "https://www.spanx.com/products/spanx-poplin-oversized-short-sleeve-button-down-classic-white.json"
data = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}).json().get("product", {})

result = c.parse_product(url, data)

print("=== extra ===")
import pprint
pprint.pprint(result.extra)
"""Operator-supplied product catalog for Example 1.

The grounding-check guard rejects any product_id not in this catalog.
Catalogs in real deployments would load from a database or API; for the
demo, a small in-memory dict is sufficient and self-contained.
"""

from __future__ import annotations

from typing import Final

PRODUCT_CATALOG: Final[dict[str, dict[str, object]]] = {
    "SKU-1001": {
        "name": "Wireless mouse",
        "category": "peripherals",
        "price_usd": 24.99,
        "in_stock": True,
    },
    "SKU-1002": {
        "name": "Mechanical keyboard",
        "category": "peripherals",
        "price_usd": 89.50,
        "in_stock": True,
    },
    "SKU-1003": {
        "name": "27-inch monitor",
        "category": "displays",
        "price_usd": 329.00,
        "in_stock": False,
    },
    "SKU-1004": {
        "name": "USB-C hub",
        "category": "accessories",
        "price_usd": 39.95,
        "in_stock": True,
    },
    "SKU-1005": {
        "name": "Laptop stand",
        "category": "accessories",
        "price_usd": 49.00,
        "in_stock": True,
    },
    "SKU-1006": {
        "name": "Webcam 1080p",
        "category": "peripherals",
        "price_usd": 79.99,
        "in_stock": True,
    },
    "SKU-1007": {
        "name": "Noise-cancelling headphones",
        "category": "audio",
        "price_usd": 199.00,
        "in_stock": True,
    },
    "SKU-1008": {
        "name": "Standing desk converter",
        "category": "furniture",
        "price_usd": 215.00,
        "in_stock": False,
    },
}

VALID_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"peripherals", "displays", "accessories", "audio", "furniture"}
)

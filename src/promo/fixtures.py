"""Dados sinteticos para exercitar o pipeline sem a API do Mercado Livre.

Serve pra calibrar o tom dos textos e testar a entrega enquanto a conta de
desenvolvedor nao e liberada. Tudo entra com source "demo", entao nunca se
mistura com o historico real -- e `seed --clear` limpa so o que e sintetico.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from .db import now
from .models import Offer

SOURCE = "demo"

# Precos em BRL, proximos do real pra o texto sair plausivel.
CATALOG = [
    # (id, titulo, preco normal, preco promocional, frete gratis)
    ("MLB2718281", "Fone de Ouvido Bluetooth JBL Tune 520BT Sem Fio", 249.0, 179.0, True),
    ("MLB3141592", "Smartwatch Amazfit GTS 4 Mini Tela AMOLED 1.65", 599.0, 429.0, True),
    ("MLB1618033", "SSD NVMe 1TB Kingston NV2 PCIe 4.0 Leitura 3500MB/s", 449.0, 389.0, False),
    ("MLB1414213", "Air Fryer Mondial 4L Family Inox AFN-40-BI", 389.0, 359.0, True),
    ("MLB2236067", "Monitor 27 Polegadas LG UltraGear 144Hz Full HD", 1699.0, 1199.0, True),
]


def seed(conn: sqlite3.Connection, days: int = 60) -> int:
    """Cria historico ate ontem no preco normal e uma queda hoje.

    O preco normal fica constante de proposito: o objetivo nao e simular
    mercado, e produzir uma baseline previsivel pra conferir se o desconto
    calculado bate com o que aparece no texto.
    """
    today = now().date()
    created = 0

    for external_id, title, normal, promo, free_shipping in CATALOG:
        product_id = f"{SOURCE}:{external_id}"
        url = f"https://produto.mercadolivre.com.br/{external_id}"
        ts = now().isoformat()

        conn.execute(
            """
            INSERT INTO products (id, source, external_id, title, url, image_url,
                                  category, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            ON CONFLICT(id) DO UPDATE SET last_seen_at = excluded.last_seen_at
            """,
            (product_id, SOURCE, external_id, title, url, ts, ts),
        )

        for offset in range(1, days + 1):
            conn.execute(
                """
                INSERT INTO price_history (product_id, observed_on, price, original_price, available)
                VALUES (?, ?, ?, NULL, 1)
                ON CONFLICT(product_id, observed_on) DO UPDATE SET price = excluded.price
                """,
                (product_id, (today - timedelta(days=offset)).isoformat(), normal),
            )

        conn.execute(
            """
            INSERT INTO price_history (product_id, observed_on, price, original_price, available)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(product_id, observed_on) DO UPDATE SET price = excluded.price
            """,
            (product_id, today.isoformat(), promo, normal),
        )
        created += 1

    return created


def clear(conn: sqlite3.Connection) -> int:
    """Remove tudo que veio do seed, deixando o historico real intacto."""
    ids = [f"{SOURCE}:{entry[0]}" for entry in CATALOG]
    marks = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM posts WHERE product_id IN ({marks})", ids)
    conn.execute(f"DELETE FROM price_history WHERE product_id IN ({marks})", ids)
    cursor = conn.execute(f"DELETE FROM products WHERE id IN ({marks})", ids)
    return cursor.rowcount


def current_offers() -> list[Offer]:
    """As ofertas de hoje, no formato que o scoring espera."""
    return [
        Offer(
            source=SOURCE,
            external_id=external_id,
            title=title,
            price=promo,
            original_price=normal,
            url=f"https://produto.mercadolivre.com.br/{external_id}",
            free_shipping=free_shipping,
        )
        for external_id, title, normal, promo, free_shipping in CATALOG
    ]

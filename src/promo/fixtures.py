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

# Produtos reais do catalogo do ML, com foto real da CDN. Nao e capricho: o
# `demo` existe pra avaliar layout e tom de texto, e os dois mentem quando a
# foto e generica ou repetida. Os IDs sao de catalogo (rota /p/), as fotos
# saem do og:image da propria pagina do produto.
#
# Os precos abaixo sao INVENTADOS -- servem so pra produzir uma baseline
# previsivel e conferir se o desconto calculado bate com o texto gerado.
#
# Sufixo -O na CDN: ~45 KB. O -F tem a mesma imagem com ate 500 KB, peso que
# nao se justifica numa grade com varios cards.
CATALOG = [
    # (id, titulo, preco normal, preco promocional, frete gratis, imagem)
    (
        "MLB46196451",
        "Monitor Gamer Samsung Odyssey G5 32\" QHD 165Hz 1ms HDMI DisplayPort",
        1899.0,
        1349.0,
        True,
        "https://http2.mlstatic.com/D_NQ_NP_987462-MLA99509630578_112025-O.jpg",
    ),
    (
        "MLB46470846",
        "Notebook Vaio FE16 AMD Ryzen 7-5825U 16GB RAM 512GB SSD Wi-Fi 6",
        3299.0,
        2599.0,
        True,
        "https://http2.mlstatic.com/D_NQ_NP_954850-MLA99514927694_112025-O.jpg",
    ),
    (
        "MLB52052995",
        "Air Fryer Philco 6,5L Visor Glass Redstone 1700W PAF65A",
        469.0,
        359.0,
        True,
        "https://http2.mlstatic.com/D_NQ_NP_761173-MLA99475995202_112025-O.jpg",
    ),
    (
        "MLB46211942",
        "Smartwatch Huawei Band 10 Analise de Sono e Oximetro",
        349.0,
        249.0,
        True,
        "https://http2.mlstatic.com/D_NQ_NP_806194-MLA105593417435_012026-O.jpg",
    ),
    (
        "MLB59090080",
        "Smart TV 43\" AOC LED Roku Full HD Wi-Fi 60Hz HDMI USB",
        1699.0,
        1399.0,
        False,
        "https://http2.mlstatic.com/D_NQ_NP_935991-MLA99489146286_112025-O.jpg",
    ),
]


def seed(conn: sqlite3.Connection, days: int = 60) -> int:
    """Cria historico ate ontem no preco normal e uma queda hoje.

    O preco normal fica constante de proposito: o objetivo nao e simular
    mercado, e produzir uma baseline previsivel pra conferir se o desconto
    calculado bate com o que aparece no texto.
    """
    today = now().date()
    created = 0

    for external_id, title, normal, promo, free_shipping, image_url in CATALOG:
        product_id = f"{SOURCE}:{external_id}"
        # Rota /p/: sao IDs de produto de catalogo, nao de anuncio.
        url = f"https://www.mercadolivre.com.br/p/{external_id}"
        ts = now().isoformat()

        conn.execute(
            """
            INSERT INTO products (id, source, external_id, title, url, image_url,
                                  category, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title        = excluded.title,
                url          = excluded.url,
                image_url    = excluded.image_url,
                last_seen_at = excluded.last_seen_at
            """,
            (product_id, SOURCE, external_id, title, url, image_url, ts, ts),
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
    # Precisa vir antes do DELETE de products: a FK impede orfao.
    conn.execute(f"DELETE FROM affiliate_links WHERE product_id IN ({marks})", ids)
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
            url=f"https://www.mercadolivre.com.br/p/{external_id}",
            image_url=image_url,
            free_shipping=free_shipping,
        )
        for external_id, title, normal, promo, free_shipping, image_url in CATALOG
    ]

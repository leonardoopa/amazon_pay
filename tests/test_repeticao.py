"""Um produto nao volta ao grupo logo depois de ter ido.

A vitrine do ML devolve o mesmo item enquanto ele seguir em promocao, e o
filtro aprova de novo -- o desconto continua real, e do ponto de vista da
medicao nada esta errado. Do lado de quem le o grupo, porem, a segunda
aparicao em quinze minutos nao e oferta nova: e a mesma, repetida.

O `recent_headlines` ja evitava repetir a FRASE. Isto evita repetir o PRODUTO,
que e o que o leitor percebe mesmo com a frase trocada.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.db import (  # noqa: E402
    SCHEMA,
    create_post,
    mark_post_sent,
    now,
    products_in_cooldown,
    record_offer,
)
from promo.models import Offer  # noqa: E402

PRODUTO = "mercadolivre:MLB1"


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    record_offer(
        conn,
        Offer(
            source="mercadolivre",
            external_id="MLB1",
            title="Produto",
            price=100.0,
            url="https://mercadolivre.com.br/MLB1",
        ),
    )
    return conn


def enfileirar(conn: sqlite3.Connection) -> int:
    return create_post(conn, PRODUTO, 100.0, 150.0, 33.0, "texto")


def envelhecer(conn: sqlite3.Connection, post_id: int, minutos: int) -> None:
    """Recua o `sent_at` para simular um envio de N minutos atras."""
    quando = (now() - timedelta(minutes=minutos)).isoformat()
    conn.execute("UPDATE posts SET sent_at = ? WHERE id = ?", (quando, post_id))


# ---------- o que bloqueia ----------


def test_post_na_fila_bloqueia_o_mesmo_produto():
    """Ainda nao foi para o grupo, mas vai. Enfileirar outro garante a
    repeticao em vez de arriscar ela."""
    conn = make_conn()
    enfileirar(conn)

    assert PRODUTO in products_in_cooldown(conn, 60)


def test_enviado_dentro_da_janela_bloqueia():
    conn = make_conn()
    post_id = enfileirar(conn)
    mark_post_sent(conn, post_id)
    envelhecer(conn, post_id, 20)

    assert PRODUTO in products_in_cooldown(conn, 60)


# ---------- o que libera ----------


def test_enviado_antes_da_janela_libera():
    conn = make_conn()
    post_id = enfileirar(conn)
    mark_post_sent(conn, post_id)
    envelhecer(conn, post_id, 90)

    assert products_in_cooldown(conn, 60) == set()


def test_banco_sem_post_nao_bloqueia_nada():
    assert products_in_cooldown(make_conn(), 60) == set()


def test_expirado_nao_bloqueia():
    """`expired` nunca chegou ao grupo -- nao ha repeticao a evitar, e
    bloquear por causa dele tiraria do ar justamente a oferta que falhou
    em ser entregue."""
    conn = make_conn()
    post_id = enfileirar(conn)
    conn.execute("UPDATE posts SET status = 'expired' WHERE id = ?", (post_id,))

    assert products_in_cooldown(conn, 60) == set()


def test_falho_nao_bloqueia():
    conn = make_conn()
    post_id = enfileirar(conn)
    conn.execute("UPDATE posts SET status = 'failed' WHERE id = ?", (post_id,))

    assert products_in_cooldown(conn, 60) == set()


# ---------- desligar ----------


def test_janela_zero_ainda_bloqueia_o_que_esta_na_fila():
    """Desligar a espera nao pode liberar post duplicado na propria fila:
    duas rodadas seguidas enfileirariam o mesmo produto e o grupo receberia
    os dois em sequencia, com minutos de diferenca."""
    conn = make_conn()
    enfileirar(conn)

    assert PRODUTO in products_in_cooldown(conn, 0)


def test_janela_zero_libera_o_que_ja_foi_enviado():
    conn = make_conn()
    post_id = enfileirar(conn)
    mark_post_sent(conn, post_id)
    envelhecer(conn, post_id, 1)

    assert products_in_cooldown(conn, 0) == set()


# ---------- limite da janela ----------


def test_outro_produto_nao_e_afetado():
    conn = make_conn()
    record_offer(
        conn,
        Offer(
            source="mercadolivre",
            external_id="MLB2",
            title="Outro",
            price=50.0,
            url="https://mercadolivre.com.br/MLB2",
        ),
    )
    enfileirar(conn)

    assert products_in_cooldown(conn, 60) == {PRODUTO}

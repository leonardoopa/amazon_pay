"""O que sai porque o outro grupo postou, sai uma vez so -- para sempre.

Pedido do dono em 12/09/2026: "lembre que caso tenhamos enviado no grupo
aquela promocao, nao vamos mandar novamente".

O cooldown por si nao resolve este caso, e o motivo e estrutural. A fonte rele
as mesmas 50 mensagens do grupo deles a cada leitura, entao a oferta continua
aparecendo enquanto eles nao apagarem o post. Com um cooldown de N dias, um
post que fica uma semana no grupo deles rende um post nosso a cada N dias --
indefinidamente.

A regra vale so para o que NAO e medicao nossa. `score` afirma que o preco
caiu contra o nosso historico, e isso e razao propria para postar de novo,
venha o produto de onde vier. `score_campaign` e `score_pista` nao tem razao
propria nenhuma: a razao e que eles postaram.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.db import (  # noqa: E402
    SCHEMA,
    create_post,
    mark_post_failed,
    produtos_ja_postados,
    record_offer,
)
from promo.models import Offer  # noqa: E402


def oferta(external_id: str, preco: float = 49.99) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id=external_id,
        title=f"Produto {external_id}",
        price=preco,
        url=f"https://mercadolivre.com.br/{external_id}",
    )


def make_conn(*ids: str) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for i in ids:
        record_offer(conn, oferta(i))
    return conn


def test_produto_que_ja_saiu_aparece_na_lista():
    conn = make_conn("MLB1", "MLB2")
    create_post(conn, "mercadolivre:MLB1", 49.99, 69.99, 28.6, "texto")

    achados = produtos_ja_postados(conn, ["mercadolivre:MLB1", "mercadolivre:MLB2"])

    assert achados == {"mercadolivre:MLB1"}


def test_post_ainda_na_fila_ja_conta_como_enviado():
    """Vai sair. Deixar passar agora garante a dupla quando a fila drenar."""
    conn = make_conn("MLB1")
    create_post(conn, "mercadolivre:MLB1", 49.99, 69.99, 28.6, "texto")

    assert produtos_ja_postados(conn, ["mercadolivre:MLB1"]) == {"mercadolivre:MLB1"}


def test_post_que_falhou_nao_conta():
    """Nunca chegou ao grupo; o produto ainda tem uma chance."""
    conn = make_conn("MLB1")
    post = create_post(conn, "mercadolivre:MLB1", 49.99, 69.99, 28.6, "texto")
    mark_post_failed(conn, post, "sem conexao", max_attempts=1)

    assert produtos_ja_postados(conn, ["mercadolivre:MLB1"]) == set()


def test_sem_prazo_nenhum():
    """O cooldown responde "faz pouco tempo?"; esta responde "ja saiu?"."""
    conn = make_conn("MLB1")
    post = create_post(conn, "mercadolivre:MLB1", 49.99, 69.99, 28.6, "texto")
    conn.execute(
        "UPDATE posts SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (post,),
    )

    assert produtos_ja_postados(conn, ["mercadolivre:MLB1"]) == {"mercadolivre:MLB1"}


def test_lista_vazia_nao_consulta_o_banco():
    assert produtos_ja_postados(make_conn(), []) == set()


def test_muitos_produtos_numa_consulta_so():
    conn = make_conn(*[f"MLB{i}" for i in range(40)])
    for i in range(0, 40, 2):
        create_post(conn, f"mercadolivre:MLB{i}", 10.0, 20.0, 50.0, "texto")

    achados = produtos_ja_postados(conn, [f"mercadolivre:MLB{i}" for i in range(40)])

    assert len(achados) == 20
    assert "mercadolivre:MLB0" in achados
    assert "mercadolivre:MLB1" not in achados

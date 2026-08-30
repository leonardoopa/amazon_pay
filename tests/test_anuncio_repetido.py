"""O mesmo produto nao volta ao grupo por outro anuncio.

O ML lista o mesmo produto fisico como dezenas de anuncios de vendedores
diferentes -- `external_id` distinto, e portanto `product_id` distinto. O
cooldown por produto nao ve isso, e em quinze minutos o grupo recebeu:

    Short Saia Esportivo Feminino Ausare de Lycra...      R$  95,00
    Short Saia Esportivo Feminino Ausare com Protecao...  R$  99,90
    Short Saia Esportivo Feminino Ausare com Protecao...  R$ 126,51
    Short Saia Esportivo Ausare Feminino, para Treino...  R$  89,90

Quatro anuncios, quatro precos, o mesmo short. Precos diferentes tornam a
repeticao pior, nao melhor: parece que o grupo nao sabe o preco.
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
    familia_do_titulo,
    families_in_cooldown,
    mark_post_sent,
    now,
    record_offer,
)
from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import _uma_por_familia  # noqa: E402

SHORTS = [
    "Short Saia Esportivo Feminino Ausare de Lycra com Toque Gelado e Proteção UV 50+",
    "Short Saia Esportivo Feminino Ausare com Proteção UV 50+ e Toque Gelado",
    "Short Saia Esportivo Feminino Ausare com Proteção UV50+ e Toque Gelado",
    "Short Saia Esportivo Ausare Feminino, para Treino e Beach Tennis",
]


# ---------- a assinatura ----------


def test_os_quatro_anuncios_do_short_caem_na_mesma_familia():
    assert len({familia_do_titulo(t) for t in SHORTS}) == 1


def test_acento_e_caixa_nao_separam():
    assert familia_do_titulo("Papel Higiênico Personal") == familia_do_titulo(
        "papel higienico personal"
    )


def test_numero_nao_entra_na_assinatura():
    """"UV 50+" e "UV50+" nao podem virar produtos diferentes."""
    assert familia_do_titulo("Camiseta Dry Fit 2 unidades") == familia_do_titulo(
        "Camiseta Dry Fit 3 unidades"
    )


def test_produtos_de_fato_diferentes_continuam_separados():
    """A regra nao pode ser tao larga que junte o que e diferente."""
    assert familia_do_titulo("Mochila jiesipote À Prova D'água") != familia_do_titulo(
        "Mochila Notebook Ntesx Grande Expansível"
    )
    assert familia_do_titulo("Smart Tv 43 Samsung Crystal") != familia_do_titulo(
        "Smart Tv 40 Philco Roku"
    )
    assert familia_do_titulo("Papel Higiênico Personal Vip") != familia_do_titulo(
        "Papel higiênico Neve Toque da Seda"
    )


def test_titulo_vazio_nao_estoura():
    assert familia_do_titulo("") == ""
    assert familia_do_titulo("2026 500 40") == ""


# ---------- dedup dentro da rodada ----------


def escolhida(titulo: str, preco: float, external_id: str) -> ScoredOffer:
    return ScoredOffer(
        offer=Offer(
            source="mercadolivre",
            external_id=external_id,
            title=titulo,
            price=preco,
            url=f"https://mercadolivre.com.br/{external_id}",
        ),
        baseline=300.0,
        discount_pct=(300.0 - preco) / 300.0 * 100,
        observations=9,
        lowest_ever=False,
        verified=True,
    )


def test_so_um_anuncio_da_familia_sobrevive_a_rodada():
    candidatos = [escolhida(t, 95.0 + i, f"MLB{i}") for i, t in enumerate(SHORTS)]

    saida = _uma_por_familia(candidatos)

    assert len(saida) == 1


def test_fica_com_o_primeiro_que_e_o_de_maior_desconto():
    """A lista chega ordenada por desconto: o primeiro e a melhor oferta."""
    candidatos = [
        escolhida(SHORTS[0], 89.90, "MLB1"),
        escolhida(SHORTS[1], 126.51, "MLB2"),
    ]

    assert _uma_por_familia(candidatos)[0].offer.price == 89.90


def test_familias_diferentes_passam_todas():
    candidatos = [
        escolhida("Mochila jiesipote À Prova D'água", 86.0, "MLB1"),
        escolhida("Mochila Notebook Ntesx Grande", 95.51, "MLB2"),
        escolhida("Kit Cobre Leito Colcha Casal", 76.62, "MLB3"),
    ]

    assert len(_uma_por_familia(candidatos)) == 3


def test_lista_vazia_nao_estoura():
    assert _uma_por_familia([]) == []


# ---------- cooldown entre rodadas ----------


def conexao_com(titulo: str, external_id: str = "MLB1") -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    record_offer(
        conn,
        Offer(
            source="mercadolivre",
            external_id=external_id,
            title=titulo,
            price=95.0,
            url=f"https://mercadolivre.com.br/{external_id}",
        ),
    )
    return conn


def test_familia_recem_postada_fica_bloqueada():
    conn = conexao_com(SHORTS[0])
    post_id = create_post(conn, "mercadolivre:MLB1", 95.0, 300.0, 68.0, "texto")
    mark_post_sent(conn, post_id)

    assert familia_do_titulo(SHORTS[1]) in families_in_cooldown(conn, 60)


def test_familia_antiga_libera():
    conn = conexao_com(SHORTS[0])
    post_id = create_post(conn, "mercadolivre:MLB1", 95.0, 300.0, 68.0, "texto")
    mark_post_sent(conn, post_id)
    conn.execute(
        "UPDATE posts SET sent_at = ?",
        ((now() - timedelta(minutes=90)).isoformat(),),
    )

    assert families_in_cooldown(conn, 60) == set()


def test_post_na_fila_ja_bloqueia_a_familia():
    """Ainda nao saiu, mas vai. Enfileirar o irmao garante a repeticao."""
    conn = conexao_com(SHORTS[0])
    create_post(conn, "mercadolivre:MLB1", 95.0, 300.0, 68.0, "texto")

    assert familia_do_titulo(SHORTS[3]) in families_in_cooldown(conn, 60)


def test_outra_familia_nao_e_bloqueada():
    conn = conexao_com(SHORTS[0])
    post_id = create_post(conn, "mercadolivre:MLB1", 95.0, 300.0, 68.0, "texto")
    mark_post_sent(conn, post_id)

    bloqueadas = families_in_cooldown(conn, 60)
    assert familia_do_titulo("Mochila Notebook Ntesx Grande") not in bloqueadas

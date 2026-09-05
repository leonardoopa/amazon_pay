"""Uma rajada do mesmo TIPO de produto nao e cinco ofertas, e uma repeticao.

Os dois cooldowns que ja existiam olham para baixo: `products_in_cooldown` ve o
anuncio, `families_in_cooldown` ve o produto. Nenhum dos dois ve o tipo, e e o
tipo que o leitor percebe.

Medido em producao em 04/09/2026, nos ultimos 200 posts (11h30 de grupo):

    product_id repetido               0
    familia de 3 palavras repetida   27  (a menor distancia, 3h18)
    assinatura de 2 palavras         48  (a menor distancia, 0h02)

E a terceira linha que o grupo estava recebendo:

    Whey Protein Isolado Iso Blend Complex Zero Acucar
    Whey Protein 1kg Whey Pro Max Titanium Sabor Morango     +54 min
    Whey protein concentrado 900g doce de leite              +78 min
    Whey Protein Concentrado 1kg Sabor Chocolate            +100 min

Quatro fabricantes, quatro precos, quatro `product_id`. Para a regra sao quatro
produtos; para quem le sao whey, whey, whey e whey.

A janela e propria e curta. Whey, tenis e camiseta Hering sao justamente os
temas prioritarios, e sao os que mais colidem aqui -- reusar as 12h do produto
tiraria do ar o que mais converte. Duas horas espacam a rajada e ainda deixam
doze posts de tenis por dia.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import config  # noqa: E402
from promo.db import (  # noqa: E402
    CATEGORIA,
    SCHEMA,
    categories_in_cooldown,
    create_post,
    familia_do_titulo,
    families_in_cooldown,
    mark_post_sent,
    now,
    record_offer,
)
from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import _uma_por_familia  # noqa: E402


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def postar(conn: sqlite3.Connection, external_id: str, title: str, minutos: int) -> None:
    """Grava um post ja enviado ha N minutos."""
    record_offer(
        conn,
        Offer(
            source="mercadolivre",
            external_id=external_id,
            title=title,
            price=100.0,
            url=f"https://mercadolivre.com.br/{external_id}",
        ),
    )
    post_id = create_post(
        conn, f"mercadolivre:{external_id}", 100.0, 150.0, 33.0, "texto"
    )
    mark_post_sent(conn, post_id)
    quando = (now() - timedelta(minutes=minutos)).isoformat()
    conn.execute("UPDATE posts SET sent_at = ? WHERE id = ?", (quando, post_id))


def oferta(external_id: str, title: str, desconto: float = 30.0) -> ScoredOffer:
    return ScoredOffer(
        offer=Offer(
            source="mercadolivre",
            external_id=external_id,
            title=title,
            price=100.0,
            url=f"https://mercadolivre.com.br/{external_id}",
        ),
        baseline=150.0,
        discount_pct=desconto,
        observations=9,
        lowest_ever=False,
        verified=True,
    )


# ---------- a assinatura grossa ----------


def test_a_categoria_junta_o_que_a_familia_separa():
    """Os quatro wheys medidos em producao caem na mesma categoria."""
    wheys = [
        "Whey Protein Isolado Iso Blend Complex Zero Acucar",
        "Whey Protein 1kg Whey Pro Max Titanium Sabor Morango",
        "Whey protein concentrado 900g doce de leite",
        "Whey Protein Concentrado 1kg Sabor Chocolate",
    ]
    categorias = {familia_do_titulo(t, CATEGORIA) for t in wheys}
    familias = {familia_do_titulo(t) for t in wheys}

    assert len(categorias) == 1
    assert len(familias) > 1  # e por isso que a familia deixava passar


def test_a_categoria_e_prefixo_da_familia():
    """Mesma familia implica mesma categoria -- a curta e um corte da longa.

    E o que deixa uma passagem so de `_uma_por_familia` cobrir os dois niveis.
    """
    titulos = [
        "Tenis De Corrida Masculino Questar 4 adidas Branco",
        "Tenis De Corrida Feminino Questar 4 adidas Preto",
    ]
    assert len({familia_do_titulo(t) for t in titulos}) == 1
    assert len({familia_do_titulo(t, CATEGORIA) for t in titulos}) == 1


def test_categorias_de_verdade_diferentes_nao_colidem():
    assert familia_do_titulo("Smart TV Samsung 50 Polegadas", CATEGORIA) != (
        familia_do_titulo("Cadeira Gamer ThunderX3", CATEGORIA)
    )


# ---------- o que bloqueia ----------


def test_whey_recente_bloqueia_outro_whey():
    conn = make_conn()
    postar(conn, "MLB1", "Whey Protein Isolado Iso Blend Complex", minutos=30)

    bloqueadas = categories_in_cooldown(conn, 120)

    assert familia_do_titulo("Whey Protein 1kg Pro Max Titanium", CATEGORIA) in bloqueadas


def test_post_na_fila_tambem_bloqueia_a_categoria():
    """A fila e persistente: enfileirar o segundo whey garante a rajada em vez
    de arriscar ela."""
    conn = make_conn()
    record_offer(
        conn,
        Offer(
            source="mercadolivre",
            external_id="MLB1",
            title="Whey Protein Isolado Iso Blend",
            price=100.0,
            url="https://mercadolivre.com.br/MLB1",
        ),
    )
    create_post(conn, "mercadolivre:MLB1", 100.0, 150.0, 33.0, "texto")

    assert categories_in_cooldown(conn, 120)


# ---------- o que libera ----------


def test_fora_da_janela_libera():
    conn = make_conn()
    postar(conn, "MLB1", "Whey Protein Isolado Iso Blend Complex", minutos=180)

    assert categories_in_cooldown(conn, 120) == set()


def test_categoria_diferente_nao_e_afetada():
    conn = make_conn()
    postar(conn, "MLB1", "Whey Protein Isolado Iso Blend Complex", minutos=30)

    bloqueadas = categories_in_cooldown(conn, 120)

    assert familia_do_titulo("Cadeira Gamer ThunderX3 EC3", CATEGORIA) not in bloqueadas


def test_banco_sem_post_nao_bloqueia_nada():
    assert categories_in_cooldown(make_conn(), 120) == set()


# ---------- desligar ----------


def test_janela_zero_desliga_a_regra_inteira():
    """Diferente do cooldown de produto, aqui o zero libera ate a fila: quem
    nao configurar a variavel nao muda de comportamento, e a fila continua
    coberta pelos outros dois niveis."""
    conn = make_conn()
    postar(conn, "MLB1", "Whey Protein Isolado Iso Blend", minutos=1)
    create_post(conn, "mercadolivre:MLB1", 100.0, 150.0, 33.0, "texto")

    assert categories_in_cooldown(conn, 0) == set()


def test_o_padrao_e_desligado(monkeypatch):
    monkeypatch.delenv("CATEGORY_COOLDOWN_MINUTES", raising=False)

    assert config.category_cooldown_minutes() == 0


def test_le_a_variavel_de_ambiente(monkeypatch):
    monkeypatch.setenv("CATEGORY_COOLDOWN_MINUTES", "120")

    assert config.category_cooldown_minutes() == 120


def test_desligada_a_categoria_nao_bloqueia_pelo_ambiente(monkeypatch):
    monkeypatch.delenv("CATEGORY_COOLDOWN_MINUTES", raising=False)
    conn = make_conn()
    postar(conn, "MLB1", "Whey Protein Isolado Iso Blend", minutos=1)

    assert categories_in_cooldown(conn) == set()


# ---------- as duas janelas sao independentes ----------


def test_a_janela_da_categoria_e_menor_que_a_do_produto():
    """O caso que motiva ter duas: 3h depois, o mesmo whey ainda esta barrado
    pelo produto, mas outro whey ja pode sair."""
    conn = make_conn()
    postar(conn, "MLB1", "Whey Protein Isolado Iso Blend Complex", minutos=180)

    assert families_in_cooldown(conn, 720)  # 12h: o produto segue barrado
    assert categories_in_cooldown(conn, 120) == set()  # 2h: o tipo ja liberou


# ---------- dentro da mesma rodada ----------


def test_uma_por_categoria_corta_a_rajada_da_rodada():
    """O cooldown olha o que ja saiu; isto olha o que esta sendo escolhido
    agora. Sem os dois, os tres wheys entram juntos na mesma rodada e o
    cooldown so pega a partir da proxima."""
    escolhidas = [
        oferta("MLB1", "Whey Protein Isolado Iso Blend", 50.0),
        oferta("MLB2", "Whey Protein 1kg Pro Max Titanium", 40.0),
        oferta("MLB3", "Whey protein concentrado 900g doce de leite", 30.0),
        oferta("MLB4", "Cadeira Gamer ThunderX3 EC3", 25.0),
    ]

    saida = _uma_por_familia(escolhidas, CATEGORIA)

    assert [s.offer.external_id for s in saida] == ["MLB1", "MLB4"]


def test_uma_por_categoria_fica_com_o_melhor_desconto():
    """A lista chega ordenada por desconto, entao a primeira de cada categoria
    e a melhor dela."""
    escolhidas = [
        oferta("MLB1", "Whey Protein Isolado Iso Blend", 60.0),
        oferta("MLB2", "Whey Protein 1kg Pro Max Titanium", 20.0),
    ]

    saida = _uma_por_familia(escolhidas, CATEGORIA)

    assert [s.discount_pct for s in saida] == [60.0]


def test_tres_palavras_continua_sendo_o_padrao():
    """Sem argumento, o comportamento e o de antes: dois tenis adidas de
    modelos diferentes passam."""
    escolhidas = [
        oferta("MLB1", "Tenis adidas Solado Boost Run Corrida"),
        oferta("MLB2", "Tenis adidas Sem Genero Ih4039 Liso"),
    ]

    assert len(_uma_por_familia(escolhidas)) == 2
    assert len(_uma_por_familia(escolhidas, CATEGORIA)) == 1


def test_titulo_sem_palavra_significativa_nao_agrupa_com_ninguem():
    """Assinatura vazia nao pode virar um balde que engole tudo."""
    escolhidas = [oferta("MLB1", "de a com"), oferta("MLB2", "para o na")]

    assert len(_uma_por_familia(escolhidas, CATEGORIA)) == 2

"""O cooldown por TIPO de produto -- escrito em 04/09/2026, DESLIGADO em 12/09.

A regra nasceu de uma medicao boa e de uma leitura errada do que ela dizia.
Nos 200 posts de 04/09/2026 havia 48 repeticoes de assinatura de duas
palavras, e o grupo recebia isto:

    Whey Protein Isolado Iso Blend Complex Zero Acucar
    Whey Protein 1kg Whey Pro Max Titanium Sabor Morango     +54 min
    Whey protein concentrado 900g doce de leite              +78 min
    Whey Protein Concentrado 1kg Sabor Chocolate            +100 min

A conclusao na epoca foi "para quem le sao whey, whey, whey e whey". O dono
disse o contrario em 12/09/2026: sao quatro fabricantes, e quatro fabricantes
sao quatro ofertas. "Pode ser enviado whey varias vezes, portanto que sejam de
marcas diferentes."

O que ele NAO quer e o mesmo produto da mesma marca voltando -- e para isso a
assinatura de duas palavras era a ferramenta errada dos dois lados: ela junta
marcas diferentes que comecam igual, e separa a mesma marca quando o titulo
comeca diferente. Quem cuida disso agora e `db.mesmo_produto`, que compara os
titulos inteiros; ver `tests/test_mesmo_produto.py`.

O codigo continua aqui, com `CATEGORY_COOLDOWN_MINUTES=0` em producao. Os
testes abaixo guardam o comportamento da funcao para quem quiser religa-la.
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
from promo.pipeline import _um_por_produto  # noqa: E402


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
    """Mesma familia implica mesma categoria -- a curta e um corte da longa."""
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
#
# A selecao da rodada passou a usar `_um_por_produto`, que compara titulo com
# titulo. Estes testes guardam a regra NOVA, que e o oposto da que estava aqui:
# quatro wheys de quatro marcas saem juntos, e dois anuncios do mesmo whey nao.


def test_wheys_de_marcas_diferentes_saem_todos_na_mesma_rodada():
    """Pedido do dono em 12/09/2026, com os titulos que motivaram a regra
    antiga -- que barrava tres dos quatro."""
    escolhidas = [
        oferta("MLB1", "Whey Protein Isolado Iso Blend", 50.0),
        oferta("MLB2", "Whey Protein 1kg Pro Max Titanium", 40.0),
        oferta("MLB3", "Whey Protein Concentrado Growth Supplements", 30.0),
        oferta("MLB4", "Cadeira Gamer ThunderX3 EC3", 25.0),
    ]

    saida = _um_por_produto(escolhidas)

    assert [s.offer.external_id for s in saida] == ["MLB1", "MLB2", "MLB3", "MLB4"]


def test_o_mesmo_whey_em_dois_anuncios_sai_uma_vez_so():
    """Mesma marca, mesmo produto, tamanhos diferentes: e um post."""
    escolhidas = [
        oferta("MLB1", "Whey Isolate Protein Fuse Refil 1,8kg Dark Lab", 50.0),
        oferta("MLB2", "Whey Isolate Protein Fuse 900g Dark Lab", 40.0),
    ]

    assert len(_um_por_produto(escolhidas)) == 1


def test_fica_com_o_primeiro_que_e_o_de_maior_desconto():
    """A lista chega ordenada por desconto."""
    escolhidas = [
        oferta("MLB1", "Whey Isolate Protein Fuse Refil 1,8kg Dark Lab", 60.0),
        oferta("MLB2", "Whey Isolate Protein Fuse 900g Dark Lab", 20.0),
    ]

    assert [s.discount_pct for s in _um_por_produto(escolhidas)] == [60.0]


def test_dois_tenis_adidas_de_modelos_diferentes_passam():
    escolhidas = [
        oferta("MLB1", "Tenis adidas Solado Boost Run Corrida"),
        oferta("MLB2", "Tenis adidas Sem Genero Ih4039 Liso"),
    ]

    assert len(_um_por_produto(escolhidas)) == 2


def test_titulo_sem_palavra_significativa_nao_agrupa_com_ninguem():
    """Titulo sem conteudo nao pode virar um balde que engole tudo."""
    escolhidas = [oferta("MLB1", "de a com"), oferta("MLB2", "para o na")]

    assert len(_um_por_produto(escolhidas)) == 2

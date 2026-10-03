"""Comida e bebida dos grupos-fonte: prioridade, sempre.

Pedido do dono em 02/10/2026, depois de o xet postar "Vodka Grey Goose Original
750ml" (De R$ 174 por R$ 104 em 3x) e a oferta nao chegar ao grupo. Era a
segunda vez: em 23/09 a mesma vodka virou post, ficou sessenta minutos na fila e
expirou sem sair.

Duas causas, as duas medidas em producao:

- A fila. `MAX_PENDING_QUEUE` conta os posts de TODOS os grupos, e com 78
  pendentes contra um teto de 30 nenhuma oferta de outro grupo entrava.
- A disputa. O Geral envia 6 posts por hora, e o repasse inteiro tem a mesma
  prioridade: em 72h, 71 ofertas da Amazon repassadas expiraram contra 71
  enviadas.

O que se muda e a ORDEM e a ADMISSAO. O ritmo de envio -- gotejamento e teto por
hora -- continua o mesmo.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import (  # noqa: E402
    _VISTOS_EM_OUTRO_GRUPO,
    _admitir_pistas,
    _gravar_post,
    _prioridade_do_post,
    e_comida_ou_bebida,
    load_comida_e_bebida,
)


def oferta(titulo: str, external_id: str = "B006ILMIGE", source: str = "amazon") -> Offer:
    return Offer(
        source=source,
        external_id=external_id,
        title=titulo,
        price=104.0,
        url=f"https://www.amazon.com.br/dp/{external_id}",
        original_price=174.0,
    )


def pontuada(titulo: str, external_id: str) -> ScoredOffer:
    return ScoredOffer(
        offer=oferta(titulo, external_id),
        baseline=174.0,
        discount_pct=40.0,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


@pytest.fixture(autouse=True)
def limpa_vistos():
    _VISTOS_EM_OUTRO_GRUPO.clear()
    yield
    _VISTOS_EM_OUTRO_GRUPO.clear()


# ---------- o que e comida e bebida ----------


@pytest.mark.parametrize(
    "titulo",
    [
        "Vodka Grey Goose Original 750ml",  # o caso do pedido
        "Vodka Absolut Tabasco 750ml",
        "Whisky Johnnie Walker Red Label 1L",
        "Gin Tanqueray London Dry 750ml",
        "Cerveja Heineken Lata 350ml Pack 12",
        "Cervejas Sortidas Kit 6 Unidades",  # plural
        "Vinho Tinto Chileno Cabernet Sauvignon",
        "Espumante Chandon Brut 750ml",
        "Café Torrado Moído 3 Corações Tradicional 500g",
        "Cápsulas Nespresso Café Ristretto 10un",
        "L'OR Café Solúvel Intense Pote De Vidro 130G",
        "Leite Condensado Moça 395g",
        "Creme de Leite Nestlé 200g",
        "Chocolate Lindt Excellence 70% Cacau",
        "Biscoitos Recheados Oreo 36g Kit 10",  # plural
        "Cereal Matinal Snow Churros 170g",
        "Azeite de Oliva Extra Virgem Gallo 500ml",
        "Mel Orgânico Silvestre 500g",
        "Suco de Uva Integral Aurora 1,5L",
        "Energético Red Bull 250ml Pack 24",
        "Panetone Bauducco Gotas de Chocolate 500g",
        "Pasta de Amendoim Integral Mandubim 1kg",
        # Casos reais do #BVA em 03/10/2026 que o primeiro corte da lista deixou passar.
        "Pringles Pack Promo 3 Sabores Batata Frita",
        "Refrigerante Coca-Cola Original 2L Pack 6",
        "Cerveja Skol Pilsen Lata 269ml Pack 15",
        "Leite UHT Integral Italac 1L Caixa 12",
        "Ovos Brancos Grandes Caixa 30 Unidades",
    ],
)
def test_comida_e_bebida_de_verdade(titulo):
    assert e_comida_ou_bebida(oferta(titulo)), titulo


@pytest.mark.parametrize(
    "titulo",
    [
        # Cita a bebida e nao e ela.
        "Máquina de Café Expresso Nespresso Vertuo Next",
        "Cafeteira Elétrica Philips Walita 15 Xícaras",
        "Taça de Vinho Cristal Kit 6 Peças",
        "Copo Térmico Stanley para Cerveja 473ml",
        "Adega Climatizada para Vinhos 12 Garrafas",
        "Camiseta Cerveja Skol Beats Masculina",
        "Perfume Vodka Extreme Paris Elysees Masculino 100ml",  # o nome e de bebida
        "Hidratante Corporal Mel e Aveia 200ml",
        "Creme Facial Hidratante com Leite de Cabra",
        # Palavra dentro de outra palavra.
        "Tênis de Ginástica Esportivo Masculino",  # "gin" em "ginastica"
        "Melhor Perfume Importado 100ml",  # "mel" em "melhor"
        "Fritadeira Airfryer Philco 4,3L",
        "Fone Bluetooth JBL Tune 510BT",
        "Whey Protein Concentrado 900g Morango",  # suplemento nao e comida aqui
        # Casos reais de 96h de repasse, que citavam comida sem ser comida.
        "100% Whey Protein 900g Sabores - Dark Lab Sabor Cappuccino",  # sabor
        "Proteína Dux Nutrition FreshWhey em pó sabor Pistache e Chocolate Branco",
        "Kit Remendo Metal Reparo Macarrão Pneu Carro Moto Com Maleta",
        "Kit 4 Blusa Regata Canelada Feminina Nadador Preto/marrom/vinho",  # cor
    ],
)
def test_o_que_cita_a_palavra_mas_nao_e_comida_nem_bebida(titulo):
    assert not e_comida_ou_bebida(oferta(titulo)), titulo


def test_as_listas_do_watchlist_real_existem():
    termos, nao_e = load_comida_e_bebida()

    assert "vodka" in termos and "cerveja" in termos and "cafe" in termos
    assert "maquina" in nao_e and "perfume" in nao_e


def test_lista_vazia_nao_marca_nada():
    assert not e_comida_ou_bebida(oferta("Vodka Grey Goose"), [], [])


# ---------- a faixa da fila ----------


def test_comida_de_outro_grupo_ganha_a_faixa_2():
    o = oferta("Vodka Grey Goose Original 750ml")
    _VISTOS_EM_OUTRO_GRUPO.add(o.external_id)

    assert _prioridade_do_post(o) == 2


def test_o_resto_do_repasse_continua_na_faixa_1():
    o = oferta("Jaqueta Masculina Field & Stream Capuz", "B0JAQUETA1")
    _VISTOS_EM_OUTRO_GRUPO.add(o.external_id)

    assert _prioridade_do_post(o) == 1


def test_o_que_nos_escolhemos_continua_na_faixa_0():
    """Comida da nossa propria coleta nao sobe: o pedido e sobre o repasse."""
    assert _prioridade_do_post(oferta("Vodka Absolut 750ml")) == 0


def banco() -> sqlite3.Connection:
    from promo.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def test_o_post_da_vodka_nasce_com_a_faixa_2():
    from promo.db import record_offer

    conn = banco()
    scored = pontuada("Vodka Grey Goose Original 750ml", "B006ILMIGE")
    record_offer(conn, scored.offer)
    _VISTOS_EM_OUTRO_GRUPO.add("B006ILMIGE")

    _gravar_post(conn, scored, "texto", "")

    assert conn.execute("SELECT prioridade FROM posts").fetchone()[0] == 2


def test_a_faixa_2_sai_antes_da_1_que_sai_antes_da_0():
    from promo.db import create_post, pending_posts, record_offer

    conn = banco()
    ids = {}
    for nome, prioridade in (("zero", 0), ("um", 1), ("dois", 2)):
        o = oferta(f"Produto {nome}", f"X{nome}")
        record_offer(conn, o)
        ids[nome] = create_post(
            conn, o.product_id, 104.0, 174.0, 40.0, "texto", prioridade=prioridade
        )

    ordem = [linha["id"] for linha in pending_posts(conn)]

    assert ordem == [ids["dois"], ids["um"], ids["zero"]]


# ---------- a admissao na rodada ----------


def lote(*titulos: str) -> list[ScoredOffer]:
    return [pontuada(t, f"ID{i}") for i, t in enumerate(titulos)]


def titulos(escolhidas: list[ScoredOffer]) -> list[str]:
    return [s.offer.title for s in escolhidas]


def test_com_a_fila_cheia_so_a_comida_entra(monkeypatch):
    """O caso de producao: 78 pendentes contra teto 30. Nada de outro grupo
    entrava -- a vodka incluida."""
    monkeypatch.delenv("MAX_PENDING_QUEUE", raising=False)
    novas = lote("Jaqueta Field & Stream", "Vodka Grey Goose 750ml", "Relógio Invicta")

    admitidas = _admitir_pistas(novas, na_fila=78, ja_escolhidas=0)

    assert titulos(admitidas) == ["Vodka Grey Goose 750ml"]


def test_a_comida_vai_na_frente_do_resto(monkeypatch):
    monkeypatch.delenv("MAX_PENDING_QUEUE", raising=False)
    novas = lote("Jaqueta Field & Stream", "Relógio Invicta", "Vodka Grey Goose 750ml")

    admitidas = _admitir_pistas(novas, na_fila=0, ja_escolhidas=0)

    assert titulos(admitidas)[0] == "Vodka Grey Goose 750ml"
    assert len(admitidas) == 3


def test_a_comida_ocupa_a_folga_antes_do_resto(monkeypatch):
    """O total nao passa do que a fila comporta quando ha folga."""
    monkeypatch.setenv("MAX_PENDING_QUEUE", "3")
    novas = lote("Jaqueta A", "Jaqueta B", "Vodka Grey Goose 750ml", "Jaqueta C")

    admitidas = _admitir_pistas(novas, na_fila=0, ja_escolhidas=0)

    assert titulos(admitidas) == ["Vodka Grey Goose 750ml", "Jaqueta A", "Jaqueta B"]


def test_o_freio_da_comida_vale(monkeypatch):
    """Sem freio, um dia de promocao de supermercado furaria a fila inteira."""
    monkeypatch.setenv("MAX_PENDING_QUEUE", "30")
    monkeypatch.setenv("MAX_COMIDA_E_BEBIDA_POR_RODADA", "2")
    novas = lote("Vodka A", "Cerveja B", "Vinho C", "Whisky D")

    admitidas = _admitir_pistas(novas, na_fila=78, ja_escolhidas=0)

    assert titulos(admitidas) == ["Vodka A", "Cerveja B"]


def test_zero_desliga_a_excecao(monkeypatch):
    monkeypatch.setenv("MAX_PENDING_QUEUE", "30")
    monkeypatch.setenv("MAX_COMIDA_E_BEBIDA_POR_RODADA", "0")

    admitidas = _admitir_pistas(lote("Vodka A"), na_fila=78, ja_escolhidas=0)

    assert admitidas == []


def test_o_que_o_freio_cortou_nao_volta_pela_porta_do_resto(monkeypatch):
    """A comida que passou do freio e candidata do resto; sem folga, fica fora."""
    monkeypatch.setenv("MAX_PENDING_QUEUE", "30")
    monkeypatch.setenv("MAX_COMIDA_E_BEBIDA_POR_RODADA", "1")
    novas = lote("Vodka A", "Cerveja B")

    admitidas = _admitir_pistas(novas, na_fila=78, ja_escolhidas=0)

    assert titulos(admitidas) == ["Vodka A"]

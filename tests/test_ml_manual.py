"""Oferta do Mercado Livre escolhida à mão (`promo ml-add`).

Existe desde 27/11/2026: o ML passou a recusar o preço do catálogo (403 do
PolicyAgent) e a redirecionar o servidor para a verificação de conta nas páginas
públicas. Sem fonte automática, o kit Wella a 49% só chega ao grupo pela mão.
"""

from __future__ import annotations

import pytest

from conftest import ContextoDeConexao
from promo import manual
from promo.manual import OfertaInvalida
from promo.pipeline import GrupoDestino

URL = (
    "https://www.mercadolivre.com.br/kit-wella-oil-reflections-shampoo-250ml-"
    "mascara-150ml/p/MLB63180217?pdp_filters=item_id%3AMLB1#polycard_client=x"
)
TITULO = "Kit Wella Oil Reflections Shampoo 250ml + Máscara 150ml"
LINK = "https://mercadolivre.com/sec/abc123"

GRUPOS = [
    GrupoDestino(jid="", nome="Geral"),
    GrupoDestino(jid="mulheres@g.us", nome="Mulheres", temas=("wella",)),
    GrupoDestino(jid="esportes@g.us", nome="Esportes", temas=("nike",)),
]


def _preparar(**troca):
    dados = {"url": URL, "titulo": TITULO, "preco": 141.96, "de": 283.0}
    dados.update(troca)
    return manual.preparar_oferta_ml(**dados)


@pytest.fixture
def banco(banco_em_memoria, monkeypatch):
    monkeypatch.setattr(manual, "connect", lambda: ContextoDeConexao(banco_em_memoria))
    monkeypatch.setattr(manual, "init_db", lambda: None)
    return banco_em_memoria


def test_monta_a_oferta_com_endereco_limpo_e_id_do_catalogo():
    scored = _preparar()

    assert scored.offer.product_id == "mercadolivre:MLB63180217"
    assert scored.offer.url == URL.split("?")[0]
    assert round(scored.discount_pct) == 50
    assert scored.verified is False


def test_aceita_endereco_de_anuncio_de_vendedor():
    scored = _preparar(
        url="https://produto.mercadolivre.com.br/MLB-1383181630-kit-wella-_JM"
    )

    assert scored.offer.external_id == "MLB1383181630"


@pytest.mark.parametrize(
    "troca",
    [
        {"url": "https://www.amazon.com.br/dp/B07DVJC66X"},
        {"url": "https://mercadolivre.com.br.golpe.com/p/MLB63180217"},
        {"url": "https://www.mercadolivre.com.br/ofertas"},
        {"titulo": "  "},
        {"preco": 0},
        {"preco": 300.0},
    ],
)
def test_recusa_entrada_que_nao_vira_post(troca):
    with pytest.raises(OfertaInvalida):
        _preparar(**troca)


def test_enfileira_so_nos_grupos_que_aceitam_o_titulo_e_na_frente_da_fila(banco):
    ids = manual.enfileirar_ml(_preparar(), "texto", LINK, GRUPOS)

    linhas = banco.execute(
        "SELECT grupo_jid, prioridade, status, verified FROM posts ORDER BY id"
    ).fetchall()
    assert len(ids) == 2
    assert [tuple(linha) for linha in linhas] == [
        ("", 2, "pending", 0),
        ("mulheres@g.us", 2, "pending", 0),
    ]
    assert banco.execute("SELECT url FROM affiliate_links").fetchone()[0] == LINK


def test_sem_grupo_que_aceite_nada_e_enfileirado(banco):
    with pytest.raises(OfertaInvalida):
        manual.enfileirar_ml(_preparar(), "texto", LINK, GRUPOS[2:])

    assert banco.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


@pytest.mark.parametrize(
    "link", ["https://golpe.com/sec/abc", "http://exemplo.com", "", None]
)
def test_link_de_fora_do_mercado_livre_e_recusado(banco, link):
    with pytest.raises(OfertaInvalida):
        manual.enfileirar_ml(_preparar(), "texto", link, GRUPOS)

    assert banco.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0

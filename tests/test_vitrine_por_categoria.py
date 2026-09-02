"""A vitrine filtrada por categoria, que e onde a marca de roupa aparece.

O grupo pediu short Nike, tenis adidas, camisa Hering. Nenhum deles chega pelo
catalogo -- medido, toda variante devolve zero, porque roupa de marca no ML e
anuncio de vendedor e nao produto de catalogo. As duas vias de anuncio que
existiriam estao fechadas:

    /sites/MLB/search              HTTP 403 (bloqueado desde 2025)
    lista.mercadolivre.com.br      redireciona para /gz/account-verification

Sobra a vitrine /ofertas, que ja liamos. E ela ACEITA `?category=`. Medido em
01/09/2026, e o numero que justifica este arquivo:

    /ofertas                    648 KB, 1.621 precos, ZERO Nike/adidas/Puma
    /ofertas?category=MLB23262  291 KB,   422 precos, 36 ocorrencias de adidas
    /ofertas?category=MLB1430&page=2      485 precos, 25 ocorrencias de adidas

O produto estava na campanha o tempo todo. So nao estava nas duas primeiras
paginas da vitrine crua.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.pipeline import (  # noqa: E402
    collect_vitrine,
    load_vitrine_categories,
)
from promo.sources.ml_ofertas import MLOfertas, ParseFailed  # noqa: E402


def card(external_id: str, titulo: str, preco: float, riscado: float | None = None):
    componentes = [
        {"type": "title", "title": {"text": titulo}},
        {
            "type": "price",
            "price": {
                "current_price": {"value": preco},
                "price_labels": (
                    [
                        {
                            "values": [
                                {"key": "previous_price", "price": {"value": riscado}}
                            ]
                        }
                    ]
                    if riscado
                    else []
                ),
            },
        },
    ]
    return {
        "card": {
            "metadata": {
                "id": external_id,
                "url": f"www.mercadolivre.com.br/p/{external_id}",
            },
            "components": componentes,
            "pictures": {"pictures": [{"id": "123456"}]},
        }
    }


def pagina(items: list[dict]) -> str:
    payload = {"appProps": {"pageProps": {"data": {"items": items}}}}
    return (
        '<script id="__NORDIC_RENDERING_CTX__" type="application/json">'
        f"window.__CTX__ = {json.dumps(payload)};</script>"
    )


def fonte(por_url) -> MLOfertas:
    """`por_url` recebe (params) e devolve a lista de items daquela pagina."""
    pedidas: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        pedidas.append(params)
        return httpx.Response(200, text=pagina(por_url(params)))

    ml = MLOfertas()
    ml._client = httpx.Client(transport=httpx.MockTransport(handler))
    ml.pedidas = pedidas  # type: ignore[attr-defined]
    return ml


# ---------- a requisicao ----------


def test_sem_categoria_pede_so_a_vitrine_crua():
    """Comportamento antigo intacto: quem nao configurar nada nao muda."""
    ml = fonte(lambda p: [card("MLB1", "Fone", 99.0, 149.0)])

    ml.fetch(pages=2)

    assert ml.pedidas == [{}, {"page": "2"}]  # type: ignore[attr-defined]


def test_cada_categoria_vira_uma_requisicao_com_o_filtro():
    ml = fonte(lambda p: [card("MLB1", "Short Nike", 79.0, 129.0)])

    ml.fetch(pages=1, categories=[("MLB23262", 2), ("MLB1430", 1)])

    assert ml.pedidas == [  # type: ignore[attr-defined]
        {},
        {"category": "MLB23262"},
        {"category": "MLB23262", "page": "2"},
        {"category": "MLB1430"},
    ]


def test_as_ofertas_da_categoria_entram_no_resultado():
    def por_url(params):
        if params.get("category") == "MLB23262":
            return [card("MLB_NIKE", "Short Nike Feminino Dri-Fit", 79.0, 129.0)]
        return [card("MLB_FONE", "Fone Bluetooth", 99.0, 149.0)]

    offers = fonte(por_url).fetch(pages=1, categories=[("MLB23262", 1)])

    assert [o.external_id for o in offers] == ["MLB_FONE", "MLB_NIKE"]
    assert offers[1].title == "Short Nike Feminino Dri-Fit"
    assert (offers[1].price, offers[1].original_price) == (79.0, 129.0)


def test_categoria_vazia_para_de_paginar():
    """Pagina sem card nao tem proxima. Pedir mais e gastar requisicao a toa."""

    def por_url(params):
        return [] if params.get("category") else [card("MLB1", "Fone", 99.0, 149.0)]

    ml = fonte(por_url)
    ml.fetch(pages=1, categories=[("MLB23262", 5)])

    pedidas = ml.pedidas  # type: ignore[attr-defined]
    assert pedidas == [{}, {"category": "MLB23262"}]


def test_layout_mudado_falha_alto_tambem_na_categoria():
    """Silencio aqui vira grupo mudo sem ninguem entender por que."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>sem o script de contexto</html>")

    ml = MLOfertas()
    ml._client = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(ParseFailed):
        ml.fetch(pages=1, categories=[("MLB23262", 1)])


# ---------- a ligacao com o pipeline ----------


class VitrineAntiga:
    """Fonte sem o parametro novo. Nao pode quebrar."""

    name = "ml_ofertas"

    def fetch(self, pages: int = 1):
        from promo.models import Offer

        return [
            Offer(
                source="ml_ofertas",
                external_id="MLB1",
                title="Fone",
                price=99.0,
                url="https://mercadolivre.com.br/p/MLB1",
            )
        ]


class VitrineQueExplode:
    name = "ml_ofertas"

    def fetch(self, pages: int = 1, categories=None):
        raise TypeError("defeito de verdade dentro do fetch")


def test_fonte_sem_o_parametro_continua_funcionando():
    assert len(collect_vitrine(VitrineAntiga(), [("MLB23262", 1)])) == 1


def test_typeerror_de_dentro_do_fetch_nao_vira_coleta_pela_metade(caplog):
    """`except TypeError` engoliria isso e chamaria de novo sem categoria,
    escondendo o defeito atras de uma coleta incompleta."""
    import logging

    with caplog.at_level(logging.WARNING):
        assert collect_vitrine(VitrineQueExplode(), [("MLB23262", 1)]) == []

    assert "defeito de verdade" in caplog.text


def test_fonte_sem_fetch_e_ignorada():
    class SoCatalogo:
        name = "mercadolivre"

    assert collect_vitrine(SoCatalogo(), [("MLB23262", 1)]) == []


# ---------- o watchlist.json de verdade ----------


def test_le_as_categorias_de_vitrine_do_arquivo(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(
        json.dumps(
            {
                "keywords": [],
                "vitrine_categories": [
                    {"id": "MLB1430", "name": "Moda", "pages": 2},
                    {"id": "MLB23262"},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert load_vitrine_categories(caminho) == [("MLB1430", 2), ("MLB23262", 1)]


def test_campo_ausente_e_o_normal(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(json.dumps({"keywords": []}), encoding="utf-8")

    assert load_vitrine_categories(caminho) == []


def test_entrada_sem_id_e_ignorada(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(
        json.dumps({"keywords": [], "vitrine_categories": [{"name": "sem id"}]}),
        encoding="utf-8",
    )

    assert load_vitrine_categories(caminho) == []


def test_as_categorias_onde_a_marca_aparece_estao_configuradas():
    """Contado nos TITULOS da vitrine em 01/09/2026, nao no HTML cru:

        MLB23262  Calcados             adidas 19, puma 3
        MLB188064 Bermudas e Shorts    adidas  9, puma 1
        MLB31447  Camisetas e Regatas  adidas  8, puma 1, new balance 2
        MLB270215 Moda Fitness         adidas  7, puma 6
        MLB455528 Agasalhos            adidas  4, puma 2
        MLB107292 Camisas              hering  3

    Cada uma e a unica via de uma dessas marcas ate o grupo: elas nao existem
    no catalogo, entao tirar a categoria daqui apaga a marca.

    MLB1457 (Malas e Bolsas) tinha adidas 11 e puma 2, e saiu mesmo assim em
    01/09/2026: o grupo estava recebendo bolsa demais, e onze adidas nao
    pagavam o custo de uma pagina inteira de mochila.
    """
    faltando = {
        "MLB23262",
        "MLB188064",
        "MLB31447",
        "MLB270215",
        "MLB455528",
        "MLB107292",
    } - {cid for cid, _ in load_vitrine_categories()}

    assert faltando == set()


def test_o_custo_por_rodada_continua_baixo():
    """Cada pagina e UMA requisicao com ~45 produtos prontos, contra uma
    chamada POR PRODUTO no catalogo. Barato e o motivo de rodar toda rodada --
    se virar dezenas de requisicoes, deixa de ser."""
    assert sum(paginas for _, paginas in load_vitrine_categories()) <= 20

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
    se virar dezenas de requisicoes, deixa de ser.

    O teto subiu de 20 para 30 em 17/09/2026, com as oito categorias que o dono
    mandou. Agora ele tem numero medido em vez de palpite: a pagina leva 0,6s
    (tres medicoes ao vivo), entao 25 paginas custam 14s de uma rodada que leva
    de 1550s a 2679s -- menos de 1%. O que segura a rodada e o Gemini, a ~40s
    por oferta.
    """
    assert sum(paginas for _, paginas in load_vitrine_categories()) <= 30


def test_as_categorias_que_o_dono_mandou_estao_na_vitrine():
    """Invariante de configuracao. Em 17/09/2026 o dono mandou oito paginas do
    ML ("procure sempre nelas") e cada uma virou o ID da categoria equivalente.

    As oito foram pedidas ao vivo antes de entrar no arquivo: todas
    responderam, e o primeiro item de cada era o produto certo -- gloss da
    Franciny Ehlke em Maquiagem, kit Wella em Cuidados com o Cabelo, Creatina
    Growth em Suplementos.
    """
    from promo.pipeline import load_vitrine_categories

    ids = {c[0] for c in load_vitrine_categories()}

    for esperado in (
        "MLB6284",  # Perfumes
        "MLB1248",  # Maquiagem
        "MLB199407",  # Cuidados com a Pele
        "MLB1263",  # Cuidados com o Cabelo
        "MLB1339",  # Moda Fitness (Esportes)
        "MLB123103",  # Monitores Esportivos
        "MLB438178",  # Suplementos e Shakers
        "MLB3900",  # Tenis
    ):
        assert esperado in ids, f"{esperado} sumiu da vitrine"


# ---------- as categorias de esporte viram prioridade dentro do grupo ----------


def test_as_categorias_de_esporte_apontam_para_o_grupo_de_esportes():
    """As dez paginas que levam esporte e marca ao grupo, mapeadas em 27/09/2026.

    O dono mandou dezesseis URLs de `lista.mercadolivre.com.br` -- conjunto
    masculino e feminino da Nike, treino da Nike, loja da adidas por genero,
    camisetas/regatas e shorts/bermudas da adidas, tenis de corrida, termicos e
    acessorios de esporte -- dizendo "la sao os melhores itens". Aquele host
    devolve 302 para /gz/account-verification em toda requisicao de servidor,
    entao a pagina nao da para ler.

    Estas categorias sao a mesma prateleira pela via que responde, e a contagem
    de 01/09/2026 mostra que e onde a marca esta: Calcados 19 adidas, Bermudas e
    Shorts 9, Camisetas e Regatas 8, Moda Fitness 7 adidas e 6 puma, Agasalhos 4.

    O `grupos` nao muda o que o grupo ACEITA -- isso continua sendo o titulo. Ele
    muda a ordem DENTRO do grupo, pela reserva de prioridade, que e o mesmo
    mecanismo que o Beleza Premium ganhou em Mulheres e Perfumes em 18/09/2026.
    """
    from promo.pipeline import origens_por_grupo

    mapa = origens_por_grupo()
    for esperado in (
        "MLB3900",  # Tenis
        "MLB1339",  # Moda Fitness
        "MLB270215",  # Moda Fitness (a outra)
        "MLB438178",  # Suplementos e Shakers
        "MLB123103",  # Monitores Esportivos
        "MLB1276",  # Esportes e Fitness
        "MLB23262",  # Calcados -- 19 adidas
        "MLB188064",  # Bermudas e Shorts -- 9 adidas
        "MLB31447",  # Camisetas e Regatas -- 8 adidas
        "MLB455528",  # Agasalhos -- 4 adidas
    ):
        assert "Esportes" in mapa.get(
            esperado, ()
        ), f"{esperado} nao aponta para Esportes"


@pytest.mark.parametrize(
    "titulo, origem",
    [
        ("Tenis Nike Revolution 7 Masculino Corrida", "MLB3900"),
        ("Camiseta Adidas Essentials Masculina Preta", "MLB31447"),
        ("Short Adidas Treino Masculino", "MLB188064"),
        ("Agasalho Nike Sportswear Masculino", "MLB455528"),
        ("Creatina Growth 300g Monohidratada", "MLB438178"),
    ],
)
def test_o_que_vem_das_paginas_de_esporte_entra_e_passa_na_frente(titulo, origem):
    from promo.models import Offer
    from promo.pipeline import load_grupos, prioritaria_no_grupo

    grupo = next(g for g in load_grupos() if g.nome.endswith("Esportes"))
    oferta = Offer(
        source="mercadolivre",
        external_id="MLB1",
        title=titulo,
        price=100.0,
        url="https://produto.mercadolivre.com.br/MLB-1",
        vitrine_categoria=origem,
    )

    assert grupo.aceita(oferta), titulo
    assert prioritaria_no_grupo(oferta, grupo), titulo


@pytest.mark.parametrize(
    "titulo",
    [
        "Camisa Polo Reserva Friso Branca",
        "Sapato Social Masculino Couro Legitimo",
        "Sandalia Feminina Rasteira Verao",
    ],
)
def test_a_origem_nao_abre_a_porta_do_grupo(titulo):
    """Calcados traz adidas, mas traz sapato social junto.

    A prioridade por origem nao e um passe de entrada: `_escolhe_para_o_grupo`
    monta as candidatas com `grupo.aceita` ANTES de ordenar, entao o sapato
    social nunca chega a ser ordenado. O titulo continua sendo a porta -- e
    depois da poda de 27/09/2026 ele precisa citar esporte, nao roupa.
    """
    from promo.models import Offer
    from promo.pipeline import load_grupos

    grupo = next(g for g in load_grupos() if g.nome.endswith("Esportes"))
    oferta = Offer(
        source="mercadolivre",
        external_id="MLB1",
        title=titulo,
        price=100.0,
        url="https://produto.mercadolivre.com.br/MLB-1",
        vitrine_categoria="MLB23262",
    )

    assert not grupo.aceita(oferta), titulo

"""As landing pages do ML que o dono mandou em 18/09/2026.

Ele mandou dez URLs e pediu que a coleta lesse elas, "visto que fazemos
webscraping de ofertas". Oito nao dao: todo `lista.mercadolivre.com.br`
responde 200 mas redireciona para `/gz/account-verification` quando quem pede e
servidor. Passar disso seria burlar deteccao de robo, entao a resposta e nao.

As seis que responderam estao no `landing_pages` do watchlist e trazem ~280
produtos por SEIS requisicoes -- a mesma economia que faz a vitrine valer a
pena, contra uma chamada de busca MAIS uma por produto no caminho por termo.
"""

from __future__ import annotations

import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer  # noqa: E402
from promo.pipeline import (  # noqa: E402
    collect_landings,
    e_prioritaria,
    load_landing_pages,
)
from promo.sources.ml_ofertas import (  # noqa: E402
    ParseFailed,
    _cards_com_preco,
    titulo_do_slug,
)


# ---------- configuracao ----------


def test_as_seis_paginas_que_respondem_estao_configuradas():
    caminhos = {p["path"] for p in load_landing_pages()}

    assert caminhos == {
        "/e/beleza-premium",
        "/c/beleza-e-cuidado-pessoal",
        "/c/esportes-e-fitness",
        "/c/calcados-roupas-e-bolsas",
        "/c/eletrodomesticos",
        "/c/casa-moveis-e-decoracao",
    }


def test_nenhuma_pagina_de_lista_entrou():
    """`lista.mercadolivre.com.br` redireciona servidor para verificacao de
    conta. Configurar uma delas aqui seria coleta vazia todo round -- ou pior,
    convite a burlar a verificacao."""
    for pagina in load_landing_pages():
        assert pagina["path"].startswith("/"), pagina
        assert "lista." not in pagina["path"]


def test_as_duas_paginas_de_beleza_sao_prioritarias():
    """ "principalmente os perfumes e as coisas para mulheres"."""
    prioritarias = {p["path"] for p in load_landing_pages() if p["prioridade"]}

    assert prioritarias == {"/e/beleza-premium", "/c/beleza-e-cuidado-pessoal"}


def test_a_pagina_prioritaria_torna_a_oferta_prioritaria():
    """O Idole Lancome nao cita marca nenhuma que esteja na lista global? Cita.
    Este caso usa um titulo que NAO cita, para provar que quem decide e a
    pagina."""
    generico = Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title="Serum Facial Coreano 30ml Todo Tipo De Pele",
        price=99.0,
        url="https://www.mercadolivre.com.br/p/MLB1",
        vitrine_categoria="/e/beleza-premium",
    )

    assert e_prioritaria(generico, [])


def test_a_pagina_nao_prioritaria_nao_torna():
    comum = Offer(
        source="ml_ofertas",
        external_id="MLB2",
        title="Guarda-roupa Casal 6 Portas",
        price=699.0,
        url="https://www.mercadolivre.com.br/p/MLB2",
        vitrine_categoria="/c/casa-moveis-e-decoracao",
    )

    assert not e_prioritaria(comum, [])


# ---------- o titulo que sai do slug ----------


def test_o_slug_vira_titulo_legivel():
    """A rota B nao traz titulo: a pagina so linka o produto. O slug e
    descritivo e nao custa uma segunda chamada.

    O titulo nao e enfeite -- e nele que tema, prioridade e exclusao casam.
    Produto sem titulo nao entra em grupo nenhum.
    """
    slug = "idole-lancome-perfume-feminino-eau-de-parfum-100ml"

    assert titulo_do_slug(slug) == "Idole Lancome Perfume Feminino Eau De Parfum 100ml"


def test_o_titulo_do_slug_casa_tema():
    from promo.pipeline import tem_tema

    titulo = titulo_do_slug("gel-kerastase-curl-manifesto-para-cabelos-cacheados")

    assert tem_tema(titulo, ["kerastase"])


# ---------- o parser de card ----------


def card(preco: float | None) -> dict:
    componentes = [{"type": "title", "title": {"text": "Produto"}}]
    if preco is not None:
        componentes.append(
            {"type": "price", "price": {"current_price": {"value": preco}}}
        )
    return {"metadata": {"id": "MLB9", "url": "x"}, "components": componentes}


def test_acha_card_com_preco_em_qualquer_profundidade():
    """O caminho muda de pagina para pagina: numa os cards moram sob
    `components[].items`, noutra sob `containers[]`. Fixar o caminho daria
    coleta vazia e silenciosa a cada remodelagem do ML."""
    payload = {"a": {"b": [{"c": {"items": [card(10.0)]}}]}}

    assert len(_cards_com_preco(payload)) == 1


def test_ignora_card_sem_preco():
    assert _cards_com_preco({"x": [card(None)]}) == []


def test_nao_entra_em_recursao_infinita():
    fundo = {}
    no = fundo
    for _ in range(40):
        no["dentro"] = {}
        no = no["dentro"]

    assert _cards_com_preco(fundo) == []


# ---------- a coleta ----------


class VitrineFalsa:
    def __init__(self, ofertas=None, ids=None, erro=None):
        self._ofertas = ofertas or []
        self._ids = ids or []
        self._erro = erro
        self.pedidas = []

    def landing_com_preco(self, caminho):
        self.pedidas.append(caminho)
        if self._erro:
            raise self._erro
        from dataclasses import replace

        return [replace(o, vitrine_categoria=caminho) for o in self._ofertas]

    def landing_catalogo(self, caminho):
        self.pedidas.append(caminho)
        if self._erro:
            raise self._erro
        return self._ids


class CatalogoFalso:
    def __init__(self, ofertas=None):
        self._ofertas = ofertas or []
        self.pedidos = []

    def fetch_by_ids(self, tracked):
        self.pedidos.append(tracked)
        return self._ofertas


def oferta(titulo="Produto", preco=10.0):
    return Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title=titulo,
        price=preco,
        url="https://www.mercadolivre.com.br/p/MLB1",
    )


def test_a_rota_a_carimba_a_pagina_de_origem(monkeypatch):
    from promo import pipeline

    monkeypatch.setattr(
        pipeline,
        "load_landing_pages",
        lambda: [{"path": "/c/x", "rota": "A", "prioridade": False}],
    )
    vitrine = VitrineFalsa(ofertas=[oferta()])

    saida = collect_landings(vitrine, None)

    assert [o.vitrine_categoria for o in saida] == ["/c/x"]


def test_a_rota_b_precifica_pelo_catalogo(monkeypatch):
    from promo import pipeline

    monkeypatch.setattr(
        pipeline,
        "load_landing_pages",
        lambda: [{"path": "/e/y", "rota": "B", "prioridade": True}],
    )
    vitrine = VitrineFalsa(ids=[("MLB7", "Perfume Tal")])
    catalogo = CatalogoFalso(ofertas=[oferta("Perfume Tal", 50.0)])

    saida = collect_landings(vitrine, catalogo)

    assert catalogo.pedidos == [[("MLB7", "Perfume Tal", None)]]
    assert [o.vitrine_categoria for o in saida] == ["/e/y"]


def test_a_rota_b_sem_catalogo_e_pulada(monkeypatch):
    """Sem quem precifique, a rota B devolveria oferta sem preco -- pior que
    nao coletar."""
    from promo import pipeline

    monkeypatch.setattr(
        pipeline,
        "load_landing_pages",
        lambda: [{"path": "/e/y", "rota": "B", "prioridade": True}],
    )

    assert collect_landings(VitrineFalsa(ids=[("MLB7", "X")]), None) == []


def test_uma_pagina_quebrada_nao_derruba_as_outras(monkeypatch):
    """Landing e HTML, e HTML muda."""
    from promo import pipeline

    monkeypatch.setattr(
        pipeline,
        "load_landing_pages",
        lambda: [
            {"path": "/c/quebrada", "rota": "A", "prioridade": False},
            {"path": "/c/boa", "rota": "A", "prioridade": False},
        ],
    )

    class MeioQuebrada(VitrineFalsa):
        def landing_com_preco(self, caminho):
            if caminho == "/c/quebrada":
                raise ParseFailed("layout mudou")
            return [oferta()]

    saida = collect_landings(MeioQuebrada(), None)

    assert len(saida) == 1


def test_fonte_sem_landing_devolve_vazio():
    """A Amazon nao tem landing do ML."""

    class SemLanding:
        name = "amazon"

    assert collect_landings(SemLanding(), None) == []


def test_sem_paginas_configuradas_nao_faz_requisicao(monkeypatch):
    from promo import pipeline

    monkeypatch.setattr(pipeline, "load_landing_pages", lambda: [])
    vitrine = VitrineFalsa(ofertas=[oferta()])

    assert collect_landings(vitrine, None) == []
    assert vitrine.pedidas == []


# ---------- a origem como prioridade DENTRO do grupo, 18/09/2026 ----------


def _grupo(pedaco: str):
    from promo.pipeline import load_grupos

    return [g for g in load_grupos() if g.nome.endswith(pedaco)][0]


def de(origem: str, titulo: str = "Produto Qualquer 100ml") -> Offer:
    return Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title=titulo,
        price=99.0,
        url="https://www.mercadolivre.com.br/p/MLB1",
        vitrine_categoria=origem,
    )


def test_a_maquiagem_da_vitrine_e_prioridade_em_mulheres():
    """ "principalmente a parte de maquiagem" / "maquiagem e no grupo das
    mulheres" -- 18/09/2026.

    Medido no mesmo dia: das quatro categorias de beleza, so a de Maquiagem
    traz maquiagem -- 35 de 48 itens, 43 com preco riscado. Perfumes, Pele e
    Cabelo trazem ZERO.
    """
    from promo.pipeline import prioritaria_no_grupo

    # Titulo que nao cita alvo nenhum da lista do grupo: quem decide e a origem.
    base = de("MLB1248", "Kit Extensao De Cilios B&Qaugen Fairy De Mink")

    assert prioritaria_no_grupo(base, _grupo("Mulheres"))


def test_a_maquiagem_nao_e_prioridade_no_perfumes():
    """O dono separou: maquiagem e do grupo das mulheres."""
    from promo.pipeline import prioritaria_no_grupo

    assert not prioritaria_no_grupo(de("MLB1248"), _grupo("Perfumes"))


def test_o_beleza_premium_e_prioridade_nos_dois_grupos():
    """ "esses produtos que nos pegamos de beleza premium, sejam prioridade nos
    grupos de mulher e perfume"."""
    from promo.pipeline import prioritaria_no_grupo

    oferta = de("/e/beleza-premium", "Serum Coreano 30ml")

    assert prioritaria_no_grupo(oferta, _grupo("Mulheres"))
    assert prioritaria_no_grupo(oferta, _grupo("Perfumes"))


def test_a_origem_de_beleza_nao_prioriza_o_casa():
    from promo.pipeline import prioritaria_no_grupo

    assert not prioritaria_no_grupo(de("/e/beleza-premium"), _grupo("Casa"))


def test_o_titulo_continua_valendo_junto_da_origem():
    """ "quero que o grupo mulheres e perfumes a prioridade seja pelo titulo E
    pelas melhores opcoes no Beleza Premium" -- as duas vias, nao uma."""
    from promo.pipeline import prioritaria_no_grupo

    por_titulo = Offer(
        source="ml_ofertas",
        external_id="MLB2",
        title="Batom Matte Maybelline Superstay",
        price=40.0,
        url="https://www.mercadolivre.com.br/p/MLB2",
    )

    assert prioritaria_no_grupo(por_titulo, _grupo("Mulheres"))


def test_as_origens_declaram_grupo_no_watchlist():
    """Invariante de configuracao."""
    from promo.pipeline import origens_por_grupo

    mapa = origens_por_grupo()

    assert mapa["MLB1248"] == ["Mulheres"]
    assert set(mapa["MLB6284"]) == {"Mulheres", "Perfumes"}
    assert set(mapa["/e/beleza-premium"]) == {"Mulheres", "Perfumes"}


def test_a_cota_maior_dos_dois_grupos():
    """ "nao tem problema em subir a quantidade de envios nos grupos, pode subir
    caso seja necessario, mas preciso que esses produtos sejam enviados"."""
    from promo.config import max_por_grupo_tematico

    padrao = max_por_grupo_tematico()

    assert _grupo("Mulheres").max_por_rodada > padrao
    assert _grupo("Perfumes").max_por_rodada > padrao
    assert _grupo("Casa").max_por_rodada == 0  # usa o padrao


def test_grupo_sem_cota_propria_usa_a_geral():
    from promo.pipeline import GrupoDestino

    grupo = GrupoDestino(jid="1@g.us", nome="X", temas=("y",))

    assert grupo.max_por_rodada == 0

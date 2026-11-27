"""Quando o Mercado Livre recusa, o bot para de insistir.

Medido em producao em 27/11/2026: `/products/{id}/items` respondia 403
(PolicyAgent) em todas as chamadas desde 26/11 15:20, de 2 a 3 mil por hora, e
as paginas publicas mandavam o servidor para a verificacao de conta. Insistir
nao traz preco nenhum e enterra o log.
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

from promo import pipeline
from promo.pipeline import Category, Watch, collect, collect_landings
from promo.sources import mercadolivre, ml_ofertas
from promo.sources.mercadolivre import MercadoLivre
from promo.sources.ml_bloqueio import MLSuspenso
from promo.sources.ml_ofertas import MLOfertas, ParseFailed

FOTO = "https://http2.mlstatic.com/D_NQ_NP_1-F.jpg"
PAUSA = 3600.0  # segundos, o padrao de ML_BLOQUEIO_PAUSA_MINUTOS


class Relogio:
    def __init__(self) -> None:
        self.agora = 1000.0

    def __call__(self) -> float:
        return self.agora


@pytest.fixture
def relogio(monkeypatch) -> Relogio:
    falso = Relogio()
    monkeypatch.setattr(mercadolivre.PRECOS, "relogio", falso)
    monkeypatch.setattr(ml_ofertas.PAGINAS, "relogio", falso)
    return falso


@pytest.fixture(autouse=True)
def limite_curto(monkeypatch):
    monkeypatch.setenv("ML_BLOQUEIO_LIMITE", "3")
    monkeypatch.delenv("ML_BLOQUEIO_PAUSA_MINUTOS", raising=False)


# ---------- preco de catalogo ----------


class Catalogo:
    """A API do ML de mentira. `bloqueado` liga o 403 no preco."""

    def __init__(self) -> None:
        self.bloqueado = True
        self.recusados: set[str] = set()
        self.status_forcado: int | None = None
        self.pedidas: list[str] = []

    def precos_pedidos(self) -> list[str]:
        return [p.split("/")[2] for p in self.pedidas if p.endswith("/items")]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        self.pedidas.append(caminho)
        if caminho.endswith("/items"):
            product_id = caminho.split("/")[2]
            if self.status_forcado:
                return httpx.Response(self.status_forcado, json={})
            if self.bloqueado or product_id in self.recusados:
                return httpx.Response(403, json={"code": "PA_UNAUTHORIZED"})
            return httpx.Response(
                200, json={"results": [{"price": 10.0, "condition": "new"}]}
            )
        if caminho == "/products/search":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": f"MLB{i}", "name": f"P{i}", "pictures": [{"url": FOTO}]}
                        for i in range(10)
                    ]
                },
            )
        if "/highlights/" in caminho:
            return httpx.Response(
                200,
                json={
                    "content": [{"id": f"MLB{i}", "type": "PRODUCT"} for i in range(10)]
                },
            )
        return httpx.Response(200, json={"name": "P", "pictures": [{"url": FOTO}]})


def fonte(api: Catalogo) -> MercadoLivre:
    class Cfg:
        client_id = "1"
        client_secret = "s"
        redirect_uri = "https://exemplo.com/cb"
        site_id = "MLB"
        affiliate_cookie = ""
        affiliate_tag = ""

    ml = MercadoLivre(Cfg())
    ml._client = httpx.Client(transport=httpx.MockTransport(api))
    ml.access_token = lambda: "tok"  # type: ignore[method-assign]
    return ml


def alvos(*ids: int) -> list[tuple[str, str, str]]:
    return [(f"MLB{i}", f"P{i}", FOTO) for i in ids]


def erros(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.ERROR]


def avisos(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING]


def test_recusas_seguidas_suspendem_as_chamadas(relogio):
    api = Catalogo()
    ml = fonte(api)

    assert ml.fetch_by_ids(alvos(*range(50))) == []
    assert ml.fetch_by_ids(alvos(*range(50, 90))) == []

    assert api.precos_pedidos() == ["MLB0", "MLB1", "MLB2"]


def test_um_unico_erro_e_nenhum_aviso_por_rodada(relogio, caplog):
    ml = fonte(Catalogo())

    with caplog.at_level(logging.INFO, logger="promo"):
        for _ in range(5):
            ml.fetch_by_ids(alvos(*range(20)))
        relogio.agora += PAUSA
        ml.fetch_by_ids(alvos(*range(20, 40)))

    assert len(erros(caplog)) == 1
    assert "403" in erros(caplog)[0].getMessage()
    assert avisos(caplog) == []


def test_resposta_aceita_zera_a_contagem(relogio):
    """Anuncio de vendedor responde 403 com a API saudavel: so a sequencia conta."""
    api = Catalogo()
    api.bloqueado = False
    api.recusados = {"MLB0", "MLB1", "MLB3", "MLB4"}

    achadas = fonte(api).fetch_by_ids(alvos(*range(7)))

    assert [o.external_id for o in achadas] == ["MLB2", "MLB5", "MLB6"]


def test_sequencia_de_anuncios_nao_suspende_quando_a_api_esta_saudavel(relogio, caplog):
    """`hot_products` poe na frente quem nunca atualiza, e quem nunca atualiza e
    o anuncio de vendedor: uma fila inteira de 403 com a API respondendo."""
    api = Catalogo()
    api.bloqueado = False
    api.recusados = {f"MLB{i}" for i in range(1, 13)}

    achadas = fonte(api).fetch_by_ids(alvos(0, *range(1, 13), 13))

    assert [o.external_id for o in achadas] == ["MLB0", "MLB13"]
    assert erros(caplog) == []


def test_bloqueio_que_comeca_depois_de_uma_resposta_suspende(relogio):
    api = Catalogo()
    api.bloqueado = False
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0))
    api.bloqueado = True
    api.pedidas.clear()

    ml.fetch_by_ids(alvos(*range(1, 30)))

    # As tres recusas e a conferencia com o ultimo que tinha respondido.
    assert api.precos_pedidos() == ["MLB1", "MLB2", "MLB3", "MLB0"]


def test_depois_da_pausa_vai_uma_sonda_so(relogio):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))
    api.pedidas.clear()

    relogio.agora += PAUSA - 1
    ml.fetch_by_ids(alvos(3, 4, 5))
    assert api.precos_pedidos() == []

    relogio.agora += 1
    ml.fetch_by_ids(alvos(3, 4, 5))
    ml.fetch_by_ids(alvos(6, 7, 8))
    assert api.precos_pedidos() == ["MLB3"]


def test_sonda_aceita_retoma_as_chamadas(relogio, caplog):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))
    api.bloqueado = False
    relogio.agora += PAUSA

    with caplog.at_level(logging.INFO, logger="promo"):
        achadas = ml.fetch_by_ids(alvos(3, 4, 5))

    assert [o.external_id for o in achadas] == ["MLB3", "MLB4", "MLB5"]
    assert any("voltou" in r.getMessage() for r in caplog.records)


def test_a_sonda_troca_de_produto_entre_uma_pausa_e_outra(relogio):
    """O primeiro da fila pode ser um ID que responde 403 sempre. Se ele fosse
    toda vez a sonda, a fonte nunca voltaria."""
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))

    relogio.agora += PAUSA
    ml.fetch_by_ids(alvos(3, 4, 5))  # sonda MLB3, recusada
    api.bloqueado = False
    api.recusados = {"MLB3"}
    relogio.agora += PAUSA
    achadas = ml.fetch_by_ids(alvos(3, 4, 5))

    assert [o.external_id for o in achadas] == ["MLB4", "MLB5"]


def test_com_um_produto_so_ele_volta_a_ser_sonda(relogio):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))
    relogio.agora += PAUSA
    ml.fetch_by_ids(alvos(3))
    api.bloqueado = False

    relogio.agora += 2 * PAUSA

    assert len(ml.fetch_by_ids(alvos(3))) == 1


def test_sonda_com_erro_do_servidor_nao_retoma(relogio):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))
    api.status_forcado = 500
    api.pedidas.clear()
    relogio.agora += PAUSA

    ml.fetch_by_ids(alvos(3, 4, 5))

    assert api.precos_pedidos() == ["MLB3"]


def test_a_suspensao_vale_para_a_proxima_instancia(relogio):
    """`build_sources` cria uma fonte nova a cada rodada."""
    api = Catalogo()
    fonte(api).fetch_by_ids(alvos(0, 1, 2))
    api.pedidas.clear()

    fonte(api).fetch_by_ids(alvos(3, 4, 5))

    assert api.pedidas == []


def test_pausa_e_configuravel(relogio, monkeypatch):
    monkeypatch.setenv("ML_BLOQUEIO_PAUSA_MINUTOS", "5")
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))
    api.pedidas.clear()
    relogio.agora += 300

    ml.fetch_by_ids(alvos(3))

    assert api.precos_pedidos() == ["MLB3"]


def test_limite_zero_desliga(relogio, monkeypatch):
    monkeypatch.setenv("ML_BLOQUEIO_LIMITE", "0")
    api = Catalogo()

    fonte(api).fetch_by_ids(alvos(*range(30)))

    assert len(api.precos_pedidos()) == 30


@pytest.mark.parametrize("metodo", ["search", "highlights"])
def test_descoberta_suspensa_nao_toca_na_api(relogio, metodo):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))
    api.pedidas.clear()

    with pytest.raises(MLSuspenso):
        getattr(ml, metodo)("qualquer")

    assert api.pedidas == []


@pytest.mark.parametrize("metodo", ["search", "highlights"])
def test_descoberta_manda_a_sonda_e_para(relogio, metodo):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(90, 91, 92))
    api.pedidas.clear()
    relogio.agora += PAUSA

    with pytest.raises(MLSuspenso):
        getattr(ml, metodo)("qualquer")

    assert api.precos_pedidos() == ["MLB0"]
    assert len(api.pedidas) <= 3  # lista, nome do produto e a sonda


@pytest.mark.parametrize("metodo", ["search", "highlights"])
def test_descoberta_retoma_quando_a_sonda_passa(relogio, metodo):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(90, 91, 92))
    api.bloqueado = False
    relogio.agora += PAUSA

    assert len(getattr(ml, metodo)("qualquer")) == 10


def test_a_rodada_suspensa_nao_gera_aviso(relogio, caplog, banco_em_memoria):
    api = Catalogo()
    ml = fonte(api)
    ml.fetch_by_ids(alvos(0, 1, 2))
    api.pedidas.clear()
    termos = [Watch(term=f"termo {i}") for i in range(20)]
    categorias = [Category(id=f"MLB{i}") for i in range(20)]

    with caplog.at_level(logging.INFO, logger="promo"):
        collect([ml], termos, categories=categorias)

    assert api.pedidas == []
    assert avisos(caplog) == []
    assert pipeline._time_to_discover()  # nao carimbou descoberta que nao houve


# ---------- paginas publicas ----------


def _html() -> str:
    payload = {"appProps": {"pageProps": {"data": {"items": []}}}}
    return (
        '<script id="__NORDIC_RENDERING_CTX__" type="application/json">'
        f"window.__CTX__ = {json.dumps(payload)};</script>"
    )


class Site:
    def __init__(self) -> None:
        self.bloqueado = True
        self.pedidas: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if "account-verification" in request.url.path:
            return httpx.Response(200, text="<html>verifique sua conta</html>")
        self.pedidas.append(request.url.path)
        if self.bloqueado:
            destino = "https://www.mercadolivre.com.br/gz/account-verification"
            return httpx.Response(302, headers={"location": destino})
        return httpx.Response(200, text=_html())


def vitrine(site: Site) -> MLOfertas:
    ml = MLOfertas()
    ml._client = httpx.Client(
        transport=httpx.MockTransport(site), follow_redirects=True
    )
    return ml


def bloquear_paginas(ml: MLOfertas) -> None:
    for caminho in ("/c/a", "/c/b"):
        with pytest.raises(ParseFailed, match="verificacao"):
            ml.landing_com_preco(caminho)
    with pytest.raises(MLSuspenso):
        ml.landing_catalogo("/e/c")


def test_redirecionamentos_seguidos_suspendem_vitrine_e_landings(relogio, caplog):
    site = Site()
    ml = vitrine(site)

    with caplog.at_level(logging.INFO, logger="promo"):
        bloquear_paginas(ml)
        site.pedidas.clear()
        for chamada in (
            lambda: ml.fetch(pages=2),
            lambda: ml.landing_com_preco("/c/d"),
            lambda: ml.landing_catalogo("/e/f"),
        ):
            with pytest.raises(MLSuspenso):
                chamada()

    assert site.pedidas == []
    assert len(erros(caplog)) == 1
    assert "account-verification" in erros(caplog)[0].getMessage()


def test_a_vitrine_sozinha_tambem_dispara(relogio):
    ml = vitrine(Site())

    for _ in range(2):
        with pytest.raises(ParseFailed, match="verificacao"):
            ml.fetch(pages=1)
    with pytest.raises(MLSuspenso):
        ml.fetch(pages=1)


def test_pagina_que_responde_retoma_a_coleta(relogio):
    site = Site()
    ml = vitrine(site)
    bloquear_paginas(ml)
    site.bloqueado = False
    relogio.agora += PAUSA

    assert ml.landing_com_preco("/c/d") == []
    assert ml.fetch(pages=1) == []
    assert site.pedidas[-2:] == ["/c/d", "/ofertas"]


def test_paginas_suspensas_nao_geram_aviso_na_rodada(relogio, caplog, monkeypatch):
    site = Site()
    ml = vitrine(site)
    bloquear_paginas(ml)
    site.pedidas.clear()
    monkeypatch.setattr(
        pipeline,
        "load_landing_pages",
        lambda: [{"path": "/c/x", "rota": "A"}, {"path": "/c/y", "rota": "A"}],
    )

    with caplog.at_level(logging.INFO, logger="promo"):
        assert pipeline.collect_vitrine(ml) == []
        assert collect_landings(ml) == []

    assert site.pedidas == []
    assert avisos(caplog) == []


def test_preco_recusado_nao_suspende_as_paginas(relogio):
    fonte(Catalogo()).fetch_by_ids(alvos(0, 1, 2))
    site = Site()
    site.bloqueado = False

    assert vitrine(site).fetch(pages=1) == []
    assert site.pedidas == ["/ofertas"]

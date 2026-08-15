"""Geracao automatica do link de afiliado pelo painel do ML.

Isto depende de pagina interna e cookie de sessao -- vai quebrar. O que os
testes protegem nao e o caminho feliz, e o que acontece quando quebra: sessao
expirada tem que ser distinguivel de erro de rede, e nenhuma falha aqui pode
derrubar a rodada nem gerar post sem comissao.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import MercadoLivreConfig  # noqa: E402
from promo.sources.ml_linkbuilder import (  # noqa: E402
    CSRF_META,
    ENDPOINT,
    LinkBuilder,
    PAINEL,
    SessionExpired,
)
from promo.sources.mercadolivre import MercadoLivre  # noqa: E402
from promo.models import Offer  # noqa: E402

PAGINA = (
    '<html><head><meta name="browser-support" content="samesite=true"/>'
    '<meta name="csrf-token" content="NXj1Fbgu-OKLXUalWJhLM2O4TVmLHXO0FCIY"/>'
    "</head><body>painel</body></html>"
)
PAGINA_LOGIN = "<html><head><title>Entre na sua conta</title></head></html>"

PRODUTO = "https://www.mercadolivre.com.br/p/MLB19592785"
CURTO = "https://meli.la/2DBDd4o"


def resposta_createLink(url: str = PRODUTO, short: str = CURTO) -> dict:
    """Formato real da resposta, reduzido aos campos que o codigo le."""
    return {
        "status": 200,
        "urls": [
            {
                "id": "2DBDd4o",
                "created": True,
                "short_url": short,
                "long_url": "https://www.mercadolivre.com.br/social/x?ref=blob",
                "origin_url": url,
            }
        ],
        "total_success": 1,
    }


def build(handler) -> LinkBuilder:
    """LinkBuilder com o transporte trocado por um mock."""
    builder = LinkBuilder("ssid=fake", "tagdele")
    builder._client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"cookie": "ssid=fake"},
        follow_redirects=True,
    )
    return builder


# ---------- csrf ----------


def test_regex_acha_o_token_no_meta():
    """O token nao vem do cookie _csrf: e outro valor, so na pagina."""
    assert CSRF_META.search(PAGINA).group(1) == "NXj1Fbgu-OKLXUalWJhLM2O4TVmLHXO0FCIY"


def test_pagina_de_login_vira_sessao_expirada():
    """Deslogado o ML serve o login com 200 -- entao status nao serve de sinal,
    a ausencia do token e que serve."""
    builder = build(lambda req: httpx.Response(200, text=PAGINA_LOGIN))

    with pytest.raises(SessionExpired, match="ML_AFFILIATE_COOKIE"):
        builder.create([PRODUTO])


def test_token_e_reaproveitado_entre_chamadas():
    """A pagina tem 770 KB; baixar por link gerado seria absurdo."""
    paginas = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAINEL:
            paginas.append(1)
            return httpx.Response(200, text=PAGINA)
        return httpx.Response(200, json=resposta_createLink())

    builder = build(handler)
    builder.create([PRODUTO])
    builder.create([PRODUTO])

    assert len(paginas) == 1


def test_403_renova_o_token_e_tenta_de_novo():
    """O token vira sozinho durante rodada longa; perder a oferta por isso
    seria perder dinheiro por causa de um retry que faltou."""
    estado = {"posts": 0, "paginas": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAINEL:
            estado["paginas"] += 1
            return httpx.Response(200, text=PAGINA)
        estado["posts"] += 1
        if estado["posts"] == 1:
            return httpx.Response(403, text="invalid csrf")
        return httpx.Response(200, json=resposta_createLink())

    builder = build(handler)
    assert builder.create([PRODUTO]).links == {PRODUTO: CURTO}
    assert estado["posts"] == 2
    assert estado["paginas"] == 2  # a segunda e a renovacao forcada


# ---------- create ----------


def test_mapeia_origem_para_link_curto():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAINEL:
            return httpx.Response(200, text=PAGINA)
        return httpx.Response(200, json=resposta_createLink())

    assert build(handler).create([PRODUTO]).links == {PRODUTO: CURTO}


def test_lista_vazia_nao_chama_o_painel():
    """O pipeline chama isso mesmo quando nao ha o que gerar."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nao devia ter feito requisicao")

    resultado = build(handler).create([])
    assert resultado.links == {} and resultado.recusados == {}


def test_manda_o_referer_do_painel():
    """O endpoint recusa chamada que nao parece vir da propria pagina."""
    capturado = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAINEL:
            return httpx.Response(200, text=PAGINA)
        capturado.update(request.headers)
        return httpx.Response(200, json=resposta_createLink())

    build(handler).create([PRODUTO])
    assert capturado["referer"] == PAINEL
    assert capturado["x-csrf-token"] == "NXj1Fbgu-OKLXUalWJhLM2O4TVmLHXO0FCIY"


def test_url_sem_link_na_resposta_nao_vira_entrada():
    """Melhor faltar no dicionario do que devolver link de outro produto."""

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAINEL:
            return httpx.Response(200, text=PAGINA)
        return httpx.Response(
            200, json={"status": 200, "urls": [{"origin_url": PRODUTO}]}
        )

    assert build(handler).create([PRODUTO]).links == {}


# ---------- integracao com a fonte ----------


def offer() -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB19592785",
        title="Whey",
        price=100.0,
        url=PRODUTO,
    )


def config(cookie: str = "ssid=fake", tag: str = "tagdele") -> MercadoLivreConfig:
    return MercadoLivreConfig(
        client_id="1",
        client_secret="2",
        redirect_uri="https://example.com/callback",
        site_id="MLB",
        affiliate_cookie=cookie,
        affiliate_tag=tag,
    )


def test_sem_cookie_configurado_volta_pro_fluxo_manual(tmp_path, monkeypatch):
    """Cookie e opcional de proposito: expirado, ele nao pode levar a coleta junto."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import init_db

    init_db()

    fonte = MercadoLivre(config(cookie="", tag=""))
    assert fonte.affiliate_url(offer()) is None


def test_painel_fora_do_ar_nao_derruba_a_rodada(tmp_path, monkeypatch):
    """Sem link a oferta e segurada -- mas a rodada segue e as outras saem."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import init_db

    init_db()

    fonte = MercadoLivre(config())
    fonte._builder = build(lambda req: httpx.Response(500, text="boom"))

    assert fonte.affiliate_url(offer()) is None


def test_link_gerado_fica_salvo_no_banco(tmp_path, monkeypatch):
    """O banco nao e so cache: ele segura os links quando o cookie expira."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import affiliate_link, connect, init_db, record_offer

    init_db()
    with connect() as conn:
        record_offer(conn, offer())

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == PAINEL:
            return httpx.Response(200, text=PAGINA)
        return httpx.Response(200, json=resposta_createLink())

    fonte = MercadoLivre(config())
    fonte._builder = build(handler)

    assert fonte.affiliate_url(offer()) == CURTO
    with connect() as conn:
        assert affiliate_link(conn, "mercadolivre:MLB19592785") == CURTO


def test_link_ja_salvo_nao_chama_o_painel(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import connect, init_db, record_offer, save_affiliate_link

    init_db()
    with connect() as conn:
        record_offer(conn, offer())
        save_affiliate_link(conn, "mercadolivre:MLB19592785", CURTO)

    fonte = MercadoLivre(config())
    fonte._builder = build(
        lambda req: (_ for _ in ()).throw(AssertionError("nao devia chamar o painel"))
    )

    assert fonte.affiliate_url(offer()) == CURTO


# ---------- recusa do programa ----------
#
# O ML responde 200 mesmo recusando: o erro vem por item, em `message`, no
# lugar do short_url. Produto recusado nunca vira link -- se nao registrarmos
# isso, o pipeline pergunta de novo a cada rodada, pra sempre.

RECUSA = {
    "status": 200,
    "urls": [
        {
            "origin_url": PRODUTO,
            "message": "URL not allowed in affiliates program",
            "error_code": 111,
        }
    ],
    "total_error": 1,
}


def painel_recusando(request: httpx.Request) -> httpx.Response:
    if str(request.url) == PAINEL:
        return httpx.Response(200, text=PAGINA)
    return httpx.Response(200, json=RECUSA)


def test_recusa_vem_separada_do_sucesso():
    resultado = build(painel_recusando).create([PRODUTO])

    assert resultado.links == {}
    assert resultado.recusados == {PRODUTO: "URL not allowed in affiliates program"}


def test_recusa_fica_registrada_no_banco(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import affiliate_blocked, connect, init_db, record_offer

    init_db()
    with connect() as conn:
        record_offer(conn, offer())

    fonte = MercadoLivre(config())
    fonte._builder = build(painel_recusando)
    assert fonte.affiliate_url(offer()) is None

    with connect() as conn:
        assert affiliate_blocked(conn, "mercadolivre:MLB19592785") == (
            "URL not allowed in affiliates program"
        )


def test_produto_recusado_nao_volta_a_consultar_o_painel(tmp_path, monkeypatch):
    """O ponto todo do registro: uma consulta, nao uma por rodada pra sempre."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import connect, init_db, record_offer

    init_db()
    with connect() as conn:
        record_offer(conn, offer())

    chamadas = []

    def contando(request: httpx.Request) -> httpx.Response:
        chamadas.append(str(request.url))
        return painel_recusando(request)

    fonte = MercadoLivre(config())
    fonte._builder = build(contando)

    fonte.affiliate_url(offer())
    depois = len(chamadas)
    fonte.affiliate_url(offer())

    assert len(chamadas) == depois


def test_recusado_sai_da_lista_de_pendentes(tmp_path, monkeypatch):
    """Senao o `link-all` reenviaria o mesmo inelegivel em todo backfill."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import (
        connect,
        init_db,
        mark_affiliate_blocked,
        products_missing_link,
        record_offer,
    )

    init_db()
    with connect() as conn:
        record_offer(conn, offer())
        assert len(products_missing_link(conn)) == 1

        mark_affiliate_blocked(conn, "mercadolivre:MLB19592785", "URL not allowed")
        assert products_missing_link(conn) == []


def test_recusa_antiga_volta_a_ser_consultada(tmp_path, monkeypatch):
    """Elegibilidade muda: o vendedor entra no programa, a categoria e aceita.
    Uma recusa permanente perderia essas ofertas pra sempre."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import (
        AFFILIATE_RECHECK_DAYS,
        affiliate_blocked,
        connect,
        init_db,
        mark_affiliate_blocked,
        products_missing_link,
        record_offer,
    )

    init_db()
    with connect() as conn:
        record_offer(conn, offer())
        mark_affiliate_blocked(conn, "mercadolivre:MLB19592785", "URL not allowed")
        # Envelhece a recusa para alem da janela de reconsulta.
        conn.execute(
            "UPDATE affiliate_blocked SET checked_at = datetime('now', ?)",
            (f"-{AFFILIATE_RECHECK_DAYS + 1} days",),
        )

        assert affiliate_blocked(conn, "mercadolivre:MLB19592785") is None
        assert len(products_missing_link(conn)) == 1


def test_endpoint_e_o_do_painel():
    """Fixar a URL: se ela mudar, o teste falha antes da producao."""
    assert ENDPOINT.endswith("/affiliate-program/api/v2/affiliates/createLink")

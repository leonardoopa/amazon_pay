"""O grupo "Achadinhos Pelando Vip": o link e do Pelando, nao da loja.

Pedido do dono em 30/09/2026: repassar o que o grupo posta, com o NOSSO link,
dando prioridade a Amazon e ao Mercado Livre, como nos outros grupos.

O formato das mensagens e o das duas colagens reais abaixo. O link
`pelando.promo/XXXXX` redireciona para a pagina `/d/` da oferta, e essa pagina
guarda o endereco limpo da loja em `sourceUrl`. As fixtures de `tests/fixtures`
sao a ilha real do Astro dessas duas paginas, capturada na mesma noite, sem o
autor e sem o redirecionador de afiliado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources import pelando_grupo  # noqa: E402
from promo.sources.pelando_grupo import (  # noqa: E402
    DealPelando,
    _classifica,
    _condicoes_do_deal,
    deal_da_pagina,
    deal_do_link,
    links_do_pelando,
    ofertas_do_grupo,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# O cliente de verdade, guardado antes de qualquer teste trocar `httpx.Client`.
CLIENTE_REAL = httpx.Client

MENSAGEM_AMAZON = """Calça Feminina Lã Cintura Média Confortável Pittsburgh Penguins

💰 R$60,18
🏬 Amazon
➡️ https://pelando.promo/ajCGV

⚠️ Tudo que é bom dura pouco!"""

MENSAGEM_ML = """Smart TV LG 50'' QNED Mini LED 4K Processador A7

💰 R$2.372,00
🚚 Mercado Livre
➡️ https://pelando.promo/Jq9FA

🚨 Garanta agora antes que o preço mude!"""

PAGINA_AMAZON = (FIXTURES / "pelando_oferta_amazon.html").read_text(encoding="utf-8")
PAGINA_ML = (FIXTURES / "pelando_oferta_ml_catalogo.html").read_text(encoding="utf-8")

SLUG_AMAZON = "calca-feminina-la-cintura-media-confortavel-2700"
SLUG_ML = "smart-tv-lg-50-qned-mini-led-4k-processador-a7-99af"


@pytest.fixture(autouse=True)
def cache_limpo():
    pelando_grupo._RESOLVIDOS.clear()
    yield
    pelando_grupo._RESOLVIDOS.clear()


def registro(texto: str) -> dict:
    return {"message": {"conversation": texto}}


def pelando_falso(paginas: dict[str, str], pedidos: list[str] | None = None):
    """Transporte que responde como o Pelando: 302 no encurtador, HTML na oferta."""

    def handler(request: httpx.Request) -> httpx.Response:
        if pedidos is not None:
            pedidos.append(str(request.url))
        if request.url.host == "pelando.promo":
            slug = {"ajCGV": SLUG_AMAZON, "Jq9FA": SLUG_ML}.get(request.url.path.strip("/"))
            if slug is None:
                return httpx.Response(404)
            return httpx.Response(
                302,
                headers={
                    "location": f"https://www.pelando.com.br/d/{slug}"
                    "?utm_source=social&utm_medium=whatsapp&aff_id=pwpp"
                },
            )
        slug = request.url.path.removeprefix("/d/")
        if slug in paginas:
            return httpx.Response(200, text=paginas[slug])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def instala(monkeypatch, paginas: dict[str, str], pedidos: list[str] | None = None) -> None:
    """O `httpx.Client` do modulo passa a falar com o Pelando de mentira."""
    transporte = pelando_falso(paginas, pedidos)
    monkeypatch.setattr(
        pelando_grupo.httpx, "Client", lambda **kw: CLIENTE_REAL(transport=transporte, **kw)
    )
    monkeypatch.setattr(pelando_grupo.time, "sleep", lambda _s: None)


@pytest.fixture
def pelando(monkeypatch):
    pedidos: list[str] = []
    instala(monkeypatch, {SLUG_AMAZON: PAGINA_AMAZON, SLUG_ML: PAGINA_ML}, pedidos)
    return pedidos


# ---------- o link na mensagem ----------


def test_acha_o_link_do_pelando_nas_mensagens_reais():
    assert links_do_pelando(MENSAGEM_AMAZON) == ["https://pelando.promo/ajCGV"]
    assert links_do_pelando(MENSAGEM_ML) == ["https://pelando.promo/Jq9FA"]


def test_pontuacao_no_fim_nao_entra_no_link():
    assert links_do_pelando("olha https://pelando.promo/ajCGV.") == [
        "https://pelando.promo/ajCGV"
    ]


def test_link_repetido_conta_uma_vez():
    texto = "https://pelando.promo/ajCGV e de novo https://pelando.promo/ajCGV"

    assert links_do_pelando(texto) == ["https://pelando.promo/ajCGV"]


def test_link_de_outro_site_nao_e_do_pelando():
    texto = "https://meli.la/1abc https://www.amazon.com.br/dp/B0F3391RMZ"

    assert links_do_pelando(texto) == []


# ---------- a pagina da oferta ----------


def test_le_a_oferta_da_amazon_na_pagina_real():
    deal = deal_da_pagina(PAGINA_AMAZON)

    assert deal is not None
    assert deal.titulo == "Calça Feminina Lã Cintura Média Confortável Pittsburgh Penguins"
    assert deal.preco == 60.18
    assert deal.loja == "Amazon"
    assert deal.ativa is True
    assert deal.cupom == ""
    assert "B0F3391RMZ" in deal.url_da_loja


def test_le_a_oferta_do_ml_com_cupom_na_pagina_real():
    deal = deal_da_pagina(PAGINA_ML)

    assert deal is not None
    assert deal.preco == 2372.0
    assert deal.loja == "Mercado Livre"
    assert deal.cupom == "MIMODODIA"
    assert deal.descricao == "Preço válido á vista + cupom!"
    assert "/p/MLB75001430" in deal.url_da_loja


def test_pagina_sem_oferta_devolve_none():
    assert deal_da_pagina("<html><body>nada aqui</body></html>") is None
    assert deal_da_pagina('<astro-island props="{nao e json}"></astro-island>') is None


def test_oferta_encerrada_vem_inativa():
    encerrada = PAGINA_ML.replace(
        "&quot;status&quot;:[0,&quot;active&quot;]", "&quot;status&quot;:[0,&quot;expired&quot;]"
    )

    assert encerrada != PAGINA_ML
    assert deal_da_pagina(encerrada).ativa is False


# ---------- do endereco da loja ao ID ----------


def deal(url: str, **extra) -> DealPelando:
    base = dict(titulo="Produto", preco=10.0, loja="x", url_da_loja=url)
    base.update(extra)
    return DealPelando(**base)


def test_amazon_vira_asin():
    url = "https://www.amazon.com.br/dp/B0F3391RMZ?ref=ppx_yo2ov_dt_b_fed_asin_title&th=1&psc=1"

    assert _classifica(deal(url)) == ("amazon", "B0F3391RMZ")


def test_amazon_com_titulo_na_frente_do_dp():
    url = "https://www.amazon.com.br/processador-multiefeitos/dp/B0D5QCT2XY?ref=x"

    assert _classifica(deal(url)) == ("amazon", "B0D5QCT2XY")


def test_ml_de_catalogo_vira_o_id_do_catalogo():
    url = "https://www.mercadolivre.com.br/smart-tv-lg-50-qned/p/MLB75001430?"

    assert _classifica(deal(url)) == ("mercadolivre", "MLB75001430")


def test_ml_de_catalogo_ignora_o_rastreio_da_query():
    url = "https://www.mercadolivre.com.br/p/MLB18410268?matt_tool=38524122&pdp_filters=item_id:MLB5362698854"

    assert _classifica(deal(url)) == ("mercadolivre", "MLB18410268")


@pytest.mark.parametrize(
    "url",
    [
        # Produto de usuario: a API de preco nao responde por ele, e a pagina
        # direta do ML devolve account-verification para servidor.
        "https://www.mercadolivre.com.br/estante-aco-60cm/up/MLBU4332315912#",
        "https://produto.mercadolivre.com.br/MLB-3456789012-cadeira-gamer",
        "https://shopee.com.br/produto-i.123.456",
        "https://www.amazon.com.br/s?k=fone",  # busca, sem ASIN
    ],
)
def test_o_que_o_repasse_nao_sabe_tratar_fica_de_fora(url):
    assert _classifica(deal(url)) is None


# ---------- condicoes do preco da Amazon ----------


def test_cupom_do_pelando_vira_condicao_do_preco_da_amazon():
    assert _condicoes_do_deal(deal("u", cupom="20DISNEY")) == ("com o cupom 20DISNEY",)


def test_pix_na_descricao_vira_condicao():
    d = deal("u", descricao="Valor com desconto no pix")

    assert _condicoes_do_deal(d) == ("no Pix",)


def test_programe_e_poupe_e_parcelado_vem_da_descricao():
    d = deal("u", descricao="Selecione Programe e Poupe, em 3x sem juros")

    assert _condicoes_do_deal(d) == ("em 3x sem juros", "Programe e Poupe")


def test_oferta_a_vista_nao_tem_condicao():
    assert _condicoes_do_deal(deal("u", descricao="Precinho muito bom")) == ()


# ---------- os dois saltos ----------


def cliente(transporte) -> httpx.Client:
    return httpx.Client(transport=transporte, follow_redirects=False)


def test_resolve_o_encurtador_e_le_a_pagina_sem_o_rastreio():
    pedidos: list[str] = []
    transporte = pelando_falso({SLUG_AMAZON: PAGINA_AMAZON}, pedidos)

    achado = deal_do_link("https://pelando.promo/ajCGV", cliente(transporte))

    assert achado is not None and achado.preco == 60.18
    # O segundo pedido vai para a pagina limpa: `aff_id` e `utm_*` sao do Pelando.
    assert pedidos == [
        "https://pelando.promo/ajCGV",
        f"https://www.pelando.com.br/d/{SLUG_AMAZON}",
    ]


def test_cada_link_e_resolvido_uma_vez():
    pedidos: list[str] = []
    c = cliente(pelando_falso({SLUG_AMAZON: PAGINA_AMAZON}, pedidos))

    deal_do_link("https://pelando.promo/ajCGV", c)
    deal_do_link("https://pelando.promo/ajCGV", c)

    assert len(pedidos) == 2


def test_destino_fora_do_pelando_nao_e_lido():
    pedidos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pedidos.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://exemplo.com/oferta"})

    achado = deal_do_link("https://pelando.promo/ajCGV", cliente(httpx.MockTransport(handler)))

    assert achado is None
    assert pedidos == ["https://pelando.promo/ajCGV"]


def test_falha_de_rede_nao_vira_cache():
    """Rede caida hoje nao pode enterrar a oferta para o resto do processo."""
    chamadas = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise httpx.ConnectError("sem rede")
        if request.url.host == "pelando.promo":
            return httpx.Response(
                302, headers={"location": f"https://www.pelando.com.br/d/{SLUG_AMAZON}"}
            )
        return httpx.Response(200, text=PAGINA_AMAZON)

    c = cliente(httpx.MockTransport(handler))

    assert deal_do_link("https://pelando.promo/ajCGV", c) is None
    assert deal_do_link("https://pelando.promo/ajCGV", c) is not None


# ---------- o grupo inteiro ----------


def test_o_grupo_vira_pista_do_ml_e_oferta_da_amazon(pelando):
    registros = [registro(MENSAGEM_ML), registro(MENSAGEM_AMAZON)]

    pistas, amazon = ofertas_do_grupo(registros, origem="Achadinhos Vip")

    assert [(p.external_id, p.cupons, p.origem) for p in pistas] == [
        ("MLB75001430", ("MIMODODIA",), "Achadinhos Vip")
    ]
    assert [(a.asin, a.preco, a.condicoes) for a in amazon] == [("B0F3391RMZ", 60.18, ())]


def test_a_pista_do_ml_vai_sem_preco_para_a_api_medir(pelando):
    """Com preco e endereco a pista viraria oferta pelo numero do Pelando, e
    sem foto. Sem eles ela cai em `fetch_by_ids`: preco nosso e foto do catalogo."""
    pistas, _ = ofertas_do_grupo([registro(MENSAGEM_ML)])

    assert pistas[0].preco == 0.0
    assert pistas[0].url == ""
    assert pistas[0].titulo == "Smart TV LG 50'' QNED Mini LED 4K Processador A7"


def test_nenhum_pedido_toca_o_redirecionador_de_afiliado(pelando):
    ofertas_do_grupo([registro(MENSAGEM_ML), registro(MENSAGEM_AMAZON)])

    assert pelando, "nenhum pedido feito"
    assert not any("dpl.pelando" in url or "amazon" in url or "mercadolivre" in url for url in pelando)


def test_o_mesmo_produto_em_duas_mensagens_conta_uma_vez(pelando):
    pistas, _ = ofertas_do_grupo([registro(MENSAGEM_ML), registro(MENSAGEM_ML)])

    assert len(pistas) == 1


def test_oferta_encerrada_nao_e_repassada(monkeypatch):
    encerrada = PAGINA_ML.replace(
        "&quot;status&quot;:[0,&quot;active&quot;]", "&quot;status&quot;:[0,&quot;expired&quot;]"
    )
    instala(monkeypatch, {SLUG_ML: encerrada})

    pistas, amazon = ofertas_do_grupo([registro(MENSAGEM_ML)])

    assert pistas == [] and amazon == []


def test_amazon_com_cupom_sai_com_a_condicao(monkeypatch):
    com_cupom = PAGINA_AMAZON.replace(
        "&quot;couponCode&quot;:[0,null]", "&quot;couponCode&quot;:[0,&quot;20DISNEY&quot;]"
    )
    assert com_cupom != PAGINA_AMAZON
    instala(monkeypatch, {SLUG_AMAZON: com_cupom})

    _, amazon = ofertas_do_grupo([registro(MENSAGEM_AMAZON)])

    assert amazon[0].condicoes == ("com o cupom 20DISNEY",)
    assert amazon[0].condicao == "com o cupom 20DISNEY"


def test_limite_de_links_corta_a_leitura(pelando):
    registros = [registro(MENSAGEM_ML), registro(MENSAGEM_AMAZON)]

    pistas, amazon = ofertas_do_grupo(registros, limite_links=1)

    assert len(pistas) + len(amazon) == 1


def test_mensagem_sem_link_do_pelando_nao_gera_pedido(pelando):
    pistas, amazon = ofertas_do_grupo([registro("bom dia grupo, https://meli.la/1abc")])

    assert (pistas, amazon, pelando) == ([], [], [])


# ---------- dentro da rodada ----------


@pytest.fixture
def rodada(monkeypatch, banco_em_memoria):
    """`collect_de_outros_grupos` com o grupo do Pelando como unica fonte."""
    from promo import pipeline
    from promo.config import EvolutionConfig
    from promo.sources import grupo_wa

    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()
    pipeline._CUPONS_DO_OUTRO_GRUPO.clear()
    monkeypatch.setenv("AMAZON_PARTNER_TAG", "comunidaded0a-20")
    monkeypatch.setattr(
        "promo.pipeline.load_grupos_fonte",
        lambda *a, **k: [
            {"jid": "pelando@g.us", "nome": "Achadinhos Vip", "tipo": "pelando", "limite": 5}
        ],
    )
    monkeypatch.setattr(
        EvolutionConfig,
        "load",
        classmethod(
            lambda cls, *a, **k: EvolutionConfig(
                base_url="http://e", api_key="k", instance="i", group_jid="g@g.us"
            )
        ),
    )
    monkeypatch.setattr(
        grupo_wa, "ler_mensagens", lambda *a, **k: [registro(MENSAGEM_ML), registro(MENSAGEM_AMAZON)]
    )

    def nao_deveria(**_kw):
        raise AssertionError("o grupo do Pelando nao passa pelo leitor de link do ML")

    monkeypatch.setattr(grupo_wa, "pistas", nao_deveria)
    yield pipeline
    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()
    pipeline._CUPONS_DO_OUTRO_GRUPO.clear()


class MLFalso:
    """A fonte do ML: guarda o que `fetch_by_ids` recebeu e devolve uma oferta."""

    def __init__(self) -> None:
        self.alvos: list = []

    def fetch_by_ids(self, alvos):
        from promo.models import Offer

        self.alvos = list(alvos)
        return [
            Offer(
                source="mercadolivre",
                external_id=ext,
                title=titulo,
                price=2399.0,
                url=f"https://www.mercadolivre.com.br/p/{ext}",
                image_url="https://http2.mlstatic.com/foto-F.jpg",
            )
            for ext, titulo, _img in alvos
        ]


def test_na_rodada_o_ml_vai_pela_api_e_a_amazon_leva_o_nosso_link(rodada, pelando):
    ml = MLFalso()

    achados = rodada.collect_de_outros_grupos(ml)

    assert [(ext, img) for ext, _t, img in ml.alvos] == [("MLB75001430", None)]
    por_fonte = {o.source: o for o in achados}
    assert set(por_fonte) == {"mercadolivre", "amazon"}

    amazon = por_fonte["amazon"]
    assert amazon.external_id == "B0F3391RMZ"
    assert amazon.url == "https://www.amazon.com.br/dp/B0F3391RMZ"  # canonica, sem tag
    assert amazon.image_url.startswith("https://m.media-amazon.com/images/P/B0F3391RMZ")
    assert amazon.price == 60.18


def test_na_rodada_o_cupom_do_pelando_chega_ao_post_do_ml(rodada, pelando):
    rodada.collect_de_outros_grupos(MLFalso())

    assert rodada._CUPONS_DO_OUTRO_GRUPO["MLB75001430"] == ("MIMODODIA",)


def test_na_rodada_as_ofertas_do_pelando_ficam_no_topo_da_fila(rodada, pelando):
    achados = rodada.collect_de_outros_grupos(MLFalso())

    assert all(rodada._veio_de_outro_grupo(o) for o in achados)


def test_sem_a_tag_da_amazon_so_o_ml_passa(rodada, pelando, monkeypatch):
    monkeypatch.delenv("AMAZON_PARTNER_TAG", raising=False)

    achados = rodada.collect_de_outros_grupos(MLFalso())

    assert {o.source for o in achados} == {"mercadolivre"}

"""Nos temas prioritarios a oferta sai da loja oficial.

O mesmo produto tem dezenas de anuncios de vendedores diferentes, e a regra
geral -- o mais barato -- e a certa quase sempre. Nos temas do `priority` ela
e a errada: sao marcas de salao e dermocosmetico, o vendedor avulso barato e
onde mora a falsificacao, e quem responde pelo link e o grupo.

Medido em 13 produtos dos temas, em 30/08/2026:

    12 de 13 tem anuncio de loja oficial
    sobrepreco mediano do oficial: +0%   (em 8 de 12 o mais barato JA era)
    cauda: +53% numa Truss Net Mask, +20% numa melatonina em gomas
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import MercadoLivreConfig  # noqa: E402
from promo.sources.mercadolivre import MercadoLivre  # noqa: E402

TEMAS = ["wella", "truss", "cerave"]

PRODUTO = {
    "id": "MLB1",
    "name": "Shampoo Wella Blondorplex 250ml",
    "pictures": [{"secure_url": "https://http2.mlstatic.com/foto-F.jpg"}],
}


def anuncio(preco: float, oficial: bool, riscado: float | None = None) -> dict:
    listing = {
        "price": preco,
        "condition": "new",
        "currency_id": "BRL",
        "shipping": {"free_shipping": True},
        "category_id": "MLB1246",
    }
    if oficial:
        listing["official_store_id"] = 42
    if riscado:
        listing["original_price"] = riscado
    return listing


def fonte(
    anuncios: list[dict], temas: list[str] | None = None, nome: str = ""
) -> MercadoLivre:
    produto = dict(PRODUTO, name=nome or PRODUTO["name"])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/items"):
            return httpx.Response(200, json={"results": anuncios})
        return httpx.Response(200, json=produto)

    ml = MercadoLivre(
        MercadoLivreConfig(
            client_id="1",
            client_secret="2",
            redirect_uri="https://example.com/callback",
            site_id="MLB",
        ),
        temas,
    )
    ml._client = httpx.Client(transport=httpx.MockTransport(handler))
    ml.access_token = lambda: "token"  # type: ignore[method-assign]
    return ml


def oferta(anuncios: list[dict], temas: list[str] | None = None, nome: str = ""):
    ml = fonte(anuncios, temas, nome)
    return ml._offer_for("MLB1", title=nome or PRODUTO["name"], image_url=None)


# ---------- a regra geral nao muda ----------


def test_sem_temas_vai_o_mais_barato():
    """Comportamento antigo: quem nao configurar nada nao muda de resultado."""
    anuncios = [anuncio(80.90, oficial=False), anuncio(127.50, oficial=True)]

    assert oferta(anuncios).price == 80.90


def test_produto_fora_do_tema_vai_o_mais_barato():
    anuncios = [anuncio(80.90, oficial=False), anuncio(127.50, oficial=True)]

    assert oferta(anuncios, TEMAS, nome="Smart Tv Philco 50").price == 80.90


# ---------- dentro do tema ----------


def test_no_tema_vai_a_loja_oficial_mesmo_custando_mais():
    """O caso medido: Truss Net Mask, R$ 216,80 avulso contra R$ 332 oficial."""
    anuncios = [anuncio(216.80, oficial=False), anuncio(332.00, oficial=True)]

    assert oferta(anuncios, TEMAS, nome="Truss Net Mask Hidratação").price == 332.00


def test_entre_oficiais_ainda_vale_o_mais_barato():
    anuncios = [
        anuncio(150.00, oficial=True),
        anuncio(127.50, oficial=True),
        anuncio(80.90, oficial=False),
    ]

    assert oferta(anuncios, TEMAS).price == 127.50


def test_sem_loja_oficial_cai_na_regra_geral():
    """1 dos 13 produtos medidos nao tinha nenhuma. Vaga vazia nao protege
    ninguem -- some a oferta e o tema deixa de aparecer no grupo."""
    anuncios = [anuncio(80.90, oficial=False), anuncio(96.00, oficial=False)]

    assert oferta(anuncios, TEMAS).price == 80.90


def test_o_anuncio_escolhido_e_marcado_como_loja_oficial():
    """`official_store` vai pro post: e sinal de confianca, e o texto so pode
    cita-lo se vier do anuncio que de fato foi escolhido."""
    anuncios = [anuncio(80.90, oficial=False), anuncio(127.50, oficial=True)]

    assert oferta(anuncios, TEMAS).official_store is True


def test_o_preco_riscado_vem_do_anuncio_escolhido():
    """Pegar o riscado de um anuncio e o preco de outro inventaria desconto."""
    anuncios = [
        anuncio(80.90, oficial=False, riscado=300.00),
        anuncio(127.50, oficial=True, riscado=150.00),
    ]

    escolhida = oferta(anuncios, TEMAS)

    assert (escolhida.price, escolhida.original_price) == (127.50, 150.00)


def test_anuncio_usado_fica_de_fora_mesmo_sendo_oficial():
    """Anuncio usado esta fora das regras do programa de afiliados; a loja ser
    oficial nao muda isso."""
    usado = dict(anuncio(50.00, oficial=True), condition="used")
    anuncios = [usado, anuncio(127.50, oficial=True)]

    assert oferta(anuncios, TEMAS).price == 127.50


def test_produto_sem_anuncio_ativo_continua_devolvendo_nada():
    assert oferta([], TEMAS) is None


def test_acento_no_titulo_nao_escapa_do_tema():
    """O vendedor digita o titulo; o tema e escrito a mao no watchlist.json."""
    anuncios = [anuncio(80.90, oficial=False), anuncio(127.50, oficial=True)]

    assert (
        oferta(anuncios, ["mascara"], nome="Máscara Wella Blondorplex").price == 127.50
    )

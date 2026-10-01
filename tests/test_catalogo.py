"""A coleta agora e por produto de catalogo, nao por anuncio.

O ML aposentou /sites/MLB/search e /items para apps comuns (403 do policy
agent). O que restou: /products/search descobre, /products/{id}/items da os
precos. Um produto tem dezenas de anuncios de vendedores diferentes, entao o
preco que interessa e o menor entre eles.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.mercadolivre import SITE_HOST, MercadoLivre  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Guarda as URLs pedidas e devolve payloads combinados."""

    def __init__(self, rotas: dict[str, FakeResponse]) -> None:
        self.rotas = rotas
        self.pedidas: list[str] = []

    def get(self, url, params=None, headers=None, **kwargs):
        self.pedidas.append(url)
        for fragmento, resposta in self.rotas.items():
            if fragmento in url:
                return resposta
        raise AssertionError(f"rota nao combinada: {url}")


# Os testes de preco passam uma foto: sem ela `_offer_for` pede uma ao catalogo,
# e a foto nao e o assunto deles. O comportamento sem foto tem testes proprios.
FOTO = "https://http2.mlstatic.com/D_NQ_NP_1-F.jpg"


def build(rotas: dict[str, FakeResponse]) -> MercadoLivre:
    class Cfg:
        client_id = "1"
        client_secret = "s"
        redirect_uri = "https://exemplo.com/cb"
        site_id = "MLB"

    source = MercadoLivre(Cfg())
    source._client = FakeClient(rotas)
    source.access_token = lambda: "tok"  # type: ignore[method-assign]
    return source


def listing(item_id: str, price: float, **extra) -> dict:
    base = {
        "item_id": item_id,
        "price": price,
        "condition": "new",
        "currency_id": "BRL",
        "category_id": "MLB1055",
        "shipping": {"free_shipping": True},
        "original_price": None,
    }
    base.update(extra)
    return base


# ---------- _offer_for ----------


def test_pega_o_menor_preco_entre_os_anuncios():
    """27 anuncios do mesmo celular variavam de R$ 1.289 a R$ 1.899 na API real."""
    source = build(
        {
            "/items": FakeResponse(
                {
                    "results": [
                        listing("MLB700", 1899.0),
                        listing("MLB701", 1289.0),
                        listing("MLB702", 1299.0),
                    ]
                }
            )
        }
    )

    offer = source._offer_for("MLB54982411", title="Celular", image_url=FOTO)

    assert offer is not None
    assert offer.price == 1289.0


def test_ignora_anuncio_usado():
    """Anuncio usado fica fora das regras do programa de afiliados."""
    source = build(
        {
            "/items": FakeResponse(
                {
                    "results": [
                        listing("MLB700", 500.0, condition="used"),
                        listing("MLB701", 900.0, condition="new"),
                    ]
                }
            )
        }
    )

    offer = source._offer_for("MLB1", title="Produto", image_url=FOTO)

    assert offer is not None
    assert offer.price == 900.0  # o usado, mais barato, foi descartado


def test_sem_anuncio_novo_nao_vira_oferta():
    source = build(
        {
            "/items": FakeResponse(
                {"results": [listing("MLB700", 500.0, condition="used")]}
            )
        }
    )

    assert source._offer_for("MLB1", title="Produto", image_url=None) is None


def test_produto_fora_do_catalogo_e_ignorado():
    """404 acontece quando o produto sai do ar; nao pode derrubar a rodada."""
    source = build({"/items": FakeResponse({}, status_code=404)})

    assert source._offer_for("MLB1", title="Produto", image_url=None) is None


def test_monta_a_url_a_partir_do_id():
    """O catalogo devolve permalink vazio -- a URL tem que ser construida."""
    source = build({"/items": FakeResponse({"results": [listing("MLB700", 100.0)]})})

    offer = source._offer_for("MLB54982411", title="Produto", image_url=FOTO)

    assert offer is not None
    assert offer.url == f"{SITE_HOST}/p/MLB54982411"


def test_leva_original_price_do_anuncio_vencedor():
    source = build(
        {
            "/items": FakeResponse(
                {
                    "results": [
                        listing("MLB700", 1599.0, original_price=1899.0),
                        listing("MLB701", 1399.0, original_price=1599.0),
                    ]
                }
            )
        }
    )

    offer = source._offer_for("MLB1", title="Produto", image_url=FOTO)

    assert offer is not None
    assert (offer.price, offer.original_price) == (1399.0, 1599.0)


def test_anuncio_sem_preco_e_descartado():
    source = build(
        {
            "/items": FakeResponse(
                {
                    "results": [
                        listing("MLB700", 0),
                        listing("MLB701", 250.0),
                    ]
                }
            )
        }
    )

    offer = source._offer_for("MLB1", title="Produto", image_url=FOTO)

    assert offer is not None
    assert offer.price == 250.0


# ---------- foto que chega vazia ----------
#
# A rota B das landing pages manda `(mlb, titulo, None)` para `fetch_by_ids`.
# Medido em 30/09/2026: 78 produtos de beleza e 217 posts so com texto, porque
# `_offer_for` confiava na foto que recebia e a reconsulta, que le o banco,
# repetia o vazio para sempre.


def test_foto_vazia_e_buscada_no_catalogo():
    source = build(
        {
            "/items": FakeResponse({"results": [listing("MLB700", 100.0)]}),
            "/products/MLB57111180": FakeResponse(
                {"name": "Serum", "pictures": [{"url": "https://f/1-F.jpg"}]}
            ),
        }
    )

    offer = source._offer_for("MLB57111180", title="Serum", image_url=None)

    assert offer is not None
    assert offer.image_url == "https://f/1-F.jpg"


def test_foto_que_ja_veio_nao_gasta_chamada():
    source = build({"/items": FakeResponse({"results": [listing("MLB700", 100.0)]})})

    offer = source._offer_for("MLB1", title="Produto", image_url=FOTO)

    assert offer is not None
    assert offer.image_url == FOTO
    assert not any(url.endswith("/products/MLB1") for url in source._client.pedidas)


def test_produto_sem_anuncio_nao_busca_foto():
    """Sem oferta nao ha post, e foto de post que nao existe e chamada perdida."""
    source = build({"/items": FakeResponse({"results": []})})

    assert source._offer_for("MLB1", title="Produto", image_url=None) is None
    assert source._client.pedidas == [
        "https://api.mercadolibre.com/products/MLB1/items"
    ]


def test_falha_na_foto_nao_derruba_a_oferta():
    """Post sem foto e pior que com foto, mas melhor que nenhum post."""
    source = build(
        {
            "/items": FakeResponse({"results": [listing("MLB700", 100.0)]}),
            "/products/MLB1": FakeResponse({}, status_code=503),
        }
    )

    # O FakeResponse levanta RuntimeError; o de verdade levanta httpx.HTTPError.
    import httpx

    def recusa():
        raise httpx.HTTPStatusError(
            "503", request=httpx.Request("GET", "http://x"), response=httpx.Response(503)
        )

    source._client.rotas["/products/MLB1"].raise_for_status = recusa  # type: ignore[method-assign]

    offer = source._offer_for("MLB1", title="Produto", image_url=None)

    assert offer is not None
    assert offer.price == 100.0
    assert offer.image_url is None


def test_produto_de_catalogo_sem_foto_na_api_segue_sem_foto():
    source = build(
        {
            "/items": FakeResponse({"results": [listing("MLB700", 100.0)]}),
            "/products/MLB1": FakeResponse({"name": "Produto", "pictures": []}),
        }
    )

    offer = source._offer_for("MLB1", title="Produto", image_url=None)

    assert offer is not None
    assert offer.image_url is None


# ---------- endereco do produto ----------


def test_produto_de_catalogo_usa_p_e_produto_de_usuario_usa_up():
    """A reconsulta monta a URL so pelo ID e grava por cima da que ja estava.

    `/p/MLBU...` nao existe: sem esta distincao a reconsulta trocaria, no banco,
    o endereco do produto de usuario por um que o Link Builder nao conhece.
    """
    from promo.sources.mercadolivre import _url_do_produto

    assert _url_do_produto("MLB75001430") == f"{SITE_HOST}/p/MLB75001430"
    assert _url_do_produto("MLBU4332315912") == f"{SITE_HOST}/up/MLBU4332315912"


def test_oferta_de_produto_de_usuario_leva_o_endereco_up():
    source = build({"/items": FakeResponse({"results": [listing("MLB700", 144.98)]})})

    offer = source._offer_for("MLBU4332315912", title="Estante", image_url=FOTO)

    assert offer is not None
    assert offer.url == f"{SITE_HOST}/up/MLBU4332315912"


# ---------- search ----------


def test_search_usa_o_catalogo_e_resolve_preco_por_produto():
    source = build(
        {
            "/products/search": FakeResponse(
                {
                    "results": [
                        {
                            "id": "MLB1",
                            "name": "Air Fryer 4L",
                            "pictures": [{"url": "https://http2.mlstatic.com/a-F.jpg"}],
                        },
                    ]
                }
            ),
            "/items": FakeResponse({"results": [listing("MLB700", 349.0)]}),
        }
    )

    offers = source.search("air fryer")

    assert len(offers) == 1
    offer = offers[0]
    assert offer.external_id == "MLB1"
    assert offer.title == "Air Fryer 4L"
    assert offer.price == 349.0
    assert offer.image_url == "https://http2.mlstatic.com/a-F.jpg"
    assert offer.free_shipping is True


# ---------- fetch_by_ids ----------


def test_reconsulta_nao_repete_chamada_de_nome_e_foto():
    """Titulo e imagem vem do banco: 1 chamada por produto, nao 2."""
    source = build({"/items": FakeResponse({"results": [listing("MLB700", 99.0)]})})

    offers = source.fetch_by_ids([("MLB1", "Produto salvo", "https://foto-F.jpg")])

    assert len(source._client.pedidas) == 1
    assert offers[0].title == "Produto salvo"
    assert offers[0].image_url == "https://foto-F.jpg"

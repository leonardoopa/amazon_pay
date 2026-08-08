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
    source = build({
        "/items": FakeResponse({"results": [
            listing("MLB700", 1899.0),
            listing("MLB701", 1289.0),
            listing("MLB702", 1299.0),
        ]})
    })

    offer = source._offer_for("MLB54982411", title="Celular", image_url=None)

    assert offer is not None
    assert offer.price == 1289.0


def test_ignora_anuncio_usado():
    """Anuncio usado fica fora das regras do programa de afiliados."""
    source = build({
        "/items": FakeResponse({"results": [
            listing("MLB700", 500.0, condition="used"),
            listing("MLB701", 900.0, condition="new"),
        ]})
    })

    offer = source._offer_for("MLB1", title="Produto", image_url=None)

    assert offer is not None
    assert offer.price == 900.0  # o usado, mais barato, foi descartado


def test_sem_anuncio_novo_nao_vira_oferta():
    source = build({
        "/items": FakeResponse({"results": [listing("MLB700", 500.0, condition="used")]})
    })

    assert source._offer_for("MLB1", title="Produto", image_url=None) is None


def test_produto_fora_do_catalogo_e_ignorado():
    """404 acontece quando o produto sai do ar; nao pode derrubar a rodada."""
    source = build({"/items": FakeResponse({}, status_code=404)})

    assert source._offer_for("MLB1", title="Produto", image_url=None) is None


def test_monta_a_url_a_partir_do_id():
    """O catalogo devolve permalink vazio -- a URL tem que ser construida."""
    source = build({"/items": FakeResponse({"results": [listing("MLB700", 100.0)]})})

    offer = source._offer_for("MLB54982411", title="Produto", image_url=None)

    assert offer is not None
    assert offer.url == f"{SITE_HOST}/p/MLB54982411"


def test_leva_original_price_do_anuncio_vencedor():
    source = build({
        "/items": FakeResponse({"results": [
            listing("MLB700", 1599.0, original_price=1899.0),
            listing("MLB701", 1399.0, original_price=1599.0),
        ]})
    })

    offer = source._offer_for("MLB1", title="Produto", image_url=None)

    assert offer is not None
    assert (offer.price, offer.original_price) == (1399.0, 1599.0)


def test_anuncio_sem_preco_e_descartado():
    source = build({
        "/items": FakeResponse({"results": [
            listing("MLB700", 0),
            listing("MLB701", 250.0),
        ]})
    })

    offer = source._offer_for("MLB1", title="Produto", image_url=None)

    assert offer is not None
    assert offer.price == 250.0


# ---------- search ----------


def test_search_usa_o_catalogo_e_resolve_preco_por_produto():
    source = build({
        "/products/search": FakeResponse({"results": [
            {"id": "MLB1", "name": "Air Fryer 4L",
             "pictures": [{"url": "https://http2.mlstatic.com/a-F.jpg"}]},
        ]}),
        "/items": FakeResponse({"results": [listing("MLB700", 349.0)]}),
    })

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

"""Descoberta pelos mais vendidos de uma categoria.

O ML nao tem endpoint de ofertas para afiliado. `/highlights` e o mais perto
disso: os mais vendidos por categoria. Serve pra achar produto que ninguem
pensaria em colocar na watchlist -- mas custa duas chamadas por produto, entao
o teto e o que os testes protegem junto com o parsing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import MercadoLivreConfig  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.pipeline import Category, collect_categories, load_categories  # noqa: E402
from promo.sources.mercadolivre import (  # noqa: E402
    API_HOST,
    PRODUCTS_PER_CATEGORY,
    MercadoLivre,
)

# Formato real: so id/position/type, sem nome nem preco. USER_PRODUCT vem
# misturado (4 em 20 em algumas categorias) e nao e produto de catalogo.
DESTAQUES = {
    "query_data": {"highlight_type": "BEST_SELLER", "criteria": "CATEGORY"},
    "content": [
        {"id": "MLB1", "position": 1, "type": "PRODUCT"},
        {"id": "MLBUSER", "position": 2, "type": "USER_PRODUCT"},
        {"id": "MLB2", "position": 3, "type": "PRODUCT"},
    ],
}

PRODUTO = {
    "id": "MLB1",
    "name": "Celular Bonito 128gb",
    "pictures": [{"secure_url": "https://http2.mlstatic.com/foto-F.jpg"}],
}

ANUNCIOS = {
    "results": [
        {
            "price": 999.0,
            "condition": "new",
            "currency_id": "BRL",
            "shipping": {"free_shipping": True},
            "category_id": "MLB1051",
        }
    ]
}


def fonte(handler, **extra) -> MercadoLivre:
    config = MercadoLivreConfig(
        client_id="1",
        client_secret="2",
        redirect_uri="https://example.com/callback",
        site_id="MLB",
        **extra,
    )
    ml = MercadoLivre(config)
    ml._client = httpx.Client(transport=httpx.MockTransport(handler))
    ml.access_token = lambda: "token"  # type: ignore[method-assign]
    return ml


def roteador(destaques: dict = DESTAQUES, registro: list | None = None):
    """Responde highlights / products/{id} / products/{id}/items."""

    def handler(request: httpx.Request) -> httpx.Response:
        caminho = request.url.path
        if registro is not None:
            registro.append(caminho)
        if "/highlights/" in caminho:
            return httpx.Response(200, json=destaques)
        if caminho.endswith("/items"):
            return httpx.Response(200, json=ANUNCIOS)
        return httpx.Response(200, json=dict(PRODUTO, id=caminho.rsplit("/", 1)[-1]))

    return handler


# ---------- highlights ----------


def test_ignora_user_product():
    """USER_PRODUCT e anuncio de vendedor: /products/{id}/items nao responde
    por ele, e uma 404 no meio da rodada viraria ruido no log."""
    ml = fonte(roteador())
    offers = ml.highlights("MLB1051")

    assert [o.external_id for o in offers] == ["MLB1", "MLB2"]


def test_monta_oferta_completa():
    ml = fonte(roteador())
    offer = ml.highlights("MLB1051")[0]

    assert offer.title == "Celular Bonito 128gb"
    assert offer.price == 999.0
    assert offer.image_url == "https://http2.mlstatic.com/foto-F.jpg"
    assert offer.free_shipping is True
    assert offer.url == "https://www.mercadolivre.com.br/p/MLB1"


def test_respeita_o_teto_de_produtos():
    """Cada produto custa 2 chamadas; sem teto uma categoria sozinha estoura
    a rodada."""
    muitos = {
        "content": [
            {"id": f"MLB{i}", "position": i, "type": "PRODUCT"} for i in range(50)
        ]
    }
    ml = fonte(roteador(destaques=muitos))

    assert len(ml.highlights("MLB1051")) == PRODUCTS_PER_CATEGORY


def test_teto_corta_antes_de_gastar_chamada():
    """Cortar depois de buscar seria pagar por resultado descartado."""
    muitos = {
        "content": [
            {"id": f"MLB{i}", "position": i, "type": "PRODUCT"} for i in range(50)
        ]
    }
    chamadas: list[str] = []
    ml = fonte(roteador(destaques=muitos, registro=chamadas))
    ml.highlights("MLB1051", limit=2)

    # 1 highlights + 2 produtos x (meta + anuncios)
    assert len(chamadas) == 5


def test_categoria_inexistente_devolve_vazio():
    """404 aqui e config errada no watchlist.json, nao motivo pra derrubar."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    assert fonte(handler).highlights("MLB0000") == []


def test_produto_sumido_do_catalogo_e_pulado():
    def handler(request: httpx.Request) -> httpx.Response:
        if "/highlights/" in request.url.path:
            return httpx.Response(200, json=DESTAQUES)
        return httpx.Response(404, json={"message": "not found"})

    assert fonte(handler).highlights("MLB1051") == []


# ---------- collect_categories ----------


class FonteComDestaques:
    name = "mercadolivre"

    def __init__(self, offers: list[Offer], falha: bool = False) -> None:
        self.offers = offers
        self.falha = falha
        self.pedidas: list[str] = []

    def highlights(self, category_id: str, limit: int = 10) -> list[Offer]:
        self.pedidas.append(category_id)
        if self.falha:
            raise RuntimeError("500 do ML")
        return self.offers


class FonteSemDestaques:
    name = "amazon"


def oferta(price: float, external_id: str = "MLB1") -> Offer:
    return Offer(
        source="mercadolivre",
        external_id=external_id,
        title="Produto",
        price=price,
        url=f"https://www.mercadolivre.com.br/p/{external_id}",
    )


def test_aplica_o_teto_de_preco_da_categoria():
    fonte = FonteComDestaques([oferta(100.0, "MLB1"), oferta(5000.0, "MLB2")])
    offers, _ = collect_categories(fonte, [Category(id="MLB1051", max_price=1000)])

    assert [o.external_id for o in offers] == ["MLB1"]


def test_sem_teto_passa_tudo():
    fonte = FonteComDestaques([oferta(100.0), oferta(5000.0, "MLB2")])
    assert len(collect_categories(fonte, [Category(id="MLB1051")])[0]) == 2


def test_fonte_sem_highlights_e_ignorada():
    """Categoria e conceito do ML; a Amazon nao precisa fingir que tem."""
    # Fonte sem `highlights` nao chamou ninguem, entao nao respondeu nada.
    assert collect_categories(FonteSemDestaques(), [Category(id="MLB1051")]) == (
        [],
        False,
    )


def test_sem_categorias_nao_consulta():
    fonte = FonteComDestaques([oferta(100.0)])
    assert collect_categories(fonte, []) == ([], False)
    assert fonte.pedidas == []


def test_categoria_quebrada_nao_derruba_as_outras():
    """Uma categoria fora do ar nao pode custar a rodada inteira."""
    fonte = FonteComDestaques([], falha=True)
    offers, _ = collect_categories(
        fonte, [Category(id="MLB1051"), Category(id="MLB1276")]
    )

    assert offers == []
    assert fonte.pedidas == ["MLB1051", "MLB1276"]  # seguiu depois da falha


# ---------- watchlist.json ----------


def test_categorias_ausentes_e_o_normal(tmp_path):
    """O campo e opcional: watchlist antiga nao pode quebrar."""
    caminho = tmp_path / "w.json"
    caminho.write_text(json.dumps({"keywords": [{"term": "x"}]}), encoding="utf-8")

    assert load_categories(caminho) == []


def test_le_categorias_do_arquivo(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(
        json.dumps(
            {
                "keywords": [],
                "categories": [
                    {"id": "MLB1051", "name": "Celulares", "max_price": 3000}
                ],
            }
        ),
        encoding="utf-8",
    )

    (categoria,) = load_categories(caminho)
    assert categoria.id == "MLB1051"
    assert categoria.name == "Celulares"
    assert categoria.max_price == 3000


def test_categoria_so_com_id(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(
        json.dumps({"keywords": [], "categories": [{"id": "MLB1051"}]}),
        encoding="utf-8",
    )

    (categoria,) = load_categories(caminho)
    assert categoria.max_price is None and categoria.name == ""


def test_url_dos_destaques_usa_o_site_configurado():
    """Site errado devolve categoria de outro pais, em silencio."""
    capturadas: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        capturadas.append(str(request.url))
        return httpx.Response(200, json={"content": []})

    fonte(handler).highlights("MLB1051")
    assert capturadas[0] == f"{API_HOST}/highlights/MLB/category/MLB1051"


# ---------- fontes sem busca por termo ----------


def test_fonte_sem_search_nao_gera_ruido(caplog, banco_em_memoria):
    """A vitrine e uma foto do dia, nao aceita consulta por termo. Sem guarda,
    cada termo da watchlist virava um WARNING -- 35 por rodada, escondendo
    falha de verdade no meio.

    `banco_em_memoria` nao e detalhe deste caso: `collect()` abre o banco
    sozinho pra decidir se descobre, entao sem a fixture o teste vai parar no
    SQLite real do ambiente.
    """
    import logging

    from promo.pipeline import Watch, collect

    class SoVitrine:
        name = "ml_ofertas"

        def fetch(self, pages=1):
            return [oferta(10.0)]

    with caplog.at_level(logging.WARNING):
        collect([SoVitrine()], [Watch(term="fone"), Watch(term="tv")], Rules_stub())

    assert "no attribute 'search'" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def Rules_stub():
    from promo.config import Rules

    return Rules(
        min_discount_pct=15.0,
        baseline_window_days=60,
        min_observations=4,
        repost_cooldown_days=14,
        max_offers_per_run=10,
    )

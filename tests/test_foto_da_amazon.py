"""A oferta da Amazon so vira post com foto de verdade.

Medido em 17/10/2026: 142 de 505 posts da Amazon em 7 dias sairam com o GIF
transparente de 1x1 que a Amazon devolve quando nao tem foto do ASIN. A
Evolution aceitava o pixel com 201, entao nada acusava o erro.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.delivery import evolution  # noqa: E402
from promo.sources import amazon_link  # noqa: E402
from promo.sources.amazon_grupo import OfertaAmazon  # noqa: E402

PIXEL = b"GIF89a" + b"\x00" * 37  # 43 bytes, como o da Amazon
FOTO = b"\xff\xd8\xff" + b"\x00" * 20_000

# A de verdade, guardada antes de a fixture do conftest troca-la por `True`.
FOTO_EXISTE = amazon_link.foto_existe


def oferta(asin="B0G32NPYXB", imagem=""):
    return OfertaAmazon(asin=asin, titulo="Produto", preco=50.0, imagem=imagem)


# ---------- a checagem na Amazon ----------


@pytest.fixture
def amazon_responde(monkeypatch):
    """Troca a rede por respostas fixas e devolve a checagem verdadeira."""
    amazon_link._FOTO_EXISTE.clear()
    chamadas = []

    def responder(conteudo, tipo):
        def get(url, **kwargs):
            chamadas.append(url)
            return httpx.Response(
                200,
                content=conteudo,
                headers={"content-type": tipo},
                request=httpx.Request("GET", url),
            )

        monkeypatch.setattr(httpx, "get", get)
        return FOTO_EXISTE

    responder.chamadas = chamadas
    return responder


def test_pixel_da_amazon_nao_conta_como_foto(amazon_responde):
    foto_existe = amazon_responde(PIXEL, "image/gif")
    assert not foto_existe("B0G32NPYXB")


def test_jpeg_de_verdade_conta_como_foto(amazon_responde):
    foto_existe = amazon_responde(FOTO, "image/jpeg")
    assert foto_existe("B094SJF72Z")


def test_mesmo_asin_so_e_consultado_uma_vez(amazon_responde):
    foto_existe = amazon_responde(FOTO, "image/jpeg")
    foto_existe("B094SJF72Z")
    foto_existe("B094SJF72Z")
    assert len(amazon_responde.chamadas) == 1


def test_falha_de_rede_nao_custa_a_oferta(monkeypatch):
    amazon_link._FOTO_EXISTE.clear()

    def cai(url, **kwargs):
        raise httpx.ConnectError("sem rede")

    monkeypatch.setattr(httpx, "get", cai)
    assert FOTO_EXISTE("B0XXXXXXXX")


# ---------- a escolha da foto na rodada ----------


def test_sem_foto_na_amazon_a_oferta_fica_de_fora(monkeypatch):
    monkeypatch.setattr(amazon_link, "foto_existe", lambda asin: False)
    assert pipeline._com_foto([oferta()]) == []


def test_foto_do_pelando_vale_mesmo_sem_foto_na_amazon(monkeypatch):
    monkeypatch.setattr(amazon_link, "foto_existe", lambda asin: False)
    foto = "https://media.pelando.com.br/foto.webp"

    (saida,) = pipeline._com_foto([oferta(imagem=foto)])

    assert saida.image_url == foto


def test_com_foto_na_amazon_usa_a_do_asin():
    (saida,) = pipeline._com_foto([oferta(asin="B094SJF72Z")])
    assert saida.image_url == "https://m.media-amazon.com/images/P/B094SJF72Z.jpg"


# ---------- a ultima barreira, no envio ----------


def test_envio_em_base64_recusa_o_pixel(monkeypatch):
    class Cliente:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return httpx.Response(
                200,
                content=PIXEL,
                headers={"content-type": "image/gif"},
                request=httpx.Request("GET", url),
            )

    monkeypatch.setattr(evolution.httpx, "Client", Cliente)
    entrega = evolution.Evolution.__new__(evolution.Evolution)
    entrega._send = lambda *a, **k: pytest.fail("pixel nao pode ser enviado")

    with pytest.raises(ValueError):
        entrega.send_image_bytes("https://m.media-amazon.com/images/P/X.jpg", "texto")

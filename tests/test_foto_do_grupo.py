"""A foto que o grupo-fonte anexou ao post e a que sai no nosso."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx  # noqa: E402
import pytest  # noqa: E402

from promo.sources import grupo_wa  # noqa: E402
from promo.sources.amazon_grupo import ofertas_do_grupo  # noqa: E402

FOTO = b"\xff\xd8\xff" + b"x" * 5000


@pytest.fixture(autouse=True)
def pasta_de_fotos(tmp_path, monkeypatch):
    monkeypatch.setattr("promo.config.fotos_dir", lambda: tmp_path)
    return tmp_path


def registro(id_msg="ABC123", com_foto=True) -> dict:
    msg = {"imageMessage": {"caption": "oferta"}} if com_foto else {"conversation": "x"}
    return {"key": {"id": id_msg}, "message": msg}


def evolution_devolve(monkeypatch, base64_resposta, chamadas=None):
    def post(self, url, **kw):
        if chamadas is not None:
            chamadas.append(url)
        return httpx.Response(
            200, json={"base64": base64_resposta}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx.Client, "post", post)


def test_baixa_e_guarda_a_foto_da_mensagem(monkeypatch, pasta_de_fotos):
    evolution_devolve(monkeypatch, base64.b64encode(FOTO).decode())
    caminho = grupo_wa.baixar_foto("http://evo", "inst", "k", registro())
    assert Path(caminho).read_bytes() == FOTO
    assert Path(caminho).parent == pasta_de_fotos


def test_aceita_prefixo_data_uri(monkeypatch):
    evolution_devolve(
        monkeypatch, "data:image/jpeg;base64," + base64.b64encode(FOTO).decode()
    )
    assert grupo_wa.baixar_foto("http://evo", "inst", "k", registro())


def test_nao_baixa_de_novo(monkeypatch):
    chamadas: list[str] = []
    evolution_devolve(monkeypatch, base64.b64encode(FOTO).decode(), chamadas)
    grupo_wa.baixar_foto("http://evo", "inst", "k", registro())
    grupo_wa.baixar_foto("http://evo", "inst", "k", registro())
    assert len(chamadas) == 1


def test_mensagem_sem_foto_nao_chama_a_evolution(monkeypatch):
    chamadas: list[str] = []
    evolution_devolve(monkeypatch, "", chamadas)
    assert (
        grupo_wa.baixar_foto("http://evo", "inst", "k", registro(com_foto=False)) == ""
    )
    assert chamadas == []


def test_foto_minuscula_e_descartada(monkeypatch):
    evolution_devolve(monkeypatch, base64.b64encode(b"GIF89a").decode())
    assert grupo_wa.baixar_foto("http://evo", "inst", "k", registro()) == ""


def test_falha_da_evolution_nao_derruba(monkeypatch):
    def post(self, url, **kw):
        raise httpx.ConnectError("fora")

    monkeypatch.setattr(httpx.Client, "post", post)
    assert grupo_wa.baixar_foto("http://evo", "inst", "k", registro()) == ""


def test_oferta_da_amazon_leva_a_foto_do_grupo(monkeypatch):
    from test_amazon_do_grupo import XET_PARCELADO

    monkeypatch.setattr(
        "promo.sources.amazon_grupo.asin_do_link", lambda _url, **_kw: "B0FMLBQ136"
    )
    reg = {
        "key": {"id": "M1"},
        "message": {"imageMessage": {"caption": XET_PARCELADO}},
    }
    ofertas = ofertas_do_grupo(
        [reg], pausa=0, foto_de=lambda r: "/data/fotos/M1.jpg"
    )
    assert ofertas and ofertas[0].imagem == "/data/fotos/M1.jpg"

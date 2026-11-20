"""Cartao do assistente: foto baixada por nos, texto quando a foto nao serve."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.delivery import evolution as modulo  # noqa: E402
from promo.delivery.evolution import (  # noqa: E402
    MEDIA_USER_AGENT,
    Evolution,
    NotConnected,
)

LEGENDA = "Tenis Runner\nR$ 199,00\nhttps://exemplo.com/p"
DESTINO = "5581999999999@s.whatsapp.net"
URL = "https://cdn.exemplo.com/foto.jpg"


class Falso:
    """Evolution de mentira: guarda o que seria enviado."""

    def __init__(self, erro_no_envio: Exception | None = None) -> None:
        self.bytes_enviados: list[tuple] = []
        self.textos: list[tuple] = []
        self.erro_no_envio = erro_no_envio

    def _enviar_bytes(self, bruto, caption, to):
        if self.erro_no_envio:
            raise self.erro_no_envio
        self.bytes_enviados.append((bruto, caption, to))
        return {"ok": True}

    def send_text(self, text, to=None):
        self.textos.append((text, to))
        return {"texto": True}


class ClienteFalso:
    criados: list[dict] = []

    def __init__(self, resposta=None, erro=None, **kwargs) -> None:
        self.resposta = resposta
        self.erro = erro
        ClienteFalso.criados.append(kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        if self.erro:
            raise self.erro
        return self.resposta


def troca_cliente(monkeypatch, resposta=None, erro=None):
    ClienteFalso.criados = []
    monkeypatch.setattr(
        modulo.httpx,
        "Client",
        lambda **kw: ClienteFalso(resposta=resposta, erro=erro, **kw),
    )


def resposta(status=200, tipo="image/jpeg", tamanho=20000):
    return httpx.Response(
        status,
        content=b"x" * tamanho,
        headers={"content-type": tipo},
        request=httpx.Request("GET", URL),
    )


def test_foto_por_url_vai_em_bytes_com_a_legenda(monkeypatch):
    troca_cliente(monkeypatch, resposta())
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, URL, DESTINO)
    assert falso.bytes_enviados == [(b"x" * 20000, LEGENDA, DESTINO)]
    assert falso.textos == []


def test_baixa_com_o_nosso_user_agent_e_timeout_de_10s(monkeypatch):
    troca_cliente(monkeypatch, resposta())
    Evolution.send_card(Falso(), LEGENDA, URL, DESTINO)
    (kwargs,) = ClienteFalso.criados
    assert kwargs["timeout"] == 10.0
    assert kwargs["follow_redirects"] is True
    assert kwargs["headers"]["User-Agent"] == MEDIA_USER_AGENT


@pytest.mark.parametrize(
    "resp",
    [
        resposta(tipo="image/gif", tamanho=43),
        resposta(tipo="text/html", tamanho=30000),
        resposta(tamanho=500),
        resposta(status=403),
    ],
    ids=["pixel", "html", "pequena", "http403"],
)
def test_foto_invalida_vira_texto(monkeypatch, resp):
    troca_cliente(monkeypatch, resp)
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, URL, DESTINO)
    assert falso.textos == [(LEGENDA, DESTINO)]
    assert falso.bytes_enviados == []


def test_gif_grande_tambem_vira_texto(monkeypatch):
    troca_cliente(monkeypatch, resposta(tipo="image/gif", tamanho=20000))
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, URL, DESTINO)
    assert falso.textos == [(LEGENDA, DESTINO)]


def test_falha_de_rede_vira_texto(monkeypatch):
    troca_cliente(monkeypatch, erro=httpx.ConnectError("sem rede"))
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, URL, DESTINO)
    assert falso.textos == [(LEGENDA, DESTINO)]
    assert falso.bytes_enviados == []


def test_foto_em_arquivo_local_vai_em_bytes(tmp_path):
    arquivo = tmp_path / "foto.jpg"
    arquivo.write_bytes(b"y" * 20000)
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, str(arquivo), DESTINO)
    assert falso.bytes_enviados == [(b"y" * 20000, LEGENDA, DESTINO)]
    assert falso.textos == []


def test_arquivo_local_que_sumiu_vira_texto(tmp_path):
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, str(tmp_path / "nao-existe.jpg"), DESTINO)
    assert falso.textos == [(LEGENDA, DESTINO)]
    assert falso.bytes_enviados == []


def test_sem_foto_manda_so_o_texto_sem_baixar(monkeypatch):
    troca_cliente(monkeypatch, resposta())
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, None, DESTINO)
    assert falso.textos == [(LEGENDA, DESTINO)]
    assert ClienteFalso.criados == []


def test_instancia_caida_nao_vira_texto(monkeypatch):
    troca_cliente(monkeypatch, resposta())
    falso = Falso(erro_no_envio=NotConnected("caiu"))
    with pytest.raises(NotConnected):
        Evolution.send_card(falso, LEGENDA, URL, DESTINO)
    assert falso.textos == []


def test_nao_espera_entre_tentativas(monkeypatch):
    def proibido(*args):
        raise AssertionError("time.sleep nao deveria ser chamado")

    monkeypatch.setattr(modulo.time, "sleep", proibido)
    troca_cliente(monkeypatch, erro=httpx.ConnectError("sem rede"))
    falso = Falso()
    Evolution.send_card(falso, LEGENDA, URL, DESTINO)
    assert falso.textos == [(LEGENDA, DESTINO)]

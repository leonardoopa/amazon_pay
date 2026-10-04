"""Entrega direto no grupo pela Evolution API.

O que precisa valer aqui: o payload sai no formato que a Evolution espera
(campo `number` recebendo JID de grupo, nao numero de telefone), a queda da
instancia e distinguivel de um erro qualquer, e a escolha do backend nao
vaza pro resto do pipeline.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.delivery.evolution import (
    CAPTION_LIMIT,
    Evolution,
    NotConnected,
)  # noqa: E402

GRUPO = "120363295648424210@g.us"
FOTO = "https://http2.mlstatic.com/foto-F.jpg"


class Cfg:
    base_url = "http://localhost:8080"
    api_key = "k"
    instance = "ofertas"
    group_jid = GRUPO


class FakeEvolution(Evolution):
    """Substitui so o _send, pra inspecionar o que iria pro gateway."""

    def __init__(self, falha_midia: Exception | None = None) -> None:
        super().__init__(Cfg())
        self.enviados: list[tuple[str, dict]] = []
        self.falha_midia = falha_midia

    def _send(self, path: str, payload: dict) -> dict:
        if path.endswith("sendMedia") and self.falha_midia:
            raise self.falha_midia
        self.enviados.append((path, payload))
        return {"ok": True}


def test_manda_imagem_com_legenda_numa_mensagem_so():
    evo = FakeEvolution()
    evo.send_post("Oferta boa", FOTO)

    assert len(evo.enviados) == 1
    path, payload = evo.enviados[0]
    assert path == "/message/sendMedia"
    assert payload["mediatype"] == "image"
    assert payload["media"] == FOTO
    assert payload["caption"] == "Oferta boa"


def test_envia_para_o_jid_do_grupo():
    """Grupo nao tem telefone: o campo `number` da Evolution recebe o JID."""
    evo = FakeEvolution()
    evo.send_post("Oferta boa", FOTO)

    assert evo.enviados[0][1]["number"] == GRUPO


def test_sem_imagem_cai_pra_texto():
    evo = FakeEvolution()
    evo.send_post("Oferta boa")

    path, payload = evo.enviados[0]
    assert path == "/message/sendText"
    assert payload["text"] == "Oferta boa"


def test_texto_pede_preview_do_link():
    """Sem o card do produto o post parece spam de link solto."""
    evo = FakeEvolution()
    evo.send_post("Oferta boa")

    assert evo.enviados[0][1]["linkPreview"] is True


def test_legenda_longa_demais_vira_texto():
    """Acima do teto a legenda seria cortada -- e o corte come justo o final,
    onde ficam o link e a divulgacao de afiliado."""
    evo = FakeEvolution()
    evo.send_post("x" * (CAPTION_LIMIT + 1), FOTO)

    assert evo.enviados[0][0] == "/message/sendText"


def test_midia_recusada_nao_perde_a_oferta():
    evo = FakeEvolution(falha_midia=RuntimeError("URL invalida"))
    evo.send_post("Oferta boa", FOTO)

    assert evo.enviados[0][0] == "/message/sendText"


def test_instancia_caida_nao_vira_fallback_de_texto():
    """Reparear exige QR na mao. Cair pra texto so mandaria a segunda mensagem
    pro mesmo lugar inexistente e marcaria a tentativa a toa."""
    evo = FakeEvolution(falha_midia=NotConnected("caiu"))

    with pytest.raises(NotConnected):
        evo.send_post("Oferta boa", FOTO)

    assert evo.enviados == []


def test_backend_invalido_falha_com_nome():
    from promo.delivery import build_delivery

    import os

    anterior = os.environ.get("DELIVERY_BACKEND")
    os.environ["DELIVERY_BACKEND"] = "baileys"
    try:
        with pytest.raises(ValueError, match="baileys"):
            build_delivery()
    finally:
        if anterior is None:
            del os.environ["DELIVERY_BACKEND"]
        else:
            os.environ["DELIVERY_BACKEND"] = anterior


def test_baixar_a_foto_por_conta_propria_diz_quem_somos(monkeypatch):
    """A CDN do Pelando devolve 403 para o User-Agent padrao do httpx."""
    import httpx

    from promo.delivery import evolution as modulo

    vistos: list[str] = []
    real = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        vistos.append(request.headers["user-agent"])
        return httpx.Response(200, content=b"RIFF....WEBP")

    monkeypatch.setattr(
        modulo.httpx,
        "Client",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    evo = FakeEvolution()

    evo.send_image_bytes("https://media.pelando.com.br/x.jpg", "Oferta")

    assert vistos == [modulo.MEDIA_USER_AGENT]
    assert "promo-bot" in vistos[0]
    path, payload = evo.enviados[0]
    assert path == "/message/sendMedia" and payload["mediatype"] == "image"

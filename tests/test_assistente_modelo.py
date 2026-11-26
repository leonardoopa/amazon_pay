"""O assistente usa um Gemini proprio, para nao disputar a cota do grupo."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.assistente.conversa import Assistente, Pedido  # noqa: E402
from promo.config import assistente_gemini_modelo  # noqa: E402


class Resp:
    def __init__(self, parsed):
        self.parsed = parsed
        self.text = parsed.model_dump_json()


class GeminiGravador:
    """Grava o `model` que chegou em generate_content."""

    def __init__(self):
        self.models = self
        self.modelos: list[str] = []

    def generate_content(self, model, contents, config):
        self.modelos.append(model)
        return Resp(Pedido(consulta="ps5"))


def test_assistente_usa_o_modelo_proprio_por_padrao(monkeypatch):
    monkeypatch.delenv("ASSISTENTE_GEMINI_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    gemini = GeminiGravador()

    Assistente(None, client=gemini).entender("tem ps5?")

    assert gemini.modelos == ["gemini-3.1-flash-lite"]


def test_modelo_do_assistente_vem_do_env(monkeypatch):
    monkeypatch.setenv("ASSISTENTE_GEMINI_MODEL", "x")

    assert assistente_gemini_modelo() == "x"

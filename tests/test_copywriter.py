"""Garante que os fatos passados ao Gemini batem com o que foi calculado."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.copywriter import SYSTEM, Copywriter, _facts, fallback_copy  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402

LINK = "https://produto.mercadolivre.com.br/MLB-123?matt_tool=1"


def make_scored(**kwargs) -> ScoredOffer:
    offer = Offer(
        source="mercadolivre",
        external_id="MLB123",
        title="Fone Bluetooth XYZ",
        price=150.0,
        url="https://produto.mercadolivre.com.br/MLB-123",
        free_shipping=kwargs.pop("free_shipping", False),
    )
    defaults = dict(baseline=200.0, discount_pct=25.0, observations=10, lowest_ever=False)
    return ScoredOffer(offer=offer, **{**defaults, **kwargs})


class FakeModels:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text)


class FakeClient:
    def __init__(self, text: str) -> None:
        self.models = FakeModels(text)


def test_facts_carregam_os_numeros_reais():
    facts = _facts(make_scored(), LINK)

    assert "R$ 150.00" in facts
    assert "R$ 200.00" in facts
    assert "25%" in facts
    assert LINK in facts
    assert "Mercado Livre" in facts


def test_facts_sinalizam_menor_preco_e_frete():
    facts = _facts(make_scored(lowest_ever=True, free_shipping=True), LINK)

    assert "menor preco" in facts
    assert "Frete gratis." in facts


def test_facts_omitem_flags_quando_falsas():
    facts = _facts(make_scored(), LINK)

    assert "menor preco" not in facts
    assert "Frete gratis." not in facts


def test_write_envia_system_instruction_e_devolve_texto():
    client = FakeClient("  Achei um bom preco\nFone XYZ  ")
    copy = Copywriter(client=client).write(make_scored(), LINK)

    assert copy == "Achei um bom preco\nFone XYZ"
    call = client.models.calls[0]
    assert call["config"].system_instruction == SYSTEM
    assert LINK in call["contents"]


def test_write_recusa_resposta_vazia():
    """Filtro de seguranca do Gemini pode zerar a resposta -- nao pode virar post."""
    copywriter = Copywriter(client=FakeClient(""))

    with pytest.raises(RuntimeError, match="vazia"):
        copywriter.write(make_scored(), LINK)


def test_fallback_tem_disclosure_e_numeros():
    text = fallback_copy(make_scored(lowest_ever=True), LINK)

    assert "*R$ 150.00*" in text
    assert "25%" in text
    assert LINK in text
    assert text.endswith("Link de afiliado - o preco pra voce nao muda.")

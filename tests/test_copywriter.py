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


AMAZON_DISCLOSURE = (
    "Como participante do Programa de Associados da Amazon, "
    "sou remunerado pelas compras qualificadas efetuadas"
)


def make_scored(**kwargs) -> ScoredOffer:
    offer = Offer(
        source=kwargs.pop("source", "mercadolivre"),
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
        self.finish_reason = "STOP"
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=self.text,
            candidates=[SimpleNamespace(finish_reason=self.finish_reason)],
        )


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

    assert copy.startswith("Achei um bom preco\nFone XYZ")
    call = client.models.calls[0]
    assert call["config"].system_instruction == SYSTEM
    assert LINK in call["contents"]


def test_write_recusa_resposta_truncada():
    """Modelo com thinking estoura o orcamento e devolve post sem preco/link."""
    client = FakeClient("Que achado esse fone! 🎧\nJBL Tune 52")
    client.models.finish_reason = "MAX_TOKENS"

    with pytest.raises(RuntimeError, match="truncou"):
        Copywriter(client=client).write(make_scored(), LINK)


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


# --- Divulgacao obrigatoria (Clausula 5 do Contrato de Associados) ---
#
# A frase da Amazon e literal por contrato, e violar a Clausula 5 conta como
# descumprimento material. Por isso ela e testada caractere por caractere.


def test_amazon_usa_a_frase_exata_do_contrato():
    text = fallback_copy(make_scored(source="amazon"), LINK)

    assert text.endswith(AMAZON_DISCLOSURE)


def test_facts_mandam_a_divulgacao_da_loja_certa():
    assert AMAZON_DISCLOSURE in _facts(make_scored(source="amazon"), LINK)
    assert AMAZON_DISCLOSURE not in _facts(make_scored(), LINK)


def test_disclosure_e_reposta_se_o_modelo_parafrasear():
    """LLM nao e confiavel para reproduzir texto legal -- o codigo garante."""
    client = FakeClient("Achei um bom preco\nRelogio Seiko\nsou remunerado pelas compras")
    copy = Copywriter(client=client).write(make_scored(source="amazon"), LINK)

    assert copy.endswith(AMAZON_DISCLOSURE)


def test_disclosure_nao_e_duplicada_quando_o_modelo_acerta():
    client = FakeClient(f"Achei um bom preco\nRelogio Seiko\n{LINK}\n{AMAZON_DISCLOSURE}")
    copy = Copywriter(client=client).write(make_scored(source="amazon"), LINK)

    assert copy.count(AMAZON_DISCLOSURE) == 1

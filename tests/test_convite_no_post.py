"""A assinatura de convite no fim do post.

Post de grupo circula por encaminhamento, e quem recebe encaminhado nao tem
como entrar. A assinatura fecha esse buraco. O que os testes protegem e a
ordem: a divulgacao de afiliado tem que continuar colada no link que ela
identifica (CDC art. 36), com o convite DEPOIS -- e nunca duplicado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import group_invite_url  # noqa: E402
from promo.copywriter import (  # noqa: E402
    CHAMADA_DO_GRUPO,
    DISCLOSURES,
    com_convite,
    disclosure_for,
    fallback_copy,
)
from promo.models import Offer, ScoredOffer  # noqa: E402

CONVITE = "https://comunidadedodesconto.com.br/entrar/?de=post"
DISCLOSURE_ML = DISCLOSURES["mercadolivre"]

POST = f"""QUEM AUTORIZOU ESSE PRECO?

Fone Bluetooth XYZ

De R$ 200,00 por *R$ 150,00*

https://mercadolivre.com/sec/abc123
{DISCLOSURE_ML}"""


def make_scored() -> ScoredOffer:
    offer = Offer(
        source="mercadolivre",
        external_id="MLB123",
        title="Fone Bluetooth XYZ",
        price=150.0,
        url="https://produto.mercadolivre.com.br/MLB-123",
    )
    return ScoredOffer(
        offer=offer,
        baseline=200.0,
        discount_pct=25.0,
        observations=10,
        lowest_ever=False,
    )


def test_o_convite_entra_no_fim_do_post():
    saida = com_convite(POST, CONVITE)

    assert saida.endswith(f"{CHAMADA_DO_GRUPO}\n{CONVITE}")


def test_a_divulgacao_continua_colada_no_link():
    """O convite nao pode empurrar a divulgacao para longe do link.

    Quem le confere a identificacao no mesmo lugar onde clica. Se o convite
    entrasse no meio, a divulgacao passaria a identificar o link errado.
    """
    linhas = com_convite(POST, CONVITE).splitlines()
    posicao = linhas.index(DISCLOSURE_ML)

    assert linhas[posicao - 1].startswith("https://mercadolivre.com/sec/")
    assert linhas.index(CONVITE) > posicao


def test_chamar_duas_vezes_nao_duplica():
    uma = com_convite(POST, CONVITE)

    assert com_convite(uma, CONVITE) == uma


def test_sem_url_o_post_sai_intacto():
    assert com_convite(POST, "") == POST


def test_espaco_em_branco_conta_como_desligado():
    """`GROUP_INVITE_URL=  ` no .env nao pode virar uma linha vazia no post."""
    assert com_convite(POST, "   ") == POST


def test_o_fallback_tambem_recebe_o_convite():
    """O texto sem IA sai no grupo igual ao do Gemini.

    O fallback entra justamente nas rodadas em que o Gemini falha, que sao
    imprevisiveis -- se ele saisse sem convite, o canal de crescimento sumiria
    sem ninguem notar.
    """
    texto = com_convite(fallback_copy(make_scored(), "https://ml.com/sec/x"), CONVITE)

    assert texto.endswith(CONVITE)
    assert disclosure_for("mercadolivre") in texto


def test_o_convite_vem_do_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("GROUP_INVITE_URL", CONVITE)

    assert group_invite_url() == CONVITE
    assert com_convite(POST).endswith(CONVITE)


def test_o_post_manual_tambem_assina(monkeypatch: pytest.MonkeyPatch):
    """O `promo amazon-add` sai no mesmo grupo e tem que sair no mesmo formato."""
    monkeypatch.setenv("GROUP_INVITE_URL", CONVITE)
    from promo.manual import escrever_texto

    assert escrever_texto(make_scored(), usar_ia=False).endswith(CONVITE)


def test_sem_env_a_assinatura_fica_desligada(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GROUP_INVITE_URL", raising=False)

    assert group_invite_url() == ""
    assert com_convite(POST) == POST

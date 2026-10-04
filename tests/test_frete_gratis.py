"""A vitrine diz se o frete e gratis, e a gente nao estava lendo.

O campo `free_shipping` existe no modelo desde o inicio e o copywriter ja sabe
escrever "Frete gratis" a partir dele -- mas so a fonte de CATALOGO o
preenchia. Como a vitrine e 89% do que sai no grupo, quase nenhum post podia
mencionar o frete, inclusive os que o tinham.

Medido na vitrine em 06/09/2026, sobre 144 cards de tres paginas:

    componente shipping_v2 presente   144 de 144
    com frete gratis                  107        (74,3%)
    enviado pelo FULL                 105        (72,9%)

As chaves vistas foram `free_shipping_single` (105), `next_day_free_shipping`
(1) e `same_day_free_shipping` (1). O casamento e por SUBSTRING: a primeira tem
sufixo depois, as outras duas tem prefixo antes, entao nem `startswith` nem
`endswith` cobre as tres -- foi assim que a primeira versao disto passou nos
dois casos raros e falhou justamente nos 105.

O texto visivel nao serve de chave: vem localizado ("Frete gratis") e mudaria
com o idioma da resposta.

Na fila o frete DESEMPATA, e nao ordena -- entra depois do desconto. Com 74,3%
de cobertura ele seria um filtro que corta um quarto do volume, e o que se quer
e escolher entre duas ofertas ja equivalentes. O custo do frete e justamente o
que some do preco anunciado na hora do checkout.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.sources.ml_ofertas import _frete_gratis, _offer  # noqa: E402


def card(shipping: dict | None = None, **extra) -> dict:
    """Card minimo da vitrine, no formato que o payload real usa."""
    componentes = [
        {"type": "title", "title": {"text": "Produto de Teste"}},
        {"type": "price", "price": {"current_price": {"value": 99.9}}},
    ]
    if shipping is not None:
        componentes.append(shipping)
    return {
        "metadata": {"id": "MLB1", "url": "www.mercadolivre.com.br/MLB1"},
        "components": componentes,
        **extra,
    }


def shipping_v2(*keys: str) -> dict:
    return {
        "type": "shipping_v2",
        "id": "shipping_v2",
        "shipping_v2": [
            {
                "version": "1",
                "text": "{%s}" % (keys[0] if keys else ""),
                "values": [
                    {"type": "label", "key": k, "label": {"text": "Frete gratis"}}
                    for k in keys
                ],
            }
        ],
    }


# ---------- as tres variacoes vistas na vitrine ----------


def test_free_shipping_single():
    assert _frete_gratis(card(shipping_v2("free_shipping_single"))) is True


def test_next_day_free_shipping():
    assert _frete_gratis(card(shipping_v2("next_day_free_shipping"))) is True


def test_same_day_free_shipping():
    assert _frete_gratis(card(shipping_v2("same_day_free_shipping"))) is True


def test_uma_quarta_variacao_qualquer_tambem_passa():
    """O casamento e por substring justamente para nao precisar de release
    quando o ML inventar a proxima."""
    assert _frete_gratis(card(shipping_v2("weekend_free_shipping"))) is True


# ---------- o que NAO e frete gratis ----------


def test_sem_o_componente_shipping_e_falso():
    assert _frete_gratis(card()) is False


def test_componente_vazio_e_falso():
    assert _frete_gratis(card({"type": "shipping_v2", "shipping_v2": []})) is False


def test_frete_pago_e_falso():
    """O card do frete pago existe, mas com outra chave."""
    assert _frete_gratis(card(shipping_v2("shipping_cost"))) is False


def test_o_icone_do_full_sozinho_nao_e_frete_gratis():
    """FULL e o deposito do ML, nao o preco do frete. Sao 72,9% e 74,3% --
    numeros parecidos, coisas diferentes."""
    so_full = {
        "type": "shipping_v2",
        "shipping_v2": [
            {
                "values": [
                    {
                        "type": "icon",
                        "key": "full_icon",
                        "icon": {"key": "vpp_full_icon"},
                    }
                ]
            }
        ],
    }

    assert _frete_gratis(card(so_full)) is False


def test_o_texto_visivel_nao_serve_de_chave():
    """O rotulo vem localizado; casar por ele quebraria se a resposta viesse
    em outro idioma."""
    mentiroso = {
        "type": "shipping_v2",
        "shipping_v2": [
            {
                "values": [
                    {
                        "type": "label",
                        "key": "shipping_cost",
                        "label": {"text": "Frete gratis"},
                    }
                ]
            }
        ],
    }

    assert _frete_gratis(card(mentiroso)) is False


# ---------- o campo chega na Offer ----------


def test_a_oferta_da_vitrine_carrega_o_frete():
    offer = _offer(card(shipping_v2("free_shipping_single")))

    assert offer is not None
    assert offer.free_shipping is True


def test_a_oferta_sem_frete_gratis_fica_falsa():
    offer = _offer(card())

    assert offer is not None
    assert offer.free_shipping is False


# ---------- desempate na fila ----------


def oferta(external_id: str, desconto: float, frete: bool) -> ScoredOffer:
    return ScoredOffer(
        offer=Offer(
            source="ml_ofertas",
            external_id=external_id,
            title=f"Produto {external_id}",
            price=100.0,
            url=f"https://exemplo/{external_id}",
            free_shipping=frete,
        ),
        baseline=200.0,
        discount_pct=desconto,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


def ordenar(ofertas: list[ScoredOffer]) -> list[str]:
    ofertas = list(ofertas)
    ofertas.sort(key=lambda s: (s.discount_pct, s.offer.free_shipping), reverse=True)
    return [s.offer.external_id for s in ofertas]


def test_no_empate_o_frete_gratis_ganha():
    assert ordenar([oferta("A", 30.0, False), oferta("B", 30.0, True)]) == ["B", "A"]


def test_o_desconto_ainda_manda():
    """Frete desempata, e nao ordena: 40% sem frete continua na frente de 30%
    com frete."""
    assert ordenar([oferta("A", 30.0, True), oferta("B", 40.0, False)]) == ["B", "A"]

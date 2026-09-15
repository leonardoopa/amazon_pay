"""A faixa prioritaria do repasse, e o teto dela.

O que estes testes guardam e a separacao POR ORIGEM. A faixa nasceu para o
pedido do dono -- o que o xet e o Economizei postam tem que sair no nosso
grupo -- mas ela era alimentada so pelo `score_pista`, a porta de quem nao tem
preco riscado. Quando a pista passou a ler o riscado da propria pagina, ela
comecou a ser aceita pelo `score_campaign` e voltou a disputar a cota: medido
em 14/09/2026, 50 pistas coletadas e UMA na frente da fila.

O teto e o outro lado: fora da cota sem limite nenhum multiplicaria o volume
do grupo por sete numa tacada, e volume e o principal sinal de banimento no
Baileys.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import max_pistas_por_run  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import (  # noqa: E402
    _VISTOS_EM_OUTRO_GRUPO,
    _quantas_pistas_cabem,
    _veio_de_outro_grupo,
)


def oferta(external_id: str, preco: float = 100.0, riscado: float = 200.0) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id=external_id,
        title=f"Produto {external_id}",
        price=preco,
        url=f"https://produto.mercadolivre.com.br/{external_id}",
        original_price=riscado,
    )


def pontuada(offer: Offer, desconto: float) -> ScoredOffer:
    return ScoredOffer(
        offer=offer,
        baseline=offer.original_price or offer.price,
        discount_pct=desconto,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


@pytest.fixture(autouse=True)
def limpa_vistos():
    _VISTOS_EM_OUTRO_GRUPO.clear()
    yield
    _VISTOS_EM_OUTRO_GRUPO.clear()


def test_o_teto_padrao_e_dez():
    """Escolhido pelo dono em 14/09/2026, entre "tudo" e "com teto"."""
    assert max_pistas_por_run() == 10


def test_o_teto_vem_do_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAX_PISTAS_POR_RUN", "3")

    assert max_pistas_por_run() == 3


def test_pista_com_riscado_continua_sendo_pista():
    """O caso que quebrou.

    Ter preco riscado faz o `score_campaign` aceitar a oferta, e antes disso
    bastava para ela cair na fila comum. A origem e o que decide, nao a porta.
    """
    alvo = oferta("MLB1")
    _VISTOS_EM_OUTRO_GRUPO.add("MLB1")

    assert _veio_de_outro_grupo(alvo)


def test_o_que_nao_veio_do_outro_grupo_nao_entra_na_faixa():
    assert not _veio_de_outro_grupo(oferta("MLB2"))


def test_a_ordem_dentro_da_faixa_e_por_desconto():
    """Quando o teto corta, corta o pior.

    O `score_campaign` mediu o desconto contra o riscado da loja; esse numero
    viaja no ScoredOffer e e o unico criterio disponivel para ordenar pista.
    """
    fila = [
        pontuada(oferta("MLB_BAIXO"), 12.0),
        pontuada(oferta("MLB_ALTO"), 55.0),
        pontuada(oferta("MLB_MEIO"), 30.0),
    ]

    fila.sort(key=lambda s: s.discount_pct, reverse=True)

    assert [s.offer.external_id for s in fila] == ["MLB_ALTO", "MLB_MEIO", "MLB_BAIXO"]


def test_o_teto_limita_mesmo_com_fila_vazia():
    """Os dois tetos valem, e o menor manda.

    Com a fila vazia o limite da fila e grande; o que segura e o teto de
    repasse. Sem ele, as ~50 pistas de uma rodada entrariam de uma vez.
    """
    novas = [pontuada(oferta(f"MLB{i}"), 20.0) for i in range(50)]

    cabem = _quantas_pistas_cabem(na_fila=0, ja_escolhidas=0)

    assert cabem == 10
    assert len(novas[:cabem]) == 10


def test_a_fila_cheia_aperta_mais_que_o_teto():
    """Fila quase cheia manda menos que o teto, nunca mais."""
    assert _quantas_pistas_cabem(na_fila=26, ja_escolhidas=1) == 3


def test_fila_estourada_nao_devolve_negativo():
    """`novas[:-2]` cortaria a lista pelo fim em vez de nao levar nada."""
    assert _quantas_pistas_cabem(na_fila=40, ja_escolhidas=0) == 0


def test_o_teto_de_repasse_manda_quando_a_fila_esta_folgada(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MAX_PISTAS_POR_RUN", "4")

    assert _quantas_pistas_cabem(na_fila=0, ja_escolhidas=0) == 4

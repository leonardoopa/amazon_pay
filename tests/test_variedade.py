"""As vagas da rodada se dividem entre as fontes.

Ordenar so por desconto dava as cinco vagas a vitrine em toda rodada -- e nao
por ela ter oferta melhor. O desconto do repasse e medido contra o preco
riscado, que quem escolhe e a loja: quanto mais inflado o "de", mais alto o
produto subia na fila. A ordenacao premiava justamente a pratica que a landing
acusa, e o grupo recebia cinco eletronicos enquanto perfume e roupa, ja no
catalogo e com desconto de sobra, nunca chegavam.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import _intercalar_por_fonte  # noqa: E402


def oferta(fonte: str, external_id: str, desconto: float) -> ScoredOffer:
    return ScoredOffer(
        offer=Offer(
            source=fonte,
            external_id=external_id,
            title=f"Produto {external_id}",
            price=100.0,
            url=f"https://exemplo/{external_id}",
        ),
        baseline=200.0,
        discount_pct=desconto,
        observations=9,
        lowest_ever=False,
        verified=True,
    )


def fontes(saida) -> list[str]:
    return [s.offer.source for s in saida]


def test_uma_fonte_nao_leva_todas_as_vagas():
    """O caso medido em producao: cinco da vitrine, zero do catalogo."""
    candidatos = [oferta("ml_ofertas", f"V{i}", 45.0) for i in range(5)]
    candidatos += [oferta("mercadolivre", f"C{i}", 20.0) for i in range(5)]

    saida = _intercalar_por_fonte(candidatos, 5)

    assert fontes(saida).count("mercadolivre") == 2
    assert fontes(saida).count("ml_ofertas") == 3


def test_a_melhor_de_cada_fonte_vem_primeiro():
    """Rodizio nao pode virar sorteio: dentro da fonte, a ordem e mantida."""
    candidatos = [
        oferta("ml_ofertas", "V1", 45.0),
        oferta("ml_ofertas", "V2", 30.0),
        oferta("mercadolivre", "C1", 25.0),
        oferta("mercadolivre", "C2", 18.0),
    ]

    saida = _intercalar_por_fonte(candidatos, 4)

    assert [s.offer.external_id for s in saida] == ["V1", "C1", "V2", "C2"]


def test_fonte_unica_se_comporta_como_o_corte_simples():
    candidatos = [oferta("ml_ofertas", f"V{i}", 40.0 - i) for i in range(8)]

    saida = _intercalar_por_fonte(candidatos, 5)

    assert [s.offer.external_id for s in saida] == ["V0", "V1", "V2", "V3", "V4"]


def test_fonte_curta_nao_deixa_vaga_vazia():
    """Diversidade nao pode custar volume: acabou uma fonte, a outra completa."""
    candidatos = [oferta("ml_ofertas", f"V{i}", 40.0) for i in range(5)]
    candidatos += [oferta("mercadolivre", "C1", 30.0)]

    saida = _intercalar_por_fonte(candidatos, 5)

    assert len(saida) == 5
    assert fontes(saida).count("mercadolivre") == 1


def test_tres_fontes_dividem_as_vagas():
    candidatos = [
        oferta("ml_ofertas", "V1", 40.0),
        oferta("ml_ofertas", "V2", 39.0),
        oferta("mercadolivre", "C1", 30.0),
        oferta("mercadolivre", "C2", 29.0),
        oferta("amazon", "A1", 20.0),
    ]

    saida = _intercalar_por_fonte(candidatos, 5)

    assert set(fontes(saida)) == {"ml_ofertas", "mercadolivre", "amazon"}


def test_menos_candidatos_que_vagas_devolve_todos():
    candidatos = [oferta("ml_ofertas", "V1", 40.0), oferta("mercadolivre", "C1", 30.0)]

    assert len(_intercalar_por_fonte(candidatos, 5)) == 2


def test_limite_zero_nao_devolve_nada():
    assert _intercalar_por_fonte([oferta("ml_ofertas", "V1", 40.0)], 0) == []


def test_lista_vazia_nao_estoura():
    assert _intercalar_por_fonte([], 5) == []


# ---------- repasse nao e exclusividade da vitrine ----------


def test_catalogo_com_preco_riscado_vira_repasse():
    """A trava por fonte segurava 71 candidatos do catalogo.

    Ela foi escrita quando o catalogo quase nunca devolvia `original_price`.
    Hoje devolve em 39,5% dos produtos -- e enquanto a trava existiu, toda
    rodada saia com cinco itens da vitrine e nada de perfume, roupa ou
    suplemento, que ja estavam no catalogo com desconto de sobra.
    """
    import sqlite3

    from promo.config import Rules
    from promo.db import SCHEMA, record_offer
    from promo.scoring import score_campaign

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    perfume = Offer(
        source="mercadolivre",  # catalogo, nao a vitrine
        external_id="MLB99",
        title="Perfume Natura Una Brilho EDP 75ml",
        price=191.99,
        url="https://mercadolivre.com.br/MLB99",
        original_price=292.99,
    )
    record_offer(conn, perfume)

    regras = Rules(
        min_discount_pct=15.0,
        baseline_window_days=60,
        min_observations=4,
        repost_cooldown_days=14,
        max_offers_per_run=5,
    )
    scored = score_campaign(conn, perfume, regras)

    assert scored is not None
    assert scored.verified is False  # segue marcado como repasse, sem afirmar medicao
    assert 34 < scored.discount_pct < 35


def test_repasse_sem_preco_riscado_continua_recusado():
    """A protecao real nunca foi a fonte, e sim exigir o preco riscado."""
    import sqlite3

    from promo.config import Rules
    from promo.db import SCHEMA, record_offer
    from promo.scoring import score_campaign

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    sem_riscado = Offer(
        source="mercadolivre",
        external_id="MLB98",
        title="Produto sem De/Por",
        price=100.0,
        url="https://mercadolivre.com.br/MLB98",
    )
    record_offer(conn, sem_riscado)

    regras = Rules(
        min_discount_pct=15.0,
        baseline_window_days=60,
        min_observations=4,
        repost_cooldown_days=14,
        max_offers_per_run=5,
    )

    assert score_campaign(conn, sem_riscado, regras) is None

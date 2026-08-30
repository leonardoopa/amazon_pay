"""Produto caro nao vai para o grupo.

O teto nao julga a oferta -- uma queda real num notebook de R$ 3.000 continua
sendo real. Ele julga o publico: quem le grupo de oferta decide um item de
R$ 100 na hora e um de R$ 1.000 em dias, e nesses dias sai do grupo, pesquisa
e compra por outro link.

Medido antes da mudanca: 26 dos 59 posts enviados passavam de R$ 500, e 15
deles de R$ 1.000.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import Rules  # noqa: E402
from promo.db import SCHEMA, record_offer  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.scoring import score_campaign  # noqa: E402


def regras() -> Rules:
    return Rules(
        min_discount_pct=15.0,
        baseline_window_days=60,
        min_observations=4,
        repost_cooldown_days=14,
        max_offers_per_run=5,
    )


def conexao() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def oferta(preco: float, riscado: float, external_id: str = "MLB1") -> Offer:
    return Offer(
        source="mercadolivre",
        external_id=external_id,
        title="Produto",
        price=preco,
        url=f"https://mercadolivre.com.br/{external_id}",
        original_price=riscado,
    )


def test_acima_do_teto_e_recusado(monkeypatch):
    monkeypatch.setenv("MAX_PRICE", "300")
    conn = conexao()
    caro = oferta(1999.00, 3999.00)
    record_offer(conn, caro)

    assert score_campaign(conn, caro, regras()) is None


def test_abaixo_do_teto_passa(monkeypatch):
    monkeypatch.setenv("MAX_PRICE", "300")
    conn = conexao()
    barato = oferta(78.90, 129.90)
    record_offer(conn, barato)

    assert score_campaign(conn, barato, regras()) is not None


def test_exatamente_no_teto_passa(monkeypatch):
    """O teto e inclusivo: R$ 300 com MAX_PRICE=300 ainda e para este grupo."""
    monkeypatch.setenv("MAX_PRICE", "300")
    conn = conexao()
    no_limite = oferta(300.00, 500.00)
    record_offer(conn, no_limite)

    assert score_campaign(conn, no_limite, regras()) is not None


def test_zero_desliga_o_teto(monkeypatch):
    """Sem MAX_PRICE o comportamento e o de antes: nada e recusado por preco."""
    monkeypatch.setenv("MAX_PRICE", "0")
    conn = conexao()
    caro = oferta(1999.00, 3999.00)
    record_offer(conn, caro)

    assert score_campaign(conn, caro, regras()) is not None


def test_ausente_desliga_o_teto(monkeypatch):
    monkeypatch.delenv("MAX_PRICE", raising=False)
    conn = conexao()
    caro = oferta(1999.00, 3999.00)
    record_offer(conn, caro)

    assert score_campaign(conn, caro, regras()) is not None

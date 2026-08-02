"""Testes do filtro de desconto -- o coracao do projeto."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import Rules  # noqa: E402
from promo.db import SCHEMA  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.scoring import score  # noqa: E402

RULES = Rules(
    min_discount_pct=15,
    baseline_window_days=60,
    min_observations=7,
    repost_cooldown_days=14,
    max_offers_per_run=5,
)


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    yield connection
    connection.close()


def make_offer(price: float, **kwargs) -> Offer:
    defaults = dict(
        source="mercadolivre",
        external_id="MLB123",
        title="Fone Bluetooth XYZ",
        url="https://produto.mercadolivre.com.br/MLB-123",
    )
    return Offer(price=price, **{**defaults, **kwargs})


def seed_history(conn: sqlite3.Connection, prices: list[float], product_id: str = "mercadolivre:MLB123") -> None:
    """Grava um preco por dia, terminando ontem."""
    conn.execute(
        """INSERT OR IGNORE INTO products
           (id, source, external_id, title, url, first_seen_at, last_seen_at)
           VALUES (?, 'mercadolivre', 'MLB123', 't', 'u', '2026-01-01', '2026-01-01')""",
        (product_id,),
    )
    today = datetime.now(UTC).date()
    for index, price in enumerate(reversed(prices), start=1):
        conn.execute(
            "INSERT INTO price_history (product_id, observed_on, price) VALUES (?, ?, ?)",
            (product_id, (today - timedelta(days=index)).isoformat(), price),
        )


def test_desconto_real_vira_post(conn):
    seed_history(conn, [200.0] * 10)
    scored = score(conn, make_offer(150.0), RULES)

    assert scored is not None
    assert scored.baseline == 200.0
    assert scored.discount_pct == pytest.approx(25.0)
    assert scored.lowest_ever is True


def test_historico_curto_nao_posta(conn):
    seed_history(conn, [200.0] * 6)  # abaixo de min_observations
    assert score(conn, make_offer(100.0), RULES) is None


def test_desconto_fake_e_ignorado(conn):
    """Preco inflado na vespera nao engana: a mediana segura a baseline."""
    seed_history(conn, [100.0] * 9 + [300.0])
    offer = make_offer(180.0, original_price=300.0)  # "40% OFF" segundo a loja

    assert score(conn, offer, RULES) is None


def test_desconto_abaixo_do_minimo(conn):
    seed_history(conn, [200.0] * 10)
    assert score(conn, make_offer(180.0), RULES) is None  # 10% < 15%


def test_produto_indisponivel(conn):
    seed_history(conn, [200.0] * 10)
    assert score(conn, make_offer(100.0, available=False), RULES) is None


def test_cooldown_bloqueia_repost(conn):
    seed_history(conn, [200.0] * 10)
    conn.execute(
        """INSERT INTO posts (product_id, price, baseline, discount_pct, copy, status, created_at)
           VALUES ('mercadolivre:MLB123', 150.0, 200.0, 25.0, 'x', 'sent', ?)""",
        ((datetime.now(UTC) - timedelta(days=2)).isoformat(),),
    )
    assert score(conn, make_offer(148.0), RULES) is None


def test_cooldown_cede_para_queda_muito_maior(conn):
    seed_history(conn, [200.0] * 10)
    conn.execute(
        """INSERT INTO posts (product_id, price, baseline, discount_pct, copy, status, created_at)
           VALUES ('mercadolivre:MLB123', 150.0, 200.0, 25.0, 'x', 'sent', ?)""",
        ((datetime.now(UTC) - timedelta(days=2)).isoformat(),),
    )
    scored = score(conn, make_offer(100.0), RULES)  # 50% >= 25% + 10

    assert scored is not None
    assert scored.discount_pct == pytest.approx(50.0)


def test_preco_de_hoje_nao_entra_na_baseline(conn):
    """Se o preco de hoje contasse, a propria queda puxaria a mediana pra baixo."""
    seed_history(conn, [200.0] * 10)
    conn.execute(
        "INSERT INTO price_history (product_id, observed_on, price) VALUES (?, ?, ?)",
        ("mercadolivre:MLB123", datetime.now(UTC).date().isoformat(), 150.0),
    )
    scored = score(conn, make_offer(150.0), RULES)

    assert scored is not None
    assert scored.baseline == 200.0

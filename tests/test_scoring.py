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


def seed_history(
    conn: sqlite3.Connection,
    prices: list[float],
    product_id: str = "mercadolivre:MLB123",
) -> None:
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


# ---------- piso zero nao pode virar "sem desconto" ----------
#
# `PRIORITY_MIN_DISCOUNT_PCT=0` liberou o piso nos temas prioritarios, e isso
# estava certo para o repasse: `score_campaign` ainda exige preco riscado MAIOR
# que o atual, entao sempre ha alguma queda.
#
# No `score` a baseline e calculada, e nada impedia que ela fosse IGUAL ao
# preco. Custou um post real em 01/09/2026:
#
#     TESTO ESSENCIAL  R$ 56,77 (baseline 56,77)  -0%   marcado VERIFICADA
#
# Anunciar preco de sempre como oferta e o oposto do que a medicao existe para
# provar.

SEM_PISO = Rules(
    min_discount_pct=0,
    baseline_window_days=60,
    min_observations=3,
    repost_cooldown_days=3,
    max_offers_per_run=5,
)


def test_preco_igual_a_baseline_nao_e_oferta(conn):
    seed_history(conn, [100.0, 100.0, 100.0])

    assert score(conn, make_offer(100.0), SEM_PISO) is None


def test_preco_acima_da_baseline_nao_e_oferta(conn):
    seed_history(conn, [100.0, 100.0, 100.0])

    assert score(conn, make_offer(120.0), SEM_PISO) is None


def test_queda_minuscula_passa_com_piso_zero(conn):
    """Piso zero continua querendo dizer "qualquer queda" -- so nao "nenhuma"."""
    seed_history(conn, [100.0, 100.0, 100.0])

    scored = score(conn, make_offer(99.0), SEM_PISO)

    assert scored is not None and scored.discount_pct == 1.0


def test_o_piso_normal_continua_valendo(conn):
    regras = Rules(
        min_discount_pct=15,
        baseline_window_days=60,
        min_observations=3,
        repost_cooldown_days=3,
        max_offers_per_run=5,
    )
    seed_history(conn, [100.0, 100.0, 100.0])

    assert score(conn, make_offer(95.0), regras) is None
    assert score(conn, make_offer(80.0), regras) is not None


# ---------- porcentagem nao ve dinheiro ----------
#
# Dois posts reais em 02/09/2026, ambos passando o filtro:
#
#     Tasty Whey 3w Gourmet 912g   de R$ 239,00 por R$ 237,00   -0,8%
#     Perfume Club De Nuit Armaf   de R$ 197,34 por R$ 197,34    0,0%
#
# O segundo o `price >= baseline` ja pega. O primeiro nao: R$ 237 e menor que
# R$ 239, entao houve queda -- de DOIS REAIS num produto de duzentos e trinta.
# Piso em porcentagem nao enxerga isso, e com `PRIORITY_MIN_DISCOUNT_PCT=0`
# nos temas prioritarios ele nao enxerga nada.

DEZ_REAIS = Rules(
    min_discount_pct=0,
    baseline_window_days=60,
    min_observations=3,
    repost_cooldown_days=3,
    max_offers_per_run=5,
    min_discount_brl=10.0,
)


def test_dois_reais_de_queda_nao_e_oferta(conn):
    """O caso literal do Tasty Whey."""
    seed_history(conn, [239.0, 239.0, 239.0])

    assert score(conn, make_offer(237.0), DEZ_REAIS) is None


def test_queda_acima_do_piso_em_reais_passa(conn):
    seed_history(conn, [239.0, 239.0, 239.0])

    assert score(conn, make_offer(220.0), DEZ_REAIS) is not None


def test_o_piso_em_reais_tambem_vale_no_repasse(conn):
    """A vitrine tem o mesmo problema: riscado de R$ 239 por R$ 237 e queda de
    verdade pelo criterio antigo, e dois reais pelo criterio do leitor."""
    from promo.models import Offer
    from promo.scoring import score_campaign

    magra = Offer(
        source="ml_ofertas", external_id="MLB9", title="Whey",
        price=237.0, url="https://x", original_price=239.0,
    )
    gorda = Offer(
        source="ml_ofertas", external_id="MLB9", title="Whey",
        price=200.0, url="https://x", original_price=239.0,
    )

    assert score_campaign(conn, magra, DEZ_REAIS) is None
    assert score_campaign(conn, gorda, DEZ_REAIS) is not None


def test_o_piso_em_reais_e_zero_por_padrao(conn):
    """Quem nao configurar `MIN_DISCOUNT_BRL` nao muda de comportamento."""
    padrao = Rules(
        min_discount_pct=0,
        baseline_window_days=60,
        min_observations=3,
        repost_cooldown_days=3,
        max_offers_per_run=5,
    )
    seed_history(conn, [239.0, 239.0, 239.0])

    assert padrao.min_discount_brl == 0.0
    assert score(conn, make_offer(237.0), padrao) is not None


def test_produto_barato_com_desconto_alto_continua_passando(conn):
    """O piso em reais nao pode virar teto de preco pelo avesso: item de R$ 30
    a -40% economiza R$ 12 e e oferta boa."""
    seed_history(conn, [30.0, 30.0, 30.0])

    assert score(conn, make_offer(18.0), DEZ_REAIS) is not None

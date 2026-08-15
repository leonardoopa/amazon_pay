"""Descoberta periodica e variedade das chamadas.

Duas mudancas feitas pelo mesmo motivo: volume de post no grupo. Volume exige
carteira grande de produtos rastreados, carteira grande exige orcamento de API,
e o orcamento estava sendo gasto redescobrindo o mesmo top-10 doze vezes por
dia. E volume sem variedade de texto cansa o grupo mais rapido que silencio.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.db import (
    SCHEMA,
    create_post,
    hours_since,
    recent_headlines,
    set_meta,
)  # noqa: E402


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO products (id, source, external_id, title, url, first_seen_at,"
        " last_seen_at) VALUES ('mercadolivre:MLB1','mercadolivre','MLB1','P','u','x','x')"
    )
    return conn


# ---------- meta / descoberta ----------


def test_nunca_carimbado_e_none():
    """None significa 'primeira rodada' -- e a primeira tem que descobrir,
    senao nao ha carteira nenhuma pra reconsultar."""
    assert hours_since(make_conn(), "last_discovery_at") is None


def test_carimbo_recente_da_quase_zero():
    from promo.db import now

    conn = make_conn()
    set_meta(conn, "last_discovery_at", now().isoformat())

    assert hours_since(conn, "last_discovery_at") < 0.1


def test_carimbo_sem_fuso_nao_estoura():
    """datetime('now') do SQLite grava sem fuso. Antes isso levantava
    TypeError e derrubava a rodada inteira em vez de so pular a descoberta."""
    conn = make_conn()
    conn.execute("INSERT INTO meta (key, value) VALUES ('k', datetime('now'))")

    assert hours_since(conn, "k") < 1


def test_carimbo_antigo_passa_do_intervalo():
    from promo.db import now
    from datetime import timedelta

    conn = make_conn()
    set_meta(conn, "last_discovery_at", (now() - timedelta(hours=13)).isoformat())

    assert hours_since(conn, "last_discovery_at") > 12


# ---------- chamadas recentes ----------


def add_post(conn: sqlite3.Connection, copy: str) -> None:
    create_post(conn, "mercadolivre:MLB1", 10.0, 20.0, 50.0, copy, None)


def test_pega_so_a_primeira_linha():
    """O que se repete e a chamada, nao o corpo -- o corpo e template."""
    conn = make_conn()
    add_post(conn, "AIR FRYER POR 389 PILA\n\nProduto\n\nDe R$ 1,00 por *R$ 2,00*")

    assert recent_headlines(conn) == ["AIR FRYER POR 389 PILA"]


def test_ordena_do_mais_recente():
    conn = make_conn()
    add_post(conn, "PRIMEIRA\nresto")
    add_post(conn, "SEGUNDA\nresto")

    assert recent_headlines(conn)[0] == "SEGUNDA"


def test_respeita_o_limite():
    conn = make_conn()
    for i in range(20):
        add_post(conn, f"CHAMADA {i}\nresto")

    assert len(recent_headlines(conn, limit=5)) == 5


def test_inclui_pendentes():
    """Post na fila ja foi escrito: repetir a formula dele seria repeticao
    visivel assim que a fila drenar."""
    conn = make_conn()
    add_post(conn, "NA FILA\nresto")

    assert recent_headlines(conn) == ["NA FILA"]


def test_banco_sem_post_devolve_vazio():
    assert recent_headlines(make_conn()) == []


# ---------- fatia quente ----------
#
# Volume exige carteira grande; carteira grande nao cabe num ciclo de minutos.
# A saida e reconsultar so quem pode virar post logo: quem ja esta colado na
# propria minima historica.


def add_history(
    conn: sqlite3.Connection, external_id: str, precos: list[float]
) -> None:
    """Cria produto com uma serie de precos, o ultimo sendo o de hoje."""
    conn.execute(
        "INSERT OR IGNORE INTO products (id, source, external_id, title, url,"
        " first_seen_at, last_seen_at) VALUES (?,?,?,?,?,?,?)",
        (
            f"mercadolivre:{external_id}",
            "mercadolivre",
            external_id,
            "P",
            "u",
            "x",
            "x",
        ),
    )
    for dia, preco in enumerate(precos):
        conn.execute(
            "INSERT INTO price_history (product_id, observed_on, price, available)"
            " VALUES (?, date('now', ?), ?, 1)",
            (f"mercadolivre:{external_id}", f"-{len(precos) - dia - 1} days", preco),
        )


def test_produto_colado_na_minima_e_quente():
    from promo.db import hot_products

    conn = make_conn()
    add_history(conn, "MLB9", [100.0, 90.0, 91.0])  # 91 esta 1% acima da minima 90

    assert [p[0] for p in hot_products(conn, "mercadolivre", 10, 25)] == ["MLB9"]


def test_produto_longe_da_minima_fica_de_fora():
    """Quem esta 40% acima da propria minima nao vira oferta com queda pequena.
    Reconsultar ele a cada 15 min e gastar chamada por nada."""
    from promo.db import hot_products

    conn = make_conn()
    add_history(conn, "MLB9", [100.0, 50.0, 70.0])  # 70 esta 40% acima da minima

    assert hot_products(conn, "mercadolivre", 10, 25) == []


def test_margem_maior_inclui_mais_produtos():
    from promo.db import hot_products

    conn = make_conn()
    add_history(conn, "MLB9", [100.0, 50.0, 70.0])

    assert hot_products(conn, "mercadolivre", 10, 25) == []
    assert len(hot_products(conn, "mercadolivre", 50, 25)) == 1


def test_ordena_do_mais_colado_na_minima():
    """Se o teto cortar, cortar os menos promissores."""
    from promo.db import hot_products

    conn = make_conn()
    add_history(conn, "MLB_perto", [100.0, 100.0, 101.0])  # 1% acima
    add_history(conn, "MLB_meio", [100.0, 100.0, 108.0])  # 8% acima

    assert [p[0] for p in hot_products(conn, "mercadolivre", 10, 25)] == [
        "MLB_perto",
        "MLB_meio",
    ]


def test_respeita_o_teto_da_fatia_quente():
    from promo.db import hot_products

    conn = make_conn()
    for i in range(10):
        add_history(conn, f"MLB{i}", [100.0, 100.0, 100.0])

    assert len(hot_products(conn, "mercadolivre", 10, 3)) == 3


def test_nao_mistura_fontes():
    from promo.db import hot_products

    conn = make_conn()
    add_history(conn, "MLB9", [100.0, 100.0, 100.0])

    assert hot_products(conn, "amazon", 10, 25) == []

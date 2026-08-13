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

from promo.db import SCHEMA, create_post, hours_since, recent_headlines, set_meta  # noqa: E402


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

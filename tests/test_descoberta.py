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


# ---------- o empate no topo ----------
#
# Produto com uma observacao so tem preco atual IGUAL a propria minima: a razao
# da 1.0 exata. Numa carteira jovem isso nao e caso de borda, e a maioria --
# medido em producao, 903 dos 904 produtos empatados. Sem desempate, o ciclo
# rapido devolvia as mesmas 25 linhas a cada 15 minutos, para sempre.


def visto_em(conn: sqlite3.Connection, external_id: str, quando: str) -> None:
    conn.execute(
        "UPDATE products SET last_seen_at = ? WHERE external_id = ?",
        (quando, external_id),
    )


def test_empatados_saem_do_mais_antigo_para_o_mais_novo():
    from promo.db import hot_products

    conn = make_conn()
    for nome in ("MLB_novo", "MLB_velho", "MLB_meio"):
        add_history(conn, nome, [100.0])  # uma observacao: razao 1.0 nos tres
    visto_em(conn, "MLB_novo", "2026-08-30T18:00:00+00:00")
    visto_em(conn, "MLB_meio", "2026-08-30T12:00:00+00:00")
    visto_em(conn, "MLB_velho", "2026-08-29T06:00:00+00:00")

    assert [p[0] for p in hot_products(conn, "mercadolivre", 10, 25)] == [
        "MLB_velho",
        "MLB_meio",
        "MLB_novo",
    ]


def test_o_ciclo_roda_a_carteira_em_vez_de_repetir():
    """Reconsultar carimba `last_seen_at`, e quem foi visto vai para o fim da
    fila. Duas passadas de teto 2 tem que cobrir quatro produtos, nao dois."""
    from promo.db import hot_products

    conn = make_conn()
    for i in range(4):
        add_history(conn, f"MLB{i}", [100.0])
        visto_em(conn, f"MLB{i}", f"2026-08-30T0{i}:00:00+00:00")

    primeira = [p[0] for p in hot_products(conn, "mercadolivre", 10, 2)]
    for external_id in primeira:
        visto_em(conn, external_id, "2026-08-30T23:00:00+00:00")
    segunda = [p[0] for p in hot_products(conn, "mercadolivre", 10, 2)]

    assert set(primeira) | set(segunda) == {"MLB0", "MLB1", "MLB2", "MLB3"}


def test_a_razao_ainda_manda_quando_nao_ha_empate():
    """O desempate e desempate: com historico de verdade, quem esta colado na
    minima continua na frente de quem foi visto ha mais tempo."""
    from promo.db import hot_products

    conn = make_conn()
    add_history(conn, "MLB_colado", [100.0, 90.0, 90.0])  # na minima
    add_history(conn, "MLB_acima", [100.0, 90.0, 98.0])  # 9% acima
    visto_em(conn, "MLB_colado", "2026-08-30T18:00:00+00:00")  # visto agora
    visto_em(conn, "MLB_acima", "2026-08-01T00:00:00+00:00")  # esquecido ha um mes

    assert [p[0] for p in hot_products(conn, "mercadolivre", 10, 25)] == [
        "MLB_colado",
        "MLB_acima",
    ]


def test_nao_mistura_fontes():
    from promo.db import hot_products

    conn = make_conn()
    add_history(conn, "MLB9", [100.0, 100.0, 100.0])

    assert hot_products(conn, "amazon", 10, 25) == []


# ---------- carimbo da descoberta ----------


class FonteQueFalha:
    """Fonte cuja busca sempre levanta -- o caso do servidor sem `ml-auth`."""

    name = "mercadolivre"

    def search(self, termo, limit=None):
        raise RuntimeError("Mercado Livre nao autorizado ainda")

    def highlights(self, category_id, limit=None):
        raise RuntimeError("Mercado Livre nao autorizado ainda")


class FonteSoVitrine:
    """Sem `search` e sem `highlights`: nao participa da descoberta."""

    name = "ml_ofertas"

    def fetch(self, pages=1):
        return []


def test_descoberta_que_falhou_inteira_nao_carimba(banco_em_memoria, monkeypatch):
    """O bug que custou meio dia de coleta.

    Na primeira rodada do servidor o `ml-auth` ainda nao tinha sido feito.
    Cada busca levantou "nao autorizado" -- tratadas uma a uma, sem derrubar a
    rodada -- e o carimbo era gravado assim mesmo. O bot passou a pular a
    descoberta por 12 horas por causa de uma descoberta que nao aconteceu.
    """
    from promo.db import get_meta
    from promo.pipeline import DISCOVERY_KEY, Category, Watch, collect

    monkeypatch.setattr("promo.pipeline._time_to_discover", lambda: True)
    collect(
        [FonteQueFalha()],
        [Watch(term="fone")],
        Rules_stub(),
        [Category(id="MLB1051")],
    )

    assert get_meta(banco_em_memoria, DISCOVERY_KEY) is None


def test_descoberta_que_respondeu_carimba(banco_em_memoria, monkeypatch):
    from promo.db import get_meta
    from promo.pipeline import DISCOVERY_KEY, Watch, collect

    class FonteOk:
        name = "mercadolivre"

        def search(self, termo, limit=None):
            return []

    monkeypatch.setattr("promo.pipeline._time_to_discover", lambda: True)
    collect([FonteOk()], [Watch(term="fone")], Rules_stub())

    assert get_meta(banco_em_memoria, DISCOVERY_KEY) is not None


def test_fonte_sem_descoberta_nao_gera_aviso(banco_em_memoria, monkeypatch, caplog):
    """Rodada so com a vitrine e o normal, nao uma falha a ser avisada."""
    import logging

    from promo.pipeline import Watch, collect

    monkeypatch.setattr("promo.pipeline._time_to_discover", lambda: True)
    with caplog.at_level(logging.WARNING):
        collect([FonteSoVitrine()], [Watch(term="fone")], Rules_stub())

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def Rules_stub():
    from promo.config import Rules

    return Rules(
        min_discount_pct=15.0,
        baseline_window_days=60,
        min_observations=4,
        repost_cooldown_days=14,
        max_offers_per_run=10,
    )

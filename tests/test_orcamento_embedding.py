"""O indexador guarda parte da cota diaria de embedding para as perguntas."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_assistente as base  # noqa: E402

from promo.assistente import banco, indice  # noqa: E402

EmbeddingsFalsos = base.EmbeddingsFalsos
produto = base.produto
bancos = base.bancos  # a fixture do outro arquivo, reaproveitada


def _cem_produtos() -> None:
    for n in range(100):
        produto(f"Produto numero {n} marca{n}", 10.0 + n, f"MLB{n}")


def test_indice_para_no_orcamento_do_dia(bancos, monkeypatch):
    monkeypatch.setattr(indice, "ORCAMENTO_DO_INDICE", 50)
    monkeypatch.setattr(indice, "LOTE_DE_EMBEDDING", 40)
    monkeypatch.setattr(indice, "dia_da_cota", lambda agora=None: "2026-11-26")
    _cem_produtos()
    with banco.conectar() as conn:
        indice.sincronizar(conn)
        assert indice.vetorizar_pendentes(conn, EmbeddingsFalsos()) == 40
        assert indice.vetorizar_pendentes(conn, EmbeddingsFalsos()) == 0


def test_orcamento_renova_no_dia_seguinte_do_pacifico(bancos, monkeypatch):
    monkeypatch.setattr(indice, "ORCAMENTO_DO_INDICE", 50)
    monkeypatch.setattr(indice, "LOTE_DE_EMBEDDING", 40)
    dia = {"valor": "2026-11-26"}
    monkeypatch.setattr(indice, "dia_da_cota", lambda agora=None: dia["valor"])
    _cem_produtos()
    with banco.conectar() as conn:
        indice.sincronizar(conn)
        assert indice.vetorizar_pendentes(conn, EmbeddingsFalsos()) == 40
        assert indice.vetorizar_pendentes(conn, EmbeddingsFalsos()) == 0
        dia["valor"] = "2026-11-27"
        assert indice.vetorizar_pendentes(conn, EmbeddingsFalsos()) == 40


def test_dia_da_cota_usa_o_fuso_do_pacifico():
    agora = datetime(2026, 11, 27, 6, 0, tzinfo=UTC)
    assert indice.dia_da_cota(agora) == "2026-11-26"

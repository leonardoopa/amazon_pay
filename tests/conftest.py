"""Fixtures compartilhadas pela suíte do bot.

Existe por causa de uma armadilha concreta: `collect()` abre o banco por conta
própria, via `connect()` do módulo, para decidir se está na hora de descobrir
produto novo. Quem chama `collect()` num teste sem substituir esse `connect`
acaba abrindo o SQLite real do ambiente -- que no CI é um arquivo recém-criado,
sem schema, e o teste morre em `no such table: meta` num ponto que não tem nada
a ver com o que ele estava verificando.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.db import SCHEMA  # noqa: E402


class ContextoDeConexao:
    """Deixa a conexão de teste passar pelo `with connect() as conn`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, *args: object) -> None:
        self.conn.commit()


@pytest.fixture
def banco_em_memoria(monkeypatch) -> sqlite3.Connection:
    """Banco com schema, em memória, no lugar do que o pipeline abriria.

    Devolve a conexão para o teste que quiser semear dado nela. Quem só precisa
    que o pipeline não toque em disco pode pedir a fixture e ignorar o retorno.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    monkeypatch.setattr("promo.pipeline.connect", lambda: ContextoDeConexao(conn))
    return conn


@pytest.fixture(autouse=True)
def sem_janela_de_silencio(monkeypatch):
    """Desliga a janela de silencio em toda a suite, por padrao.

    `_drip_gap()` multiplica o intervalo por `QUIET_DRIP_MULTIPLIER` entre
    23:30 e 07:30. Sem esta fixture, qualquer teste que meca ritmo de entrega
    passa de dia e falha de madrugada -- ele estaria medindo a hora do relogio
    em vez do codigo.

    Nao e hipotese: o runner do GitHub roda em UTC, entrou na janela, e dois
    testes de gotejamento vieram com intervalos 6x maiores (500s a 780s para
    100s configurados), barrando o deploy.

    Quem testa a janela em si sobrescreve com `monkeypatch.setenv`, que vale
    por ser aplicado depois desta fixture.
    """
    monkeypatch.setenv("QUIET_START", "")
    monkeypatch.setenv("QUIET_END", "")

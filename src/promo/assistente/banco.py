"""O SQLite do assistente: fila de mensagens, contatos e indice de busca.

Arquivo separado do `promos.db` (ver `config.assistente_db_path`). O assistente
so LE o banco do bot; o que ele escreve fica aqui.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from ..config import assistente_db_path
from ..db import _iso, now

BUSY_TIMEOUT_SECONDS = 5.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS mensagens (
    -- O id da mensagem no WhatsApp. A Evolution reenvia evento quando
    -- reconecta, e a chave primaria e o que impede responder duas vezes.
    id            TEXT PRIMARY KEY,
    jid           TEXT NOT NULL,      -- para onde vai a resposta
    telefone      TEXT NOT NULL,      -- so digitos: lista liberada e limite
    texto         TEXT NOT NULL,
    recebida_em   TEXT NOT NULL,
    status        TEXT NOT NULL,      -- pendente | respondida | ignorada | erro
    motivo        TEXT,
    consulta      TEXT,               -- o que o Gemini entendeu que era o pedido
    resultados    INTEGER,
    resposta      TEXT,
    respondida_em TEXT
);
CREATE INDEX IF NOT EXISTS idx_mensagens_fila ON mensagens(status, recebida_em);
CREATE INDEX IF NOT EXISTS idx_mensagens_telefone
    ON mensagens(telefone, respondida_em);

CREATE TABLE IF NOT EXISTS contatos (
    telefone         TEXT PRIMARY KEY,
    primeiro_contato TEXT NOT NULL,
    parou_em         TEXT            -- mandou PARAR: nao responde mais
);

CREATE TABLE IF NOT EXISTS itens (
    id         INTEGER PRIMARY KEY,
    product_id TEXT NOT NULL UNIQUE,
    titulo     TEXT NOT NULL,
    busca      TEXT NOT NULL,        -- titulo sem acento e minusculo
    fonte      TEXT NOT NULL,
    visto_em   TEXT NOT NULL
);
-- Conteudo externo: o FTS guarda so o indice, o texto mora em `itens`.
CREATE VIRTUAL TABLE IF NOT EXISTS itens_fts
    USING fts5(busca, content='itens', content_rowid='id');

CREATE TABLE IF NOT EXISTS vetores (
    id     INTEGER PRIMARY KEY REFERENCES itens(id) ON DELETE CASCADE,
    titulo TEXT NOT NULL,            -- o titulo que gerou o vetor: mudou, refaz
    modelo TEXT NOT NULL,            -- trocar de modelo invalida todos
    vetor  BLOB NOT NULL             -- float32, ja normalizado
);
"""

_INICIADOS: set[Path] = set()


@contextmanager
def conectar(caminho: Path | None = None) -> Iterator[sqlite3.Connection]:
    caminho = caminho or assistente_db_path()
    conn = sqlite3.connect(caminho, timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # A API do webhook escreve enquanto a fila e o indexador leem e escrevem.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    if caminho not in _INICIADOS:
        conn.executescript(SCHEMA)
        _INICIADOS.add(caminho)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- fila de mensagens ----------


def registrar_mensagem(
    conn: sqlite3.Connection, id: str, jid: str, telefone: str, texto: str
) -> bool:
    """Enfileira a mensagem. False quando ela ja tinha chegado antes."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO mensagens "
        "(id, jid, telefone, texto, recebida_em, status) "
        "VALUES (?, ?, ?, ?, ?, 'pendente')",
        (id, jid, telefone, texto, _iso(now())),
    )
    return cursor.rowcount == 1


def proxima_pendente(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM mensagens WHERE status = 'pendente' "
        "ORDER BY recebida_em, rowid LIMIT 1"
    ).fetchone()


def finalizar(
    conn: sqlite3.Connection,
    id: str,
    status: str,
    motivo: str | None = None,
    consulta: str | None = None,
    resultados: int | None = None,
    resposta: str | None = None,
) -> None:
    conn.execute(
        "UPDATE mensagens SET status = ?, motivo = ?, consulta = ?, "
        "resultados = ?, resposta = ?, respondida_em = ? WHERE id = ?",
        (
            status,
            motivo,
            consulta,
            resultados,
            resposta,
            _iso(now()) if status == "respondida" else None,
            id,
        ),
    )


def respondidas_na_ultima_hora(conn: sqlite3.Connection, telefone: str) -> int:
    desde = _iso(now() - timedelta(hours=1))
    return conn.execute(
        "SELECT COUNT(*) FROM mensagens WHERE telefone = ? "
        "AND status = 'respondida' AND respondida_em >= ?",
        (telefone, desde),
    ).fetchone()[0]


def apagar_antigas(conn: sqlite3.Connection, dias: int = 30) -> int:
    """Prazo de guarda das conversas. O aviso da primeira resposta promete 30."""
    limite = _iso(now() - timedelta(days=dias))
    return conn.execute(
        "DELETE FROM mensagens WHERE recebida_em < ?", (limite,)
    ).rowcount


# ---------- contatos ----------


def registrar_contato(conn: sqlite3.Connection, telefone: str) -> bool:
    """True quando e a primeira vez que este numero fala com o assistente."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO contatos (telefone, primeiro_contato) VALUES (?, ?)",
        (telefone, _iso(now())),
    )
    return cursor.rowcount == 1


def parou(conn: sqlite3.Connection, telefone: str) -> bool:
    linha = conn.execute(
        "SELECT parou_em FROM contatos WHERE telefone = ?", (telefone,)
    ).fetchone()
    return bool(linha and linha["parou_em"])


def marcar_parada(conn: sqlite3.Connection, telefone: str, parar: bool) -> None:
    registrar_contato(conn, telefone)
    conn.execute(
        "UPDATE contatos SET parou_em = ? WHERE telefone = ?",
        (_iso(now()) if parar else None, telefone),
    )

"""Como o arquivo SQLite se comporta com dois processos em cima dele.

O site (container `web`) le o mesmo promos.db que a coleta escreve. Esse
compartilhamento e novo, e no modo padrao do SQLite ele produz uma falha que
nao aparece em nenhum log: a escrita toma um lock exclusivo, o leitor recebe
SQLITE_BUSY, as views do site engolem o erro e a home serve contador zerado
com status 200.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.db import connect, create_post, init_db, record_offer  # noqa: E402
from promo.models import Offer  # noqa: E402


def _oferta() -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB1",
        title="Produto",
        price=100.0,
        url="https://produto.mercadolivre.com.br/x",
    )


def test_banco_fica_em_wal(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    init_db()

    with connect() as conn:
        modo = conn.execute("PRAGMA journal_mode").fetchone()[0]

    assert modo == "wal"


def test_wal_persiste_no_arquivo(tmp_path, monkeypatch):
    """O modo e propriedade do arquivo, nao da conexao. E isso que faz o
    processo do site (que so le) enxergar WAL sem precisar declarar nada."""
    caminho = tmp_path / "t.db"
    monkeypatch.setenv("DB_PATH", str(caminho))
    init_db()

    outra = sqlite3.connect(caminho)
    try:
        assert outra.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        outra.close()


def test_leitura_funciona_durante_uma_escrita_aberta(tmp_path, monkeypatch):
    """O caso real: a coleta esta no meio de uma transacao e o site pede a
    home. Em journal padrao esta leitura levantaria `database is locked`."""
    caminho = tmp_path / "t.db"
    monkeypatch.setenv("DB_PATH", str(caminho))
    init_db()
    with connect() as conn:
        record_offer(conn, _oferta())

    escritor = sqlite3.connect(caminho, isolation_level=None)
    try:
        escritor.execute("BEGIN IMMEDIATE")
        escritor.execute(
            "INSERT INTO price_history (product_id, observed_on, price)"
            " VALUES ('mercadolivre:MLB1', '2026-01-01', 99.0)"
        )
        # Escrita ainda nao commitada. O leitor tem que ver o estado anterior
        # em vez de falhar.
        with connect() as leitor:
            assert (
                leitor.execute("SELECT COUNT(*) c FROM products").fetchone()["c"] == 1
            )
    finally:
        escritor.rollback()
        escritor.close()


def test_busy_timeout_configurado(tmp_path, monkeypatch):
    """Duas escritas ainda se excluem entre si; esperar 5s e melhor do que
    levantar na hora."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    init_db()

    with connect() as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_banco_antigo_ganha_a_coluna_verified(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` nao mexe em tabela que ja existe. Sem a
    migracao, o banco em producao continuaria sem a coluna e todo INSERT de
    post falharia depois do deploy."""
    caminho = tmp_path / "antigo.db"
    monkeypatch.setenv("DB_PATH", str(caminho))

    antigo = sqlite3.connect(caminho)
    antigo.executescript("""
        CREATE TABLE products (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, external_id TEXT NOT NULL,
            title TEXT NOT NULL, url TEXT NOT NULL, image_url TEXT, category TEXT,
            first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL
        );
        CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL REFERENCES products(id),
            price REAL NOT NULL, baseline REAL NOT NULL, discount_pct REAL NOT NULL,
            copy TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL, sent_at TEXT
        );
        """)
    antigo.commit()
    antigo.close()

    init_db()

    with connect() as conn:
        colunas = {linha["name"] for linha in conn.execute("PRAGMA table_info(posts)")}
        assert {"verified", "image_url", "attempts"} <= colunas
        record_offer(conn, _oferta())
        post_id = create_post(conn, "mercadolivre:MLB1", 10.0, 20.0, 50.0, "t", None)
        linha = conn.execute(
            "SELECT verified FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        # Herda como verificado: o dado antigo era lido assim, e marcar tudo
        # como repasse apagaria a prova real que existe.
        assert linha["verified"] == 1

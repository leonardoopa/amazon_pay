"""SQLite: catalogo, historico de precos, posts e tokens OAuth."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Iterator

from .config import db_path
from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    image_url     TEXT,
    category      TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_history (
    product_id     TEXT NOT NULL REFERENCES products(id),
    observed_on    TEXT NOT NULL,  -- data (YYYY-MM-DD), 1 linha por dia
    price          REAL NOT NULL,  -- menor preco visto no dia
    original_price REAL,
    available      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (product_id, observed_on)
);

CREATE TABLE IF NOT EXISTS posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id   TEXT NOT NULL REFERENCES products(id),
    price        REAL NOT NULL,
    baseline     REAL NOT NULL,
    discount_pct REAL NOT NULL,
    copy         TEXT NOT NULL,
    status       TEXT NOT NULL,  -- pending | sent | failed
    error        TEXT,
    created_at   TEXT NOT NULL,
    sent_at      TEXT
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    provider      TEXT PRIMARY KEY,
    access_token  TEXT,
    refresh_token TEXT,
    expires_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_history_product
    ON price_history(product_id, observed_on DESC);
CREATE INDEX IF NOT EXISTS idx_posts_product
    ON posts(product_id, created_at DESC);
"""


def now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def record_offer(conn: sqlite3.Connection, offer: Offer) -> None:
    """Upsert do produto + registra o menor preco do dia.

    Guardar 1 linha por dia (o minimo) mantem a baseline estavel mesmo se o
    coletor rodar de hora em hora.
    """
    ts = _iso(now())
    today = now().date().isoformat()

    conn.execute(
        """
        INSERT INTO products (id, source, external_id, title, url, image_url,
                              category, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title        = excluded.title,
            url          = excluded.url,
            image_url    = excluded.image_url,
            category     = excluded.category,
            last_seen_at = excluded.last_seen_at
        """,
        (
            offer.product_id,
            offer.source,
            offer.external_id,
            offer.title,
            offer.url,
            offer.image_url,
            offer.category,
            ts,
            ts,
        ),
    )

    conn.execute(
        """
        INSERT INTO price_history (product_id, observed_on, price, original_price, available)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(product_id, observed_on) DO UPDATE SET
            price          = MIN(price_history.price, excluded.price),
            original_price = excluded.original_price,
            available      = excluded.available
        """,
        (offer.product_id, today, offer.price, offer.original_price, int(offer.available)),
    )


def price_history(
    conn: sqlite3.Connection, product_id: str, window_days: int
) -> list[float]:
    """Precos diarios da janela, excluindo hoje (nao contamina a baseline)."""
    today = now().date().isoformat()
    rows = conn.execute(
        """
        SELECT price FROM price_history
        WHERE product_id = ?
          AND observed_on < ?
          AND observed_on >= date(?, ?)
        ORDER BY observed_on DESC
        """,
        (product_id, today, today, f"-{window_days} days"),
    ).fetchall()
    return [row["price"] for row in rows]


def last_post(conn: sqlite3.Connection, product_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM posts
        WHERE product_id = ? AND status = 'sent'
        ORDER BY created_at DESC LIMIT 1
        """,
        (product_id,),
    ).fetchone()


def create_post(
    conn: sqlite3.Connection,
    product_id: str,
    price: float,
    baseline: float,
    discount_pct: float,
    copy: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO posts (product_id, price, baseline, discount_pct, copy, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
        """,
        (product_id, price, baseline, discount_pct, copy, _iso(now())),
    )
    return int(cursor.lastrowid)


def pending_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Posts que ficaram na fila (janela de 24h fechada, erro de rede etc.)."""
    return conn.execute(
        "SELECT id, copy FROM posts WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()


def mark_post_sent(conn: sqlite3.Connection, post_id: int) -> None:
    conn.execute(
        "UPDATE posts SET status = 'sent', sent_at = ? WHERE id = ?",
        (_iso(now()), post_id),
    )


def mark_post_failed(conn: sqlite3.Connection, post_id: int, error: str) -> None:
    conn.execute(
        "UPDATE posts SET status = 'failed', error = ? WHERE id = ?",
        (error[:500], post_id),
    )


def save_token(
    conn: sqlite3.Connection,
    provider: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime,
) -> None:
    conn.execute(
        """
        INSERT INTO oauth_tokens (provider, access_token, refresh_token, expires_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(provider) DO UPDATE SET
            access_token  = excluded.access_token,
            refresh_token = COALESCE(excluded.refresh_token, oauth_tokens.refresh_token),
            expires_at    = excluded.expires_at
        """,
        (provider, access_token, refresh_token, _iso(expires_at)),
    )


def load_token(conn: sqlite3.Connection, provider: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM oauth_tokens WHERE provider = ?", (provider,)
    ).fetchone()

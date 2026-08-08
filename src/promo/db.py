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
    image_url    TEXT,           -- imagem enviada junto do texto
    attempts     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,  -- pending | sent | failed
    error        TEXT,
    created_at   TEXT NOT NULL,
    sent_at      TEXT
);

-- Link de afiliado gerado a mao no Link Builder do ML. Nao da pra montar:
-- o link real aponta pra /social/<nickname> com um `ref` assinado pelo ML,
-- e o ID do produto nem aparece na URL. Ver README.
CREATE TABLE IF NOT EXISTS affiliate_links (
    product_id TEXT PRIMARY KEY REFERENCES products(id),
    url        TEXT NOT NULL,
    created_at TEXT NOT NULL
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

# Colunas acrescentadas depois do schema original. O CREATE TABLE IF NOT EXISTS
# nao mexe em tabela que ja existe, entao banco antigo precisa do ALTER.
# Tentativas de envio antes de desistir de um post.
MAX_SEND_ATTEMPTS = 3

MIGRATIONS = [
    ("posts", "image_url", "ALTER TABLE posts ADD COLUMN image_url TEXT"),
    ("posts", "attempts", "ALTER TABLE posts ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"),
]


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
        for table, column, statement in MIGRATIONS:
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(statement)


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


def tracked_products(
    conn: sqlite3.Connection, source: str, window_days: int, limit: int
) -> list[tuple[str, str, str | None]]:
    """Produtos que valem reconsultar: (external_id, titulo, imagem).

    Devolve titulo e imagem junto de proposito -- eles quase nao mudam, e
    carrega-los do banco evita uma segunda chamada de API por produto na
    reconsulta, que e o passo mais caro da rodada.

    Descarta quem sumiu ha mais de `window_days`: historico mais velho que a
    janela da baseline nao serve pra nada, e sem o corte a lista cresceria
    indefinidamente a cada termo novo na watchlist.

    Comparo por substr em vez de date() porque last_seen_at e um ISO completo
    com offset de fuso, e o date() do SQLite tropeca nisso.
    """
    today = now().date().isoformat()
    rows = conn.execute(
        """
        SELECT external_id, title, image_url FROM products
        WHERE source = ?
          AND substr(last_seen_at, 1, 10) >= date(?, ?)
        ORDER BY last_seen_at DESC
        LIMIT ?
        """,
        (source, today, f"-{window_days} days", limit),
    ).fetchall()
    return [(row["external_id"], row["title"], row["image_url"]) for row in rows]


def last_post(conn: sqlite3.Connection, product_id: str) -> sqlite3.Row | None:
    """Ultimo post do produto que ja saiu ou ainda vai sair.

    Inclui 'pending' de proposito: quando a janela de 24h fecha, o post fica
    na fila e a rodada seguinte repescava o mesmo produto, criando duplicata
    que so aparecia quando a janela reabria e as duas saiam juntas.
    """
    return conn.execute(
        """
        SELECT * FROM posts
        WHERE product_id = ? AND status IN ('sent', 'pending')
        ORDER BY created_at DESC LIMIT 1
        """,
        (product_id,),
    ).fetchone()


def affiliate_link(conn: sqlite3.Connection, product_id: str) -> str | None:
    row = conn.execute(
        "SELECT url FROM affiliate_links WHERE product_id = ?", (product_id,)
    ).fetchone()
    return row["url"] if row else None


def save_affiliate_link(conn: sqlite3.Connection, product_id: str, url: str) -> None:
    conn.execute(
        """
        INSERT INTO affiliate_links (product_id, url, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET url = excluded.url
        """,
        (product_id, url, _iso(now())),
    )


def products_missing_link(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """Produtos rastreados que ainda nao tem link de afiliado.

    Ordenados pelos que mais se aproximam de virar post (mais dias de
    historico primeiro), pra voce gerar link do que tem chance de sair.
    """
    return conn.execute(
        """
        SELECT p.id, p.external_id, p.title, p.url, COUNT(h.observed_on) AS dias
        FROM products p
        JOIN price_history h ON h.product_id = p.id
        LEFT JOIN affiliate_links a ON a.product_id = p.id
        WHERE a.product_id IS NULL
        GROUP BY p.id
        ORDER BY dias DESC, p.last_seen_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def create_post(
    conn: sqlite3.Connection,
    product_id: str,
    price: float,
    baseline: float,
    discount_pct: float,
    copy: str,
    image_url: str | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO posts (product_id, price, baseline, discount_pct, copy,
                           image_url, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (product_id, price, baseline, discount_pct, copy, image_url, _iso(now())),
    )
    return int(cursor.lastrowid)


def pending_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Posts que ficaram na fila (janela de 24h fechada, erro de rede etc.)."""
    return conn.execute(
        "SELECT id, copy, image_url FROM posts WHERE status = 'pending' ORDER BY created_at"
    ).fetchall()


def mark_post_sent(conn: sqlite3.Connection, post_id: int) -> None:
    conn.execute(
        "UPDATE posts SET status = 'sent', sent_at = ? WHERE id = ?",
        (_iso(now()), post_id),
    )


def mark_post_failed(
    conn: sqlite3.Connection, post_id: int, error: str, max_attempts: int = MAX_SEND_ATTEMPTS
) -> None:
    """Conta a tentativa e so desiste depois de `max_attempts`.

    Antes um timeout de rede marcava 'failed' na primeira falha, e o flush so
    busca 'pending' -- a oferta era perdida pra sempre por um erro transitorio.
    """
    conn.execute(
        """
        UPDATE posts
        SET attempts = attempts + 1,
            error    = ?,
            status   = CASE WHEN attempts + 1 >= ? THEN 'failed' ELSE 'pending' END
        WHERE id = ?
        """,
        (error[:500], max_attempts, post_id),
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

"""SQLite: catalogo, historico de precos, posts e tokens OAuth."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Iterator

from .config import db_path, post_cooldown_minutes, post_max_age_minutes
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
    -- Contra o que o desconto foi medido. Com verified=1 e a mediana que a
    -- nossa coleta apurou; com verified=0 e o preco riscado do anuncio.
    baseline     REAL NOT NULL,
    discount_pct REAL NOT NULL,
    copy         TEXT NOT NULL,
    image_url    TEXT,           -- imagem enviada junto do texto
    attempts     INTEGER NOT NULL DEFAULT 0,
    -- 1 = desconto medido contra a nossa mediana. 0 = repasse da vitrine do
    -- ML, medido contra o "de/por" da loja. A distincao existia so em memoria
    -- (ScoredOffer.verified) e morria aqui: o site tratava os dois como prova
    -- e chamava o "de/por" da loja de "media" -- exatamente o que a pagina
    -- acusa a loja de fazer.
    verified     INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL,  -- pending | sent | failed | expired
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

-- Produtos que o programa de afiliados recusa ("URL not allowed in affiliates
-- program", codigo 111). Sem registrar isso, o pipeline pediria link pro mesmo
-- anuncio inelegivel em toda rodada, pra sempre. A elegibilidade muda com o
-- tempo, entao a recusa expira -- ver AFFILIATE_RECHECK_DAYS.
CREATE TABLE IF NOT EXISTS affiliate_blocked (
    product_id TEXT PRIMARY KEY REFERENCES products(id),
    reason     TEXT NOT NULL,
    checked_at TEXT NOT NULL
);

-- Chave/valor de controle do proprio bot (ex.: quando a descoberta rodou
-- pela ultima vez). Vive no banco em vez de memoria porque o daemon reinicia,
-- e um contador em memoria zeraria junto.
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
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

# Quanto tempo respeitar uma recusa do programa de afiliados antes de perguntar
# de novo. Elegibilidade muda (o vendedor entra no programa, a categoria passa
# a ser aceita), entao a recusa nao pode ser definitiva -- mas 30 dias evitam
# transformar cada anuncio inelegivel numa consulta por rodada.
AFFILIATE_RECHECK_DAYS = 30

MIGRATIONS = [
    ("posts", "image_url", "ALTER TABLE posts ADD COLUMN image_url TEXT"),
    (
        "posts",
        "attempts",
        "ALTER TABLE posts ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
    ),
    (
        "posts",
        "verified",
        # DEFAULT 1 para o banco que ja existe: antes desta coluna o repasse da
        # vitrine era minoria e nao ha como separar retroativamente. Marcar
        # tudo como verificado herda o dado como ele era lido antes; marcar
        # tudo como nao verificado apagaria a prova real que existe.
        "ALTER TABLE posts ADD COLUMN verified INTEGER NOT NULL DEFAULT 1",
    ),
]

# Segundos que uma conexao espera por um lock antes de desistir.
BUSY_TIMEOUT_SECONDS = 5.0


def now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path(), timeout=BUSY_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: o site (container `web`) le este mesmo arquivo enquanto a coleta
    # escreve. No journal padrao a escrita toma um lock exclusivo e o leitor
    # recebe SQLITE_BUSY na hora -- as views do site engolem o erro e a home
    # serve contador zerado e feed vazio, com 200 e sem log. Em WAL leitor e
    # escritor nao se bloqueiam.
    #
    # O modo fica gravado no arquivo, entao uma conexao basta para valer para
    # todas. Banco em memoria (teste) ignora e responde "memory".
    conn.execute("PRAGMA journal_mode = WAL")
    # Duas escritas simultaneas ainda se excluem entre si. Esperar e melhor do
    # que falhar: sem isso o segundo escritor levanta na hora.
    conn.execute(f"PRAGMA busy_timeout = {int(BUSY_TIMEOUT_SECONDS * 1000)}")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        for table, column, statement in MIGRATIONS:
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
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
        (
            offer.product_id,
            today,
            offer.price,
            offer.original_price,
            int(offer.available),
        ),
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


def hot_products(
    conn: sqlite3.Connection, source: str, margin_pct: float, limit: int
) -> list[tuple[str, str, str | None]]:
    """Produtos perto da propria minima historica: (external_id, titulo, imagem).

    A carteira inteira nao cabe num polling de minutos, mas so uma fatia dela
    pode virar post na proxima hora. Produto que hoje esta 40% acima da propria
    minima nao vira oferta com mais uma queda pequena; produto que ja esta
    colado na minima, sim. Sao esses que vale reconsultar com frequencia.

    `margin_pct` e a folga sobre a minima: 10 significa "ate 10% acima dela".

    O desempate por `last_seen_at` nao e detalhe. Produto com uma observacao so
    tem preco atual IGUAL a propria minima, e a razao da 1.0 exata -- entao numa
    carteira jovem quase tudo empata no topo: medido em producao, 903 dos 904
    produtos. Ordenar so pela razao devolvia as mesmas 25 linhas a cada ciclo,
    e reconsultar o mesmo produto de 15 em 15 minutos nao descobre queda em
    lugar nenhum. Com o desempate, quem acabou de ser visto vai para o fim da
    fila e o ciclo roda a carteira inteira.
    """
    rows = conn.execute(
        """
        SELECT p.external_id, p.title, p.image_url
        FROM products p
        JOIN (
            SELECT product_id, MIN(price) AS minimo, MAX(observed_on) AS ultimo
            FROM price_history GROUP BY product_id
        ) h ON h.product_id = p.id
        JOIN price_history atual
          ON atual.product_id = p.id AND atual.observed_on = h.ultimo
        WHERE p.source = ?
          AND h.minimo > 0
          AND atual.price <= h.minimo * (1 + ? / 100.0)
        ORDER BY atual.price / h.minimo ASC, p.last_seen_at ASC
        LIMIT ?
        """,
        (source, margin_pct, limit),
    ).fetchall()
    return [(r["external_id"], r["title"], r["image_url"]) for r in rows]


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


def products_missing_link(
    conn: sqlite3.Connection, limit: int = 50
) -> list[sqlite3.Row]:
    """Produtos rastreados que ainda nao tem link de afiliado.

    Ordenados pelos que mais se aproximam de virar post (mais dias de
    historico primeiro), pra gerar link do que tem chance de sair.

    Fora da lista: quem o programa recusou ha menos de AFFILIATE_RECHECK_DAYS.
    Sem esse corte, cada rodada gastaria uma consulta por anuncio inelegivel --
    e eles nunca saem do banco, entao o desperdicio so cresce.
    """
    return conn.execute(
        """
        SELECT p.id, p.external_id, p.title, p.url, COUNT(h.observed_on) AS dias
        FROM products p
        JOIN price_history h ON h.product_id = p.id
        LEFT JOIN affiliate_links a ON a.product_id = p.id
        LEFT JOIN affiliate_blocked b
               ON b.product_id = p.id
              AND b.checked_at >= datetime(?, ?)
        WHERE a.product_id IS NULL
          AND b.product_id IS NULL
        GROUP BY p.id
        ORDER BY dias DESC, p.last_seen_at DESC
        LIMIT ?
        """,
        (_iso(now()), f"-{AFFILIATE_RECHECK_DAYS} days", limit),
    ).fetchall()


def mark_affiliate_blocked(
    conn: sqlite3.Connection, product_id: str, reason: str
) -> None:
    conn.execute(
        """
        INSERT INTO affiliate_blocked (product_id, reason, checked_at)
        VALUES (?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            reason     = excluded.reason,
            checked_at = excluded.checked_at
        """,
        (product_id, reason[:200], _iso(now())),
    )


def affiliate_blocked(conn: sqlite3.Connection, product_id: str) -> str | None:
    """Motivo da recusa, se ela ainda vale. None = pode tentar de novo."""
    row = conn.execute(
        """
        SELECT reason FROM affiliate_blocked
        WHERE product_id = ? AND checked_at >= datetime(?, ?)
        """,
        (product_id, _iso(now()), f"-{AFFILIATE_RECHECK_DAYS} days"),
    ).fetchone()
    return row["reason"] if row else None


def create_post(
    conn: sqlite3.Connection,
    product_id: str,
    price: float,
    baseline: float,
    discount_pct: float,
    copy: str,
    image_url: str | None = None,
    verified: bool = True,
) -> int:
    """Enfileira um post.

    `verified` acompanha o `ScoredOffer`: True quando a baseline e a mediana
    que apuramos, False quando e o preco riscado da loja (repasse da vitrine).
    O site le esta coluna para nao apresentar repasse como prova medida.
    """
    cursor = conn.execute(
        """
        INSERT INTO posts (product_id, price, baseline, discount_pct, copy,
                           image_url, verified, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            product_id,
            price,
            baseline,
            discount_pct,
            copy,
            image_url,
            1 if verified else 0,
            _iso(now()),
        ),
    )
    return int(cursor.lastrowid)


# Palavras que nao ajudam a distinguir um produto de outro.
_VAZIAS = {
    "a", "as", "com", "cor", "da", "das", "de", "do", "dos", "e", "em", "kit",
    "na", "no", "o", "os", "para", "por", "pra", "um", "uma",
}


def familia_do_titulo(titulo: str, palavras: int = 3) -> str:
    """Assinatura grosseira do produto, para pegar anuncio repetido.

    O mesmo produto fisico aparece no ML como dezenas de anuncios de vendedores
    diferentes -- `external_id` diferente, e portanto `product_id` diferente.
    O cooldown por produto nao ve isso, e o grupo recebeu em quinze minutos:

        Short Saia Esportivo Feminino Ausare de Lycra...      R$  95,00
        Short Saia Esportivo Feminino Ausare com Protecao...  R$  99,90
        Short Saia Esportivo Feminino Ausare com Protecao...  R$ 126,51
        Short Saia Esportivo Ausare Feminino, para Treino...  R$  89,90

    Quatro anuncios, quatro precos, o mesmo short. Com precos diferentes fica
    pior do que a repeticao simples: parece que o grupo nao sabe o preco.

    As tres primeiras palavras significativas bastam para juntar as quatro
    ("short saia esportivo") e continuam separando o que e de fato diferente:
    "mochila notebook ntesx" nao colide com "mochila jiesipote prova", nem
    "smart tv samsung" com "smart tv philco".

    Numero sai fora: e ele que varia entre "UV 50+" e "UV50+", e entre
    tamanhos do mesmo modelo.
    """
    tokens = re.findall(r"[a-z]+", sem_acento(titulo))
    significativas = [t for t in tokens if t not in _VAZIAS and len(t) > 1]
    return " ".join(significativas[:palavras])


def sem_acento(texto: str) -> str:
    """Minusculo e sem diacritico, para comparar titulo com termo escrito a mao.

    Quem edita o `watchlist.json` escreve "protetor solar"; o ML devolve
    "Protetor Solar Facial FPS 70". Sem normalizar os dois lados a comparacao
    depende de como o vendedor digitou o anuncio.
    """
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def tem_tema(titulo: str, temas: list[str]) -> bool:
    """O titulo cita algum dos temas, ja normalizados por `sem_acento`."""
    normalizado = sem_acento(titulo)
    return any(tema in normalizado for tema in temas)


def families_in_cooldown(
    conn: sqlite3.Connection, cooldown_minutes: int | None = None
) -> set[str]:
    """Familias de titulo que acabaram de ir para o grupo.

    Mesma regra de `products_in_cooldown`, um nivel acima: la o alvo e o
    anuncio, aqui e o produto que o leitor reconhece.
    """
    if cooldown_minutes is None:
        cooldown_minutes = post_cooldown_minutes()

    if cooldown_minutes <= 0:
        condicao, args = "po.status = 'pending'", ()
    else:
        condicao = (
            "po.status = 'pending' "
            "OR (po.status = 'sent' AND COALESCE(po.sent_at, po.created_at) >= ?)"
        )
        args = (_iso(now() - timedelta(minutes=cooldown_minutes)),)

    rows = conn.execute(
        f"""
        SELECT DISTINCT p.title FROM posts po
        JOIN products p ON p.id = po.product_id
        WHERE {condicao}
        """,
        args,
    ).fetchall()
    return {f for row in rows if (f := familia_do_titulo(row["title"]))}


def products_in_cooldown(
    conn: sqlite3.Connection, cooldown_minutes: int | None = None
) -> set[str]:
    """Produtos que nao podem virar post agora, por terem virado ha pouco.

    Duas situacoes bloqueiam, e por motivos diferentes:

    - `pending`: ainda nao foi para o grupo, mas vai. Enfileirar um segundo
      post do mesmo produto garante a repeticao em vez de arriscar ela.
    - `sent` dentro da janela: acabou de sair. E o caso que o leitor percebe.

    `expired` e `failed` ficam de fora de proposito -- eles nunca chegaram ao
    grupo, entao nao ha repeticao a evitar, e bloquear por causa deles tiraria
    do ar justamente a oferta que falhou em ser entregue.
    """
    if cooldown_minutes is None:
        cooldown_minutes = post_cooldown_minutes()
    if cooldown_minutes <= 0:
        # Mesmo desligada a regra, post na fila continua bloqueando: sem isso
        # duas rodadas seguidas enfileiram o mesmo produto e o grupo recebe os
        # dois em sequencia, com minutos de diferenca.
        rows = conn.execute(
            "SELECT DISTINCT product_id FROM posts WHERE status = 'pending'"
        ).fetchall()
        return {row["product_id"] for row in rows}

    limite = _iso(now() - timedelta(minutes=cooldown_minutes))
    rows = conn.execute(
        """
        SELECT DISTINCT product_id FROM posts
        WHERE status = 'pending'
           OR (status = 'sent' AND COALESCE(sent_at, created_at) >= ?)
        """,
        (limite,),
    ).fetchall()
    return {row["product_id"] for row in rows}


def expire_stale_posts(
    conn: sqlite3.Connection, max_age_minutes: int | None = None
) -> list[sqlite3.Row]:
    """Tira da fila os posts velhos demais para ainda valerem.

    Devolve o que saiu, para o chamador poder registrar no log. O preco no
    texto e uma medicao com hora, e uma hora depois ela pode nao valer mais --
    ver config.post_max_age_minutes.
    """
    if max_age_minutes is None:
        max_age_minutes = post_max_age_minutes()
    if max_age_minutes <= 0:
        return []
    limite = _iso(now() - timedelta(minutes=max_age_minutes))
    velhos = conn.execute(
        """
        SELECT id, product_id, price, created_at FROM posts
        WHERE status = 'pending' AND created_at < ?
        ORDER BY created_at
        """,
        (limite,),
    ).fetchall()
    if velhos:
        conn.execute(
            "UPDATE posts SET status = 'expired', error = ? "
            "WHERE status = 'pending' AND created_at < ?",
            (f"preco medido ha mais de {max_age_minutes} min", limite),
        )
    return velhos


def recent_headlines(conn: sqlite3.Connection, limit: int = 12) -> list[str]:
    """Primeira linha dos ultimos posts, pra nao repetir a chamada.

    Cada chamada ao Gemini e independente: ele nao lembra do que escreveu ontem,
    nem ha hora atras. Sem esse historico o grupo recebe "AIR FRYER POR 389
    PILA" e "AIR FRYER POR 279 PILA" no mesmo dia, e a graca morre na segunda.
    """
    rows = conn.execute(
        """
        SELECT copy FROM posts
        WHERE status IN ('sent', 'pending')
        ORDER BY created_at DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        linha for row in rows if (linha := row["copy"].strip().splitlines()[0].strip())
    ]


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
    conn: sqlite3.Connection,
    post_id: int,
    error: str,
    max_attempts: int = MAX_SEND_ATTEMPTS,
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


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def hours_since(conn: sqlite3.Connection, key: str) -> float | None:
    """Horas desde que `key` foi carimbada. None se nunca foi."""
    marca = get_meta(conn, key)
    if not marca:
        return None

    quando = datetime.fromisoformat(marca)
    if quando.tzinfo is None:
        # Carimbo sem fuso so aparece se alguem escreveu na mao (ou com
        # datetime('now') do proprio SQLite). Assumir UTC erra no maximo
        # algumas horas; estourar TypeError derrubaria a rodada inteira.
        quando = quando.replace(tzinfo=UTC)
    return (now() - quando).total_seconds() / 3600


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


def stats(conn: sqlite3.Connection, min_observations: int = 7) -> dict[str, int]:
    """Contagens do estado do banco, para o `promo stats` e para o /stats.

    Mora aqui, e nao no cli.py, porque agora tem dois consumidores -- e o SQL
    de `ready` (o que ja tem baseline) e sutil demais pra existir em duas
    copias que podem divergir.
    """

    def conta(sql: str, args: tuple = ()) -> int:
        return conn.execute(sql, args).fetchone()["c"]

    return {
        "products": conta("SELECT COUNT(*) c FROM products"),
        "price_points": conta("SELECT COUNT(*) c FROM price_history"),
        "with_baseline": conta(
            """
            SELECT COUNT(*) c FROM (
                SELECT product_id FROM price_history
                GROUP BY product_id HAVING COUNT(*) >= ?
            )
            """,
            (min_observations,),
        ),
        "posts_sent": conta("SELECT COUNT(*) c FROM posts WHERE status = 'sent'"),
        # Quanto do que saiu foi medido por nos, e quanto foi repasse do
        # "de/por" da loja. Separado porque so o primeiro sustenta a promessa
        # do site; ver a coluna `verified`.
        "posts_sent_verified": conta(
            "SELECT COUNT(*) c FROM posts WHERE status = 'sent' AND verified = 1"
        ),
        "posts_pending": conta("SELECT COUNT(*) c FROM posts WHERE status = 'pending'"),
        "posts_failed": conta("SELECT COUNT(*) c FROM posts WHERE status = 'failed'"),
        # Enfileirados e descartados por preco velho. Numero alto quer dizer
        # que a coleta produz mais do que o gotejamento drena.
        "posts_expired": conta("SELECT COUNT(*) c FROM posts WHERE status = 'expired'"),
    }

"""Configuracao lida do .env. Falha cedo e com mensagem clara quando falta credencial."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]


class MissingConfig(RuntimeError):
    """Levantada quando uma credencial obrigatoria nao esta no .env."""


def _get(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None or value == "":
        raise MissingConfig(f"Falta {key} no .env (copie de .env.example)")
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key) or default


def _int(key: str, default: int) -> int:
    raw = os.getenv(key)
    return int(raw) if raw else default


@dataclass(frozen=True)
class MercadoLivreConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    site_id: str
    # Sessao do painel de afiliados. Vazios = geracao automatica desligada, e o
    # fluxo volta a ser o `promo link` manual. Nao sao obrigatorios porque
    # cookie expira, e cookie expirado nao pode derrubar a coleta junto.
    affiliate_cookie: str = ""
    affiliate_tag: str = ""

    @classmethod
    def load(cls) -> "MercadoLivreConfig":
        return cls(
            client_id=_get("ML_CLIENT_ID"),
            client_secret=_get("ML_CLIENT_SECRET"),
            redirect_uri=_optional("ML_REDIRECT_URI", "http://localhost:8123/callback"),
            site_id=_optional("ML_SITE_ID", "MLB"),
            affiliate_cookie=_optional("ML_AFFILIATE_COOKIE"),
            affiliate_tag=_optional("ML_AFFILIATE_TAG"),
        )


@dataclass(frozen=True)
class AmazonConfig:
    client_id: str
    client_secret: str
    token_endpoint: str
    marketplace: str
    partner_tag: str

    @classmethod
    def load(cls) -> "AmazonConfig":
        return cls(
            client_id=_get("AMAZON_CLIENT_ID"),
            client_secret=_get("AMAZON_CLIENT_SECRET"),
            token_endpoint=_get("AMAZON_TOKEN_ENDPOINT"),
            marketplace=_optional("AMAZON_MARKETPLACE", "www.amazon.com.br"),
            partner_tag=_get("AMAZON_PARTNER_TAG"),
        )


@dataclass(frozen=True)
class WhatsAppConfig:
    phone_number_id: str
    token: str
    to: str
    ping_template: str
    template_lang: str

    @classmethod
    def load(cls) -> "WhatsAppConfig":
        return cls(
            phone_number_id=_get("WHATSAPP_PHONE_NUMBER_ID"),
            token=_get("WHATSAPP_TOKEN"),
            to=_get("WHATSAPP_TO"),
            ping_template=_optional("WHATSAPP_PING_TEMPLATE", "ofertas_ping"),
            template_lang=_optional("WHATSAPP_TEMPLATE_LANG", "pt_BR"),
        )


@dataclass(frozen=True)
class EvolutionConfig:
    """Evolution API -- entrega direto no grupo, por fora da API oficial."""

    base_url: str
    api_key: str
    instance: str
    group_jid: str

    @classmethod
    def load(cls) -> "EvolutionConfig":
        return cls(
            base_url=_optional("EVOLUTION_BASE_URL", "http://localhost:8080"),
            api_key=_get("EVOLUTION_API_KEY"),
            instance=_optional("EVOLUTION_INSTANCE", "ofertas"),
            # Sai do `promo wa-groups`; termina em @g.us. Nao e o numero do grupo,
            # e o JID -- grupo nao tem numero de telefone.
            group_jid=_get("EVOLUTION_GROUP_JID"),
        )


def delivery_backend() -> str:
    """'evolution' (posta no grupo) ou 'cloud' (manda pra voce encaminhar)."""
    return _optional("DELIVERY_BACKEND", "cloud").strip().lower()


@dataclass(frozen=True)
class Rules:
    min_discount_pct: float
    baseline_window_days: int
    min_observations: int
    repost_cooldown_days: int
    max_offers_per_run: int

    @classmethod
    def load(cls) -> "Rules":
        return cls(
            min_discount_pct=float(_optional("MIN_DISCOUNT_PCT", "15")),
            baseline_window_days=_int("BASELINE_WINDOW_DAYS", 60),
            min_observations=_int("MIN_OBSERVATIONS", 7),
            repost_cooldown_days=_int("REPOST_COOLDOWN_DAYS", 14),
            max_offers_per_run=_int("MAX_OFFERS_PER_RUN", 5),
        )


def db_path() -> Path:
    raw = _optional("DB_PATH", "data/promos.db")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def copy_model() -> str:
    return _optional("GEMINI_MODEL", "gemini-3.5-flash-lite")


def gemini_api_key() -> str:
    return _get("GEMINI_API_KEY")


def run_interval_seconds() -> int:
    """Intervalo do modo daemon (usado pelo container)."""
    return _int("RUN_INTERVAL_SECONDS", 7200)


def ofertas_pages() -> int:
    """Paginas da vitrine /ofertas lidas por rodada (~45 produtos cada).

    0 desliga a fonte e o grupo volta a postar so o que a nossa medicao provar.
    """
    return _int("OFERTAS_PAGES", 2)


def full_refetch_interval_hours() -> float:
    """Horas entre reconsultas da carteira INTEIRA."""
    return float(_optional("FULL_REFETCH_INTERVAL_HOURS", "2"))


def hot_interval_minutes() -> float:
    """Minutos entre reconsultas da fatia quente. 0 desliga o nivel rapido."""
    return float(_optional("HOT_INTERVAL_MINUTES", "15"))


def hot_track_limit() -> int:
    """Quantos produtos quentes reconsultar. E o custo por ciclo rapido."""
    return _int("HOT_TRACK_LIMIT", 25)


def hot_margin_pct() -> float:
    """Folga sobre a minima historica pra um produto contar como quente."""
    return float(_optional("HOT_MARGIN_PCT", "10"))


def products_per_keyword() -> int:
    """Candidatos de catalogo por termo da watchlist.

    Medido: ~65% dos produtos que a busca devolve tem ZERO anuncio ativo em
    /products/{id}/items -- sao entradas mortas do catalogo, e `status=active`
    na busca nao filtra isso. Entao pra rastrear N produtos e preciso pedir
    cerca de 3N. Cada candidato custa 1 chamada, morto ou vivo.
    """
    return _int("PRODUCTS_PER_KEYWORD", 20)


def products_per_category() -> int:
    """Idem para os mais vendidos. Cada um custa DUAS chamadas (nome + preco)."""
    return _int("PRODUCTS_PER_CATEGORY", 10)


def discovery_interval_hours() -> int:
    """Horas entre rodadas de descoberta (busca por termo + mais vendidos).

    A descoberta e cara e quase nao muda: o top-10 do catalogo por termo e o
    ranking de mais vendidos de uma categoria sao praticamente os mesmos de uma
    hora pra outra. Rodar ela em toda rodada de 2h gasta o orcamento de API que
    deveria estar mantendo o historico de uma carteira grande -- e carteira
    grande e o unico jeito de ter volume de post.

    0 desliga o intervalo e volta a descobrir em toda rodada.
    """
    return _int("DISCOVERY_INTERVAL_HOURS", 12)


def track_limit() -> int:
    """Teto de produtos reconsultados por rodada, por fonte.

    O ML aposentou o multiget de anuncios; hoje o preco sai de
    /products/{id}/items, que e **uma chamada por produto**. Entao esse numero
    e literalmente quantas requisicoes a reconsulta gasta. Com o daemon de 2h
    o padrao da ~1.800 chamadas/dia. Suba com parcimonia -- a coleta avisa no
    log quando trunca.
    """
    return _int("ML_TRACK_LIMIT", 150)

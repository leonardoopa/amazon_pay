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
    affiliate_params: str

    @classmethod
    def load(cls) -> "MercadoLivreConfig":
        return cls(
            client_id=_get("ML_CLIENT_ID"),
            client_secret=_get("ML_CLIENT_SECRET"),
            redirect_uri=_optional("ML_REDIRECT_URI", "http://localhost:8123/callback"),
            site_id=_optional("ML_SITE_ID", "MLB"),
            affiliate_params=_optional("ML_AFFILIATE_PARAMS"),
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


def track_limit() -> int:
    """Teto de produtos reconsultados por rodada, por fonte.

    Cada 20 IDs viram 1 chamada, entao o padrao custa 20 requisicoes -- pouco
    perto do limite do ML e suficiente pra uma watchlist de algumas dezenas de
    termos. Suba se a coleta comecar a avisar que truncou.
    """
    return _int("ML_TRACK_LIMIT", 400)

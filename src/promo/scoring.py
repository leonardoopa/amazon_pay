"""Decide se uma oferta e promocao de verdade.

O "de R$X por R$Y" da loja nao vale nada: e comum inflar o preco na vespera.
A baseline aqui e a mediana do menor preco diario dos ultimos N dias.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from statistics import median

from .config import Rules
from .db import last_post, price_history
from .models import Offer, ScoredOffer


def score(
    conn: sqlite3.Connection, offer: Offer, rules: Rules
) -> ScoredOffer | None:
    """Retorna a oferta pontuada, ou None se nao merece post."""
    if not offer.available or offer.price <= 0:
        return None

    history = price_history(conn, offer.product_id, rules.baseline_window_days)
    if len(history) < rules.min_observations:
        # Produto novo no radar: so coleta, ainda nao da pra afirmar que baixou.
        return None

    baseline = median(history)
    if baseline <= 0:
        return None

    discount_pct = (baseline - offer.price) / baseline * 100
    if discount_pct < rules.min_discount_pct:
        return None

    if _in_cooldown(conn, offer, discount_pct, rules):
        return None

    return ScoredOffer(
        offer=offer,
        baseline=baseline,
        discount_pct=discount_pct,
        observations=len(history),
        lowest_ever=offer.price <= min(history),
    )


def _in_cooldown(
    conn: sqlite3.Connection, offer: Offer, discount_pct: float, rules: Rules
) -> bool:
    """Evita repetir o mesmo produto no grupo, salvo se caiu bem mais."""
    previous = last_post(conn, offer.product_id)
    if previous is None:
        return False

    sent_at = datetime.fromisoformat(previous["created_at"])
    if datetime.now(sent_at.tzinfo) - sent_at > timedelta(days=rules.repost_cooldown_days):
        return False

    # Dentro do cooldown, so repassa se o desconto melhorou 10 p.p. ou mais.
    return discount_pct < previous["discount_pct"] + 10

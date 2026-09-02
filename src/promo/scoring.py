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


def score(conn: sqlite3.Connection, offer: Offer, rules: Rules) -> ScoredOffer | None:
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
    # Preco igual a mediana nao e oferta, e o preco de sempre. O piso sozinho
    # nao pega isso quando ele e zero -- e ele e zero nos temas prioritarios,
    # por `PRIORITY_MIN_DISCOUNT_PCT`. Custou um post real: "TESTO ESSENCIAL"
    # saiu a R$ 56,77 com baseline R$ 56,77, anunciado como -0%.
    #
    # O repasse nao precisava desta guarda porque `score_campaign` ja exige
    # preco riscado MAIOR que o atual; aqui a baseline e calculada, e nada
    # impedia que ela fosse igual.
    if offer.price >= baseline:
        return None
    if discount_pct < rules.min_discount_pct:
        return None
    if baseline - offer.price < rules.min_discount_brl:
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
    if datetime.now(sent_at.tzinfo) - sent_at > timedelta(
        days=rules.repost_cooldown_days
    ):
        return False

    # Dentro do cooldown, so repassa se o desconto melhorou 10 p.p. ou mais.
    return discount_pct < previous["discount_pct"] + 10


def score_campaign(
    conn: sqlite3.Connection, offer: Offer, rules: Rules
) -> ScoredOffer | None:
    """Pontua oferta da vitrine do ML, sem historico proprio.

    Existe pra dar volume desde o primeiro dia: a baseline precisa de dias pra
    amadurecer, e grupo mudo nao segura ninguem. O desconto aqui e contra o
    preco riscado da LOJA, entao o post sai marcado como nao verificado --
    `verified=False` -- e o texto nao pode afirmar que o preco caiu de verdade.

    Ainda assim passa pelo cooldown: repetir o mesmo produto no grupo cansa
    igual, venha ele da vitrine ou da nossa medicao.
    """
    if not offer.available or offer.price <= 0 or not offer.original_price:
        return None

    desconto = (offer.original_price - offer.price) / offer.original_price * 100
    if desconto < rules.min_discount_pct:
        return None
    if offer.original_price - offer.price < rules.min_discount_brl:
        return None

    if _in_cooldown(conn, offer, desconto, rules):
        return None

    return ScoredOffer(
        offer=offer,
        baseline=offer.original_price,
        discount_pct=desconto,
        observations=0,
        lowest_ever=False,
        verified=False,
    )

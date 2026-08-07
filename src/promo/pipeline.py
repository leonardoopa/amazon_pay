"""Coleta -> registra historico -> filtra -> escreve -> entrega."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from .config import (
    AmazonConfig,
    MercadoLivreConfig,
    MissingConfig,
    ROOT,
    Rules,
    WhatsAppConfig,
    track_limit,
)
from .copywriter import Copywriter, fallback_copy
from .db import (
    connect,
    create_post,
    mark_post_failed,
    mark_post_sent,
    pending_posts,
    record_offer,
    tracked_external_ids,
)
from .delivery import WhatsApp, WindowClosed
from .models import Offer, ScoredOffer
from .scoring import score
from .sources.amazon import Amazon
from .sources.mercadolivre import MercadoLivre

log = logging.getLogger("promo")


@dataclass
class Watch:
    term: str
    max_price: float | None = None


def load_watchlist(path: Path | None = None) -> list[Watch]:
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Watch(term=entry["term"], max_price=entry.get("max_price"))
        for entry in data["keywords"]
    ]


def build_sources() -> list:
    """Amazon e opcional: sem credencial (ou sem as 3 vendas), segue so com o ML."""
    sources: list = []
    try:
        sources.append(MercadoLivre(MercadoLivreConfig.load()))
    except MissingConfig as exc:
        log.warning("Mercado Livre desativado: %s", exc)

    try:
        sources.append(Amazon(AmazonConfig.load()))
    except MissingConfig as exc:
        log.info("Amazon desativada: %s", exc)

    if not sources:
        raise MissingConfig("Nenhuma fonte configurada. Preencha o .env.")
    return sources


def collect(
    sources: list, watchlist: list[Watch], rules: Rules | None = None
) -> list[Offer]:
    """Descobre produtos novos pela busca e reconsulta os que ja conhecemos."""
    rules = rules or Rules.load()
    offers: list[Offer] = []
    for source in sources:
        for watch in watchlist:
            try:
                found = source.search(watch.term)
            except Exception as exc:  # noqa: BLE001 - uma fonte quebrada nao derruba a rodada
                log.warning("%s falhou em '%s': %s", source.name, watch.term, exc)
                continue
            if watch.max_price is not None:
                found = [o for o in found if o.price <= watch.max_price]
            offers.extend(found)
            log.info("%s: %d ofertas para '%s'", source.name, len(found), watch.term)

        offers.extend(refetch_tracked(source, rules))
    return offers


def refetch_tracked(source, rules: Rules) -> list[Offer]:
    """Reconsulta por ID os produtos ja no banco.

    Sem isso o historico so avanca enquanto o produto continuar no top 50 da
    busca, e quase nenhum chega aos MIN_OBSERVATIONS dias exigidos pelo filtro.

    Sem max_price aqui de proposito: o produto ja passou pelo teto quando foi
    descoberto, e se o preco subiu acima dele isso e justamente a informacao
    que faz a baseline valer.
    """
    fetch = getattr(source, "fetch_by_ids", None)
    if fetch is None:
        return []  # fonte sem multiget (Amazon)

    limit = track_limit()
    with connect() as conn:
        ids = tracked_external_ids(conn, source.name, rules.baseline_window_days, limit)

    if not ids:
        return []
    if len(ids) == limit:
        log.warning(
            "%s: teto de %d produtos reconsultados atingido; o resto fica sem "
            "observacao nesta rodada (suba ML_TRACK_LIMIT)",
            source.name,
            limit,
        )

    try:
        found = fetch(ids)
    except Exception as exc:  # noqa: BLE001 - idem: nao derruba a rodada
        log.warning("%s falhou ao reconsultar %d produtos: %s", source.name, len(ids), exc)
        return []

    log.info("%s: %d de %d produtos reconsultados", source.name, len(found), len(ids))
    return found


def run(dry_run: bool = False) -> list[ScoredOffer]:
    rules = Rules.load()
    sources = build_sources()
    by_name = {source.name: source for source in sources}

    offers = collect(sources, load_watchlist(), rules)

    picked: list[ScoredOffer] = []
    with connect() as conn:
        for offer in offers:
            record_offer(conn, offer)

        # Deduplica mantendo o menor preco por produto nesta rodada.
        unique: dict[str, Offer] = {}
        for offer in offers:
            current = unique.get(offer.product_id)
            if current is None or offer.price < current.price:
                unique[offer.product_id] = offer

        for offer in unique.values():
            scored = score(conn, offer, rules)
            if scored is not None:
                picked.append(scored)

    picked.sort(key=lambda s: s.discount_pct, reverse=True)
    picked = picked[: rules.max_offers_per_run]
    log.info("%d ofertas passaram no filtro", len(picked))

    if not picked:
        return []

    copywriter = Copywriter()
    drafts: list[tuple[ScoredOffer, str]] = []
    for scored in picked:
        link = by_name[scored.offer.source].affiliate_url(scored.offer)
        try:
            text = copywriter.write(scored, link)
        except Exception as exc:  # noqa: BLE001 - sem IA ainda da pra postar
            log.warning("Claude falhou, usando texto padrao: %s", exc)
            text = fallback_copy(scored, link)
        drafts.append((scored, text))

    if dry_run:
        for _, text in drafts:
            print("\n" + "-" * 40 + "\n" + text)
        return picked

    deliver(drafts)
    return picked


def deliver(drafts: list[tuple[ScoredOffer, str]]) -> None:
    """Enfileira os posts novos e drena a fila (incluindo o que sobrou de antes)."""
    with connect() as conn:
        for scored, text in drafts:
            create_post(
                conn,
                scored.offer.product_id,
                scored.offer.price,
                scored.baseline,
                scored.discount_pct,
                text,
            )
    flush_pending()


def flush_pending() -> None:
    whatsapp = WhatsApp(WhatsAppConfig.load())

    with connect() as conn:
        queue = [(row["id"], row["copy"]) for row in pending_posts(conn)]

    if not queue:
        log.info("Nada pendente na fila.")
        return

    for index, (post_id, text) in enumerate(queue):
        try:
            whatsapp.send_text(text)
        except WindowClosed:
            # Fora da janela de 24h so passa template. Pinga voce pedindo uma
            # resposta qualquer -- isso reabre a janela e a proxima rodada
            # (ou `promo flush`) entrega o que ficou na fila.
            remaining = len(queue) - index
            log.warning("Janela de 24h fechada, %d na fila. Enviando template.", remaining)
            whatsapp.send_ping_template(remaining)
            return
        except Exception as exc:  # noqa: BLE001
            log.error("Falha ao enviar post %d: %s", post_id, exc)
            with connect() as conn:
                mark_post_failed(conn, post_id, str(exc))
            continue

        with connect() as conn:
            mark_post_sent(conn, post_id)

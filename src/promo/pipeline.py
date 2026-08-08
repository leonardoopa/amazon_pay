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
    tracked_products,
)
from .delivery import NotConnected, WindowClosed, build_delivery
from .models import Offer, ScoredOffer
from .scoring import score
from .sources.amazon import Amazon
from .sources.mercadolivre import MercadoLivre

log = logging.getLogger("promo")


@dataclass
class Watch:
    term: str
    max_price: float | None = None


@dataclass
class Category:
    """Categoria acompanhada pelos mais vendidos do ML."""

    id: str
    name: str = ""
    max_price: float | None = None


def load_watchlist(path: Path | None = None) -> list[Watch]:
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Watch(term=entry["term"], max_price=entry.get("max_price"))
        for entry in data["keywords"]
    ]


def load_categories(path: Path | None = None) -> list[Category]:
    """Categorias do watchlist.json. Ausente e o normal -- o campo e opcional."""
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Category(
            id=entry["id"],
            name=entry.get("name", ""),
            max_price=entry.get("max_price"),
        )
        for entry in data.get("categories", [])
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
    sources: list,
    watchlist: list[Watch],
    rules: Rules | None = None,
    categories: list[Category] | None = None,
) -> list[Offer]:
    """Descobre produtos novos e reconsulta os que ja conhecemos.

    Duas vias de descoberta, complementares: os termos da watchlist acham o que
    voce sabe que quer, e os mais vendidos por categoria acham o que voce nao
    pensaria em procurar. As duas alimentam o mesmo historico de precos.
    """
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

        offers.extend(collect_categories(source, categories or []))
        offers.extend(refetch_tracked(source, rules))
    return offers


def collect_categories(source, categories: list[Category]) -> list[Offer]:
    """Mais vendidos das categorias acompanhadas.

    Fonte sem `highlights` (Amazon) e ignorada em silencio -- categoria e
    conceito do ML, nao um recurso que toda loja precise ter.
    """
    destaques = getattr(source, "highlights", None)
    if destaques is None or not categories:
        return []

    offers: list[Offer] = []
    for category in categories:
        rotulo = category.name or category.id
        try:
            found = destaques(category.id)
        except Exception as exc:  # noqa: BLE001 - idem: nao derruba a rodada
            log.warning("%s falhou na categoria '%s': %s", source.name, rotulo, exc)
            continue
        if category.max_price is not None:
            found = [o for o in found if o.price <= category.max_price]
        offers.extend(found)
        log.info("%s: %d mais vendidos em '%s'", source.name, len(found), rotulo)
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
        tracked = tracked_products(conn, source.name, rules.baseline_window_days, limit)

    if not tracked:
        return []
    if len(tracked) == limit:
        log.warning(
            "%s: teto de %d produtos reconsultados atingido; o resto fica sem "
            "observacao nesta rodada (suba ML_TRACK_LIMIT)",
            source.name,
            limit,
        )

    try:
        found = fetch(tracked)
    except Exception as exc:  # noqa: BLE001 - idem: nao derruba a rodada
        log.warning(
            "%s falhou ao reconsultar %d produtos: %s", source.name, len(tracked), exc
        )
        return []

    log.info("%s: %d de %d produtos reconsultados", source.name, len(found), len(tracked))
    return found


def run(dry_run: bool = False) -> list[ScoredOffer]:
    rules = Rules.load()
    sources = build_sources()
    by_name = {source.name: source for source in sources}

    offers = collect(sources, load_watchlist(), rules, load_categories())

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
    sem_link: list[ScoredOffer] = []

    for scored in picked:
        link = by_name[scored.offer.source].affiliate_url(scored.offer)
        if not link:
            # Sem link de afiliado o post nao rende nada, e nao da pra montar
            # um: o ML so emite pelo Link Builder. Segura a oferta em vez de
            # queimar ela num post sem comissao.
            sem_link.append(scored)
            continue
        try:
            text = copywriter.write(scored, link)
        except Exception as exc:  # noqa: BLE001 - sem IA ainda da pra postar
            log.warning("Gemini falhou, usando texto padrao: %s", exc)
            text = fallback_copy(scored, link)
        drafts.append((scored, text))

    if sem_link:
        log.warning(
            "%d oferta(s) seguraram por falta de link de afiliado. "
            "Gere no Link Builder e rode `promo link`:",
            len(sem_link),
        )
        for scored in sem_link:
            log.warning("  %s  %s", scored.offer.external_id, scored.offer.url)

    if dry_run:
        for _, text in drafts:
            print("\n" + "-" * 40 + "\n" + text)
        return picked

    if drafts:
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
                scored.offer.image_url,
            )
    flush_pending()


def flush_pending() -> None:
    delivery = build_delivery()

    with connect() as conn:
        queue = [(row["id"], row["copy"], row["image_url"]) for row in pending_posts(conn)]

    if not queue:
        log.info("Nada pendente na fila.")
        return

    for index, (post_id, text, image_url) in enumerate(queue):
        try:
            delivery.send_post(text, image_url)
        except WindowClosed:
            # So o backend 'cloud' chega aqui. Fora da janela de 24h so passa
            # template: pinga voce pedindo uma resposta qualquer, o que reabre a
            # janela pra proxima rodada (ou `promo flush`) drenar o resto.
            remaining = len(queue) - index
            log.warning("Janela de 24h fechada, %d na fila. Enviando template.", remaining)
            delivery.send_ping_template(remaining)
            return
        except NotConnected as exc:
            # So o backend 'evolution'. Reparar exige o QR na mao, entao insistir
            # nos outros posts da fila so gastaria tentativa a toa -- eles ficam
            # 'pending' e saem quando o numero voltar.
            log.error(
                "Instancia da Evolution caiu, %d post(s) ficam na fila: %s",
                len(queue) - index,
                exc,
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.error("Falha ao enviar post %d: %s", post_id, exc)
            with connect() as conn:
                mark_post_failed(conn, post_id, str(exc))
            continue

        with connect() as conn:
            mark_post_sent(conn, post_id)

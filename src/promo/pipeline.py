"""Coleta -> registra historico -> filtra -> escreve -> entrega."""

from __future__ import annotations

import json
import logging
import random
import time
from datetime import date, datetime
from dataclasses import dataclass
from pathlib import Path

from .config import (
    AmazonConfig,
    MercadoLivreConfig,
    MissingConfig,
    ROOT,
    Rules,
    discovery_interval_hours,
    drip_interval_seconds,
    full_refetch_interval_hours,
    hot_interval_minutes,
    hot_margin_pct,
    hot_track_limit,
    max_pending_queue,
    ofertas_pages,
    post_max_age_minutes,
    quiet_drip_multiplier,
    quiet_max_offers_per_run,
    quiet_window,
    run_interval_seconds,
    track_limit,
)
from .copywriter import Copywriter, fallback_copy
from .db import (
    connect,
    create_post,
    expire_stale_posts,
    mark_post_failed,
    mark_post_sent,
    hot_products,
    hours_since,
    pending_posts,
    recent_headlines,
    record_offer,
    set_meta,
    tracked_products,
)
from .delivery import NotConnected, WindowClosed, build_delivery
from .models import Offer, ScoredOffer
from .scoring import score, score_campaign
from .sources.amazon import Amazon
from .sources.mercadolivre import MercadoLivre
from .sources.ml_ofertas import MLOfertas

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


def load_coupon(path: Path | None = None) -> str | None:
    """Cupom de campanha do watchlist.json, se ainda estiver valendo.

    Cupom do ML e de campanha (vale no site inteiro por alguns dias), nao por
    produto -- por isso mora num campo so, nao no cadastro de cada oferta. E
    por isso tem `ate`: cupom vencido no post e pior que post sem cupom, porque
    a pessoa clica, tenta, falha, e passa a desconfiar do grupo.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    cupom = data.get("coupon") or {}
    codigo, ate = cupom.get("code"), cupom.get("ate")
    if not codigo:
        return None
    if ate and date.fromisoformat(ate) < date.today():
        log.info("Cupom %s venceu em %s; post sai sem cupom.", codigo, ate)
        return None
    return codigo


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

    # A vitrine nao precisa de OAuth (e pagina publica), mas reaproveita o
    # Link Builder da fonte de catalogo pra gerar link de afiliado.
    if ofertas_pages() > 0:
        builder = None
        for fonte in sources:
            builder = getattr(fonte, "_link_builder", lambda: None)()
            if builder:
                break
        sources.append(MLOfertas(builder))

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
    descobrir = _time_to_discover()
    offers: list[Offer] = []
    for source in sources:
        # Nem toda fonte busca por termo: a vitrine e uma foto do que o ML
        # esta promovendo hoje, sem consulta. Sem esta guarda, cada termo da
        # watchlist virava um WARNING por rodada -- 35 linhas de ruido que
        # escondiam qualquer falha de verdade no meio.
        buscar = getattr(source, "search", None)
        for watch in watchlist if (descobrir and buscar) else []:
            try:
                found = buscar(watch.term)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - uma fonte quebrada nao derruba a rodada
                log.warning("%s falhou em '%s': %s", source.name, watch.term, exc)
                continue
            if watch.max_price is not None:
                found = [o for o in found if o.price <= watch.max_price]
            offers.extend(found)
            log.info("%s: %d ofertas para '%s'", source.name, len(found), watch.term)

        if descobrir:
            offers.extend(collect_categories(source, categories or []))
        offers.extend(collect_vitrine(source))
        if _due("last_full_refetch", full_refetch_interval_hours()):
            offers.extend(refetch_tracked(source, rules))
            _stamp("last_full_refetch")
        offers.extend(refetch_hot(source))

    if descobrir:
        _stamp(DISCOVERY_KEY)
    return offers


DISCOVERY_KEY = "last_discovery_at"


def _iso_now() -> str:
    from .db import now

    return now().isoformat()


def collect_vitrine(source) -> list[Offer]:
    """Ofertas do dia do ML. Uma requisicao traz ~45 produtos.

    Roda em toda rodada de proposito: e a fonte mais barata que temos, e a
    vitrine gira ao longo do dia. Gravar essas ofertas tambem alimenta o
    historico -- produto que hoje entra como repasse pode, em alguns dias,
    virar oferta verificada pela nossa propria medicao.
    """
    fetch = getattr(source, "fetch", None)
    if fetch is None:
        return []

    try:
        found = fetch(ofertas_pages())
    except Exception as exc:  # noqa: BLE001 - HTML muda; nao derruba a rodada
        log.warning("%s falhou na vitrine: %s", source.name, exc)
        return []

    log.info("%s: %d ofertas da vitrine", source.name, len(found))
    return found


def refetch_hot(source) -> list[Offer]:
    """Nivel rapido: so os produtos colados na propria minima historica.

    A carteira inteira nao cabe num ciclo de minutos -- 400 produtos a cada 15
    min sao 38 mil chamadas por dia. Mas quase nenhum deles pode virar post na
    proxima hora: quem esta 40% acima da propria minima nao vira oferta com
    mais uma queda pequena. Reconsultar so a fatia quente pega a oferta
    relampago pagando por dezenas de produtos, nao por centenas.
    """
    intervalo = hot_interval_minutes()
    fetch = getattr(source, "fetch_by_ids", None)
    if intervalo <= 0 or fetch is None:
        return []
    if not _due("last_hot_refetch", intervalo / 60):
        return []

    with connect() as conn:
        quentes = hot_products(conn, source.name, hot_margin_pct(), hot_track_limit())

    _stamp("last_hot_refetch")
    if not quentes:
        return []

    try:
        found = fetch(quentes)
    except Exception as exc:  # noqa: BLE001 - idem: nao derruba a rodada
        log.warning("%s falhou no ciclo rapido: %s", source.name, exc)
        return []

    log.info("%s: %d produtos quentes reconsultados", source.name, len(found))
    return found


def _due(chave: str, intervalo_horas: float) -> bool:
    """Passou tempo suficiente desde a ultima vez que `chave` foi carimbada?"""
    with connect() as conn:
        passadas = hours_since(conn, chave)
    return passadas is None or passadas >= intervalo_horas


def _stamp(chave: str) -> None:
    with connect() as conn:
        set_meta(conn, chave, _iso_now())


def _time_to_discover() -> bool:
    """Descobrir agora, ou so manter o historico do que ja conhecemos?

    A reconsulta roda sempre: e ela que faz a baseline existir. A descoberta e
    que e intermitente, porque e cara e o resultado dela muda devagar.
    """
    intervalo = discovery_interval_hours()
    if intervalo <= 0:
        return True

    with connect() as conn:
        passadas = hours_since(conn, DISCOVERY_KEY)

    if passadas is None:
        return True  # primeira rodada: sem carteira, nao ha o que reconsultar
    if passadas >= intervalo:
        return True

    log.info(
        "Descoberta pulada (rodou ha %.1fh, intervalo %dh). So reconsulta.",
        passadas,
        intervalo,
    )
    return False


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

    log.info(
        "%s: %d de %d produtos reconsultados", source.name, len(found), len(tracked)
    )
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

        # Verificadas primeiro: sao as unicas que podem afirmar que o preco
        # caiu de verdade, e sao o motivo de alguem preferir este grupo.
        for offer in unique.values():
            scored = score(conn, offer, rules)
            if scored is not None:
                picked.append(scored)

        ja_escolhido = {s.offer.product_id for s in picked}
        repasses: list[ScoredOffer] = []
        for offer in unique.values():
            if offer.source != "ml_ofertas" or offer.product_id in ja_escolhido:
                continue
            scored = score_campaign(conn, offer, rules)
            if scored is not None:
                repasses.append(scored)

    picked.sort(key=lambda s: s.discount_pct, reverse=True)
    repasses.sort(key=lambda s: s.discount_pct, reverse=True)

    # Contrapressao: a coleta produz mais rapido do que a entrega gotejada
    # drena. Sem teto, a fila vira um deposito e o grupo passa a receber oferta
    # de horas atras -- que pode nem existir mais no preco anunciado.
    with connect() as conn:
        # Expira antes de contar: post velho vai ser descartado no flush desta
        # mesma rodada, e deixa-lo no numero faria a fila parecer cheia e
        # recusar oferta nova que tinha lugar.
        expire_stale_posts(conn)
        na_fila = len(pending_posts(conn))

    # Na janela de silencio a entrega anda devagar; produzir no ritmo normal so
    # encheria a fila de post que morre como `expired` antes de amanhecer.
    teto = rules.max_offers_per_run
    if em_horario_silencioso():
        teto = min(teto, quiet_max_offers_per_run())
        log.info("Horario silencioso: aceitando no maximo %d oferta(s).", teto)

    espaco = max(0, min(teto, max_pending_queue() - na_fila))
    if espaco < teto:
        log.info(
            "Fila com %d post(s); aceitando so mais %d nesta rodada.", na_fila, espaco
        )

    # Repasse preenche o que sobrou da cota, nunca desloca uma verificada.
    picked = (picked + repasses)[:espaco]
    verificadas = sum(1 for s in picked if s.verified)
    log.info(
        "%d ofertas selecionadas (%d verificadas, %d repasse da vitrine)",
        len(picked),
        verificadas,
        len(picked) - verificadas,
    )

    if not picked:
        return []

    copywriter = Copywriter()
    cupom = load_coupon()
    with connect() as conn:
        recentes = recent_headlines(conn)
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
            text = copywriter.write(scored, link, cupom, recentes)
        except Exception as exc:  # noqa: BLE001 - sem IA ainda da pra postar
            log.warning("Gemini falhou, usando texto padrao: %s", exc)
            text = fallback_copy(scored, link, cupom)
        # Alimenta a proxima chamada desta mesma rodada: sem isso as 5 ofertas
        # do lote saem com a mesma formula, que e o caso mais visivel de todos.
        recentes.append(text.strip().splitlines()[0].strip())
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


def em_horario_silencioso(agora: datetime | None = None) -> bool:
    """A hora atual cai na janela de silencio?

    A janela normal cruza a meia-noite (23:30 as 07:30), entao a comparacao
    inverte quando o inicio e maior que o fim: dentro dela e "depois do inicio
    OU antes do fim", nao "entre os dois".

    Compara hora local de proposito. O que importa e a hora de quem le no
    grupo, e o Dockerfile fixa TZ=America/Sao_Paulo no container.
    """
    janela = quiet_window()
    if janela is None:
        return False

    inicio, fim = janela
    agora_hora = (agora or datetime.now()).time()

    if inicio <= fim:
        return inicio <= agora_hora < fim
    return agora_hora >= inicio or agora_hora < fim


def _drip_gap() -> float:
    """Intervalo ate o proximo post, com variacao.

    O jitter importa: intervalo exato de 150s em 150s e assinatura de robo, e
    o numero que assina os posts e um chip pareado por cliente nao oficial.
    Os grupos reais mandam a cada 2-3 minutos, sem regularidade nenhuma.

    Dentro da janela de silencio o intervalo estica pelo multiplicador. Com o
    padrao (6) ele passa do orcamento de drenagem de uma rodada, entao sai um
    post por rodada em vez de cinco.
    """
    base = drip_interval_seconds()
    if em_horario_silencioso():
        base *= quiet_drip_multiplier()
    return base * random.uniform(1 - DRIP_JITTER, 1 + DRIP_JITTER)


# Variacao aplicada ao intervalo: 0.4 = de 60% a 140% do valor configurado.
DRIP_JITTER = 0.4


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
                # Sem isso a distincao morria aqui: o site lia repasse da
                # vitrine como desconto medido por nos.
                verified=scored.verified,
            )
    flush_pending()


def flush_pending(
    budget_seconds: float | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> None:
    """Drena a fila aos poucos, um post de cada vez.

    Nao e enfeite. Dez mensagens seguidas no mesmo segundo tem dois problemas:
    o grupo le como flood e silencia a conversa, e o padrao -- rajada perfeita,
    intervalo zero -- e exatamente o que o antifraude da Meta procura num
    numero pareado por Baileys. Os grupos que funcionam postam de 2 em 3
    minutos, com intervalo irregular.

    `budget_seconds` limita quanto tempo a drenagem segura a rodada. O que nao
    couber fica na fila e sai na proxima -- por isso o daemon acorda a cada 15
    minutos em vez de a cada 2 horas.

    sleep/monotonic sao injetaveis pra o teste nao dormir de verdade.
    """
    delivery = build_delivery()

    with connect() as conn:
        # Antes de drenar, joga fora o que envelheceu. O texto na fila carrega
        # um preco com hora; passado o prazo ele deixa de ser medicao e passa a
        # ser chute -- e chute e o que o grupo existe para nao receber.
        velhos = expire_stale_posts(conn)
        queue = [
            (row["id"], row["copy"], row["image_url"]) for row in pending_posts(conn)
        ]

    if velhos:
        log.warning(
            "%d post(s) descartados por preco velho (mais de %d min na fila). "
            "Fila produzindo mais rapido do que o gotejamento drena.",
            len(velhos),
            post_max_age_minutes(),
        )

    if not queue:
        log.info("Nada pendente na fila.")
        return

    if budget_seconds is None:
        # Sobra de proposito: a drenagem nao pode invadir a proxima rodada.
        budget_seconds = run_interval_seconds() * 0.8

    inicio = monotonic()
    enviados = 0

    for index, (post_id, text, image_url) in enumerate(queue):
        if enviados:
            # Decidir ANTES de dormir. Checar so o tempo ja gasto deixava a
            # drenagem estourar o orcamento por um intervalo inteiro -- com
            # gap de 150s isso invadia a rodada seguinte do daemon.
            espera = _drip_gap()
            if monotonic() - inicio + espera >= budget_seconds:
                log.info(
                    "Orcamento de %.0fs esgotado; %d post(s) ficam pra proxima rodada.",
                    budget_seconds,
                    len(queue) - index,
                )
                return
            log.info("Aguardando %.0fs antes do proximo post.", espera)
            sleep(espera)

        try:
            delivery.send_post(text, image_url)
        except WindowClosed:
            # So o backend 'cloud' chega aqui. Fora da janela de 24h so passa
            # template: pinga voce pedindo uma resposta qualquer, o que reabre a
            # janela pra proxima rodada (ou `promo flush`) drenar o resto.
            remaining = len(queue) - index
            log.warning(
                "Janela de 24h fechada, %d na fila. Enviando template.", remaining
            )
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
        enviados += 1

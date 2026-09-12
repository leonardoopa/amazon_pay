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
    # Piso proprio: a verificada afirma que o preco caiu, e a afirmacao precisa
    # de um numero que a sustente. Ele nao e afrouxado por tema prioritario --
    # `regras_do_tema` so mexe em `min_discount_pct`.
    piso = (
        rules.min_discount_pct_verified
        if rules.min_discount_pct_verified is not None
        else rules.min_discount_pct
    )
    if discount_pct < piso:
        return None
    if baseline - offer.price < rules.min_discount_brl:
        return None

    if _in_cooldown(conn, offer, rules):
        return None

    return ScoredOffer(
        offer=offer,
        baseline=baseline,
        discount_pct=discount_pct,
        observations=len(history),
        lowest_ever=offer.price <= min(history),
    )


# Quanto o preco precisa cair, dentro do cooldown, para o mesmo produto sair
# de novo. Dez por cento e o que o leitor percebe como "baixou mais"; abaixo
# disso ele so ve o mesmo post outra vez.
QUEDA_QUE_LIBERA = 0.10


def _in_cooldown(conn: sqlite3.Connection, offer: Offer, rules: Rules) -> bool:
    """Evita repetir o mesmo produto no grupo, salvo se o PRECO caiu bem mais.

    A excecao olha o preco, e nao o desconto, porque desconto aqui nao e uma
    coisa so: o mesmo produto, pelo mesmo preco, mede 28,6% contra a nossa
    mediana e 54,6% contra o riscado da loja. As duas portas -- `score` e
    `score_campaign` -- pontuam o mesmo anuncio com reguas diferentes, e a
    diferenca entre elas passava dos 10 pontos que liberavam a repeticao.

    O efeito disso em producao, medido em 12/09/2026 sobre 72 horas: 568
    repeticoes do mesmo produto, 487 delas (86%) com o preco IDENTICO ao do
    post anterior. Kit de meias a R$ 49,99 as 12:20 e de novo as 13:41, bicicleta
    ergometrica a R$ 420,80 em quatro dias seguidos. Nenhum preco havia mudado
    -- so a regua.

    Com o preco como criterio a pergunta volta a ser a certa: "ficou mais
    barato do que quando eu postei?". Se nao ficou, e o mesmo post.
    """
    previous = last_post(conn, offer.product_id)
    if previous is None:
        return False

    sent_at = datetime.fromisoformat(previous["created_at"])
    if datetime.now(sent_at.tzinfo) - sent_at > timedelta(
        days=rules.repost_cooldown_days
    ):
        return False

    anterior = previous["price"] or 0.0
    if anterior <= 0:
        return True  # post antigo sem preco gravado: na duvida, nao repete

    return offer.price > anterior * (1 - QUEDA_QUE_LIBERA)


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

    if _in_cooldown(conn, offer, rules):
        return None

    return ScoredOffer(
        offer=offer,
        baseline=offer.original_price,
        discount_pct=desconto,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


def score_pista(
    conn: sqlite3.Connection, offer: Offer, rules: Rules
) -> ScoredOffer | None:
    """Pontua o que outro grupo acabou de postar, pela curadoria deles.

    A terceira porta, e a mais fraca das tres de proposito.

    `score` exige historico nosso e `score_campaign` exige preco riscado no
    anuncio. Um produto que o grupo vizinho acabou de achar nao tem nem um nem
    outro: e novo no nosso radar, e o ML so devolve `original_price` em parte
    dos anuncios -- medido em 09/09/2026, 9 de 13. Por isso a fonte rendia
    quase nada: as pistas morriam antes de qualquer ordenacao.

    O que sustenta esta porta nao e uma medicao nossa, e o fato de um grupo de
    990 membros que vive disso ter escolhido esse produto agora. E uma aposta
    diferente, e o post precisa ser honesto sobre isso: sem baseline e sem
    riscado nao ha desconto nenhum para afirmar, entao `discount_pct` fica em
    zero e `verified` em False. Quem escreve o texto le esses dois campos e
    anuncia o preco, nao uma queda.

    Sem piso de desconto, por decisao do dono em 09/09/2026: o filtro aqui e o
    deles.

    Com o cooldown de repeticao, pelo que aconteceu no mesmo dia. A primeira
    versao dispensava os dois, e o resultado foi o mesmo produto saindo seis
    vezes: 04:53, 05:59, 07:28, 08:42, 10:03 e 13:10. A causa e estrutural --
    a fonte rele as mesmas 50 mensagens a cada rodada, entao a pista continua
    "nova" enquanto o post dela nao envelhecer. Sem esta trava a repeticao nao
    e um risco, e o comportamento garantido.

    O cooldown e definitivo aqui: a excecao de `_in_cooldown` so libera quem
    ficou 10% mais barato do que estava no post anterior, e a pista que a fonte
    rele de 15 em 15 minutos volta sempre pelo mesmo preco. Uma pista sai uma
    vez por `REPOST_COOLDOWN_DAYS`, e pronto.

    Quando o anuncio TEM riscado, `score_campaign` pontua melhor e roda antes
    -- esta funcao so recebe o que sobrou. Desde que o preco passou a ser lido
    da propria pagina do produto, isso e a maioria: 22 dos 25 links medidos em
    12/09/2026 vieram com riscado.
    """
    if not offer.available or offer.price <= 0:
        return None

    if _in_cooldown(conn, offer, rules):
        return None

    return ScoredOffer(
        offer=offer,
        baseline=offer.price,  # sem medicao: a "baseline" e o proprio preco
        discount_pct=0.0,
        observations=0,
        lowest_ever=False,
        verified=False,
        sem_medicao=True,
    )

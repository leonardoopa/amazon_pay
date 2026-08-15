"""Resolucao de link de afiliado, compartilhada entre as fontes do ML.

Existe porque a duplicacao custou dinheiro. Quando a vitrine ganhou a propria
copia desta logica, a copia gravava `affiliate_blocked` mas nunca lia -- entao
todo produto que o programa recusa voltava ao painel em cada rodada, pra
sempre. Uma funcao so, dois chamadores, mesmo comportamento.
"""

from __future__ import annotations

import logging

from ..db import (
    affiliate_blocked,
    affiliate_link,
    connect,
    mark_affiliate_blocked,
    save_affiliate_link,
)
from ..models import Offer

log = logging.getLogger("promo")


def resolve_affiliate_link(builder, offer: Offer) -> str | None:
    """Link de afiliado do produto: do banco, do painel, ou None.

    Ordem importa. O banco vem primeiro e nao e so cache: e o que mantem os
    links dos produtos ja postados quando o cookie do painel expira. Depois vem
    a recusa registrada, que evita gastar consulta com anuncio inelegivel. So
    entao o painel.
    """
    with connect() as conn:
        existente = affiliate_link(conn, offer.product_id)
        if existente:
            return existente
        recusado = affiliate_blocked(conn, offer.product_id)

    if recusado:
        log.debug("%s fora do programa de afiliados: %s", offer.external_id, recusado)
        return None

    if builder is None:
        return None  # sem sessao: cai no fluxo manual do `promo link`

    try:
        resultado = builder.create([offer.url])
    except Exception as exc:  # noqa: BLE001 - painel fora do ar segura a oferta,
        # nao derruba a rodada. Sem link o post nao renderia comissao nenhuma.
        log.warning("Link Builder falhou para %s: %s", offer.external_id, exc)
        return None

    motivo = resultado.recusados.get(offer.url)
    if motivo:
        log.info("%s fora do programa de afiliados: %s", offer.external_id, motivo)
        with connect() as conn:
            mark_affiliate_blocked(conn, offer.product_id, motivo)
        return None

    link = resultado.links.get(offer.url)
    if not link:
        # Nem link nem motivo: soluco do painel. Nao bloqueia -- a proxima
        # rodada tenta de novo.
        log.warning("Link Builder nao devolveu link para %s", offer.external_id)
        return None

    with connect() as conn:
        save_affiliate_link(conn, offer.product_id, link)
    return link

"""Oferta escolhida a mao, enfileirada como qualquer outra.

Um caminho, tres portas: o comando `promo amazon-add`, o `POST /amazon-add` da
API, e a tela do admin do site -- que chama a API, e nao o banco. Essa distancia
e de proposito: o roteador em `web/comunidade/routers.py` proibe o site de
escrever no banco do bot, porque um `.save()` acidental corromperia o historico
de preco. A regra vale tambem para o que o dono digita.

Por que a mao: a Creators API da Amazon pede vendas qualificadas na conta
Associates e ainda nao liberou, e raspar nao e alternativa -- o Operating
Agreement proibe "any use of data mining, robots, or similar data gathering and
extraction tools" e exige que preco exibido venha da API. Entao a escolha e a
leitura do preco sao de uma pessoa; o resto e automatico.
"""

from __future__ import annotations

import logging

from .config import amazon_partner_tag
from .copywriter import Copywriter, com_convite, fallback_copy
from .db import (
    connect,
    create_post,
    init_db,
    recent_headlines,
    record_offer,
    save_affiliate_link,
)
from .models import Offer, ScoredOffer
from .sources.amazon_link import extrair_asin, imagem_do_asin, link_de_afiliado

log = logging.getLogger("promo")


class OfertaInvalida(ValueError):
    """Entrada que nao vira post. A mensagem e para quem digitou."""


def preparar_oferta_amazon(
    produto: str,
    titulo: str,
    preco: float,
    de: float,
    tag: str | None = None,
    imagem: str | None = None,
    sem_imagem: bool = False,
) -> ScoredOffer:
    """Valida a entrada e monta a oferta. Nao toca no banco.

    Separado do enfileiramento para o `--dry-run` poder mostrar o texto sem
    deixar rastro, e para o teste exercitar a validacao sem criar arquivo.
    """
    tag = (tag or amazon_partner_tag() or "").strip()
    if not tag:
        raise OfertaInvalida(
            "Sem tag de associado. Defina AMAZON_PARTNER_TAG ou informe a tag. "
            "Link sem tag e trabalho de graca para a Amazon."
        )

    asin = extrair_asin(produto)
    if not asin:
        raise OfertaInvalida(
            f"Nao achei o ASIN em {produto!r}. Use a URL do produto (a que tem "
            "/dp/) ou o ASIN de dez caracteres."
        )

    titulo = (titulo or "").strip()
    if not titulo:
        raise OfertaInvalida("O titulo do produto e obrigatorio.")

    if preco <= 0 or de <= 0:
        raise OfertaInvalida("Preco e 'de' precisam ser maiores que zero.")
    if preco >= de:
        raise OfertaInvalida(
            f"O preco (R$ {preco:.2f}) precisa ser MENOR que o de (R$ {de:.2f}) "
            "-- sem queda nao ha oferta."
        )

    offer = Offer(
        source="amazon",
        external_id=asin,
        title=titulo,
        price=preco,
        url=link_de_afiliado(asin, tag),
        original_price=de,
        image_url=None
        if sem_imagem
        else ((imagem or "").strip() or imagem_do_asin(asin)),
    )
    return ScoredOffer(
        offer=offer,
        baseline=de,
        discount_pct=(de - preco) / de * 100,
        observations=0,
        lowest_ever=False,
        # Nao medimos esta baseline: o "de" e o que a loja anuncia. Mesma
        # marcacao do repasse da vitrine do ML, e o site le esta coluna para
        # nao apresentar repasse como prova medida.
        verified=False,
    )


def escrever_texto(scored: ScoredOffer, usar_ia: bool = True) -> str:
    """O texto do post, pelo Gemini ou pelo padrao.

    Mesma queda do pipeline: se o modelo falha, o post sai com o texto padrao
    em vez de a oferta se perder.
    """
    link = scored.offer.url
    if not usar_ia:
        return com_convite(fallback_copy(scored, link))
    try:
        with connect() as conn:
            recentes = recent_headlines(conn)
        return com_convite(Copywriter().write(scored, link, None, recentes))
    except Exception as exc:  # noqa: BLE001
        log.warning("Gemini falhou (%s); usando o texto padrao.", exc)
        return com_convite(fallback_copy(scored, link))


def enfileirar(scored: ScoredOffer, texto: str) -> int:
    """Grava o produto, o link e o post pendente. Devolve o id do post."""
    offer = scored.offer
    init_db()
    with connect() as conn:
        record_offer(conn, offer)
        # Sem isso o `pending-links` voltaria a pedir link do painel do ML para
        # um produto que nem e do ML.
        save_affiliate_link(conn, offer.product_id, offer.url)
        return create_post(
            conn,
            offer.product_id,
            offer.price,
            scored.baseline,
            scored.discount_pct,
            texto,
            offer.image_url,
            verified=False,
        )

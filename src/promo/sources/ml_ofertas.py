"""Ofertas do dia do Mercado Livre, lidas da pagina publica.

Por que nao pela API: nao existe. Medido nesta conta, com token valido --
/sites/MLB/deal_ids, /deals/MLB e /sites/MLB/deals dao 404; /promotions/MLB e
/sites/MLB/search?tags=deal dao 403; /seller-promotions e do lado do vendedor
(as promocoes dos anuncios DELE). E em 218 anuncios inspecionados via
/products/{id}/items, `deal_ids` veio vazio em 218 e `original_price` em 213.

A pagina /ofertas, por outro lado, entrega 45 produtos por requisicao com o
"de" e o "por" prontos. E daqui que saem os grupos que postam de 3 em 3
minutos: eles espelham a campanha que o ML ja montou.

O preco disso: e HTML, nao contrato. Quebra quando o ML mexer no layout -- o
mesmo risco que ja aceitamos no Link Builder. Por isso o parser falha alto e
claro em vez de devolver lista vazia em silencio: oferta que sumiu por bug de
parsing e indistinguivel de dia sem oferta, e o grupo fica mudo sem ninguem
entender.

O "de" daqui e o preco riscado da LOJA, nao a nossa mediana. Ele serve pra
volume, nao como prova de desconto -- quem prova e o historico. Ver
`ScoredOffer.verified`.
"""

from __future__ import annotations

import json
import logging

import httpx

from ..models import Offer

PROVIDER = "ml_ofertas"
OFERTAS_URL = "https://www.mercadolivre.com.br/ofertas"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# O payload da pagina vive num script inline, nao num endpoint JSON.
SCRIPT_MARCA = '<script id="__NORDIC_RENDERING_CTX__"'

# A CDN serve varios tamanhos pelo mesmo id. -F e a resolucao cheia, que e a
# que a Meta precisa pra montar a imagem do post sem borrar.
CDN = "https://http2.mlstatic.com/D_NQ_NP_2X_{picture_id}-F.jpg"

log = logging.getLogger("promo")


class ParseFailed(RuntimeError):
    """O HTML mudou de forma. Falha alto: silencio vira grupo mudo."""


def _payload(html: str) -> dict:
    """Extrai o JSON embutido no script de renderizacao."""
    inicio = html.find(SCRIPT_MARCA)
    if inicio < 0:
        raise ParseFailed(
            f"Nao achei {SCRIPT_MARCA} na pagina de ofertas -- o ML mudou o layout."
        )

    corpo = html[html.find(">", inicio) + 1 : html.find("</script>", inicio)]
    if "=" not in corpo:
        raise ParseFailed("Script de contexto sem atribuicao.")

    # raw_decode em vez de loads: depois do JSON vem mais JavaScript na mesma
    # tag, e o loads reclamaria de "Extra data".
    try:
        dados, _ = json.JSONDecoder().raw_decode(corpo.split("=", 1)[1].strip())
    except ValueError as exc:
        raise ParseFailed(f"JSON da pagina de ofertas ilegivel: {exc}") from exc
    return dados


def _componente(card: dict, tipo: str) -> dict | None:
    for item in card.get("components") or []:
        if item.get("type") == tipo:
            return item
    return None


def _preco(card: dict) -> tuple[float | None, float | None]:
    """(preco atual, preco riscado da loja). O riscado pode nao existir."""
    componente = _componente(card, "price")
    if componente is None:
        return None, None

    bloco = componente.get("price") or {}
    atual = (bloco.get("current_price") or {}).get("value")

    anterior = None
    for rotulo in bloco.get("price_labels") or []:
        for valor in rotulo.get("values") or []:
            if valor.get("key") == "previous_price":
                anterior = (valor.get("price") or {}).get("value")

    return (
        float(atual) if atual is not None else None,
        float(anterior) if anterior is not None else None,
    )


def _imagem(card: dict) -> str | None:
    fotos = (card.get("pictures") or {}).get("pictures") or []
    if not fotos or not fotos[0].get("id"):
        return None
    return CDN.format(picture_id=fotos[0]["id"])


def _offer(card: dict) -> Offer | None:
    metadata = card.get("metadata") or {}
    external_id, url = metadata.get("id"), metadata.get("url")
    titulo = ((_componente(card, "title") or {}).get("title") or {}).get("text")
    preco, anterior = _preco(card)

    if not (external_id and url and titulo and preco):
        return None

    return Offer(
        source=PROVIDER,
        external_id=external_id,
        title=titulo.strip(),
        price=preco,
        # A URL vem sem esquema ("www.mercadolivre.com.br/...").
        url=url if url.startswith("http") else f"https://{url}",
        original_price=anterior,
        image_url=_imagem(card),
    )


class MLOfertas:
    """Le a vitrine de ofertas do ML. Sem OAuth: e pagina publica."""

    name = PROVIDER

    def __init__(self, link_builder=None) -> None:
        self._client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"user-agent": USER_AGENT, "accept": "text/html"},
        )
        self._builder = link_builder

    def fetch(self, pages: int = 1) -> list[Offer]:
        """Ofertas da vitrine. Cada pagina custa UMA requisicao e da ~45 itens.

        Compare com a coleta por catalogo, que gasta uma chamada por produto:
        aqui 45 produtos custam 1. E por isso que da pra rodar de minuto em
        minuto sem estourar nada.
        """
        offers: list[Offer] = []
        for pagina in range(1, max(1, pages) + 1):
            response = self._client.get(
                OFERTAS_URL, params={"page": pagina} if pagina > 1 else None
            )
            response.raise_for_status()

            items = (
                _payload(response.text)
                .get("appProps", {})
                .get("pageProps", {})
                .get("data", {})
                .get("items")
            )
            if items is None:
                raise ParseFailed(
                    "appProps.pageProps.data.items sumiu do payload -- layout mudou."
                )

            da_pagina = [o for item in items if (o := _offer(item.get("card") or {}))]
            log.info("ofertas: %d de %d cards na pagina %d", len(da_pagina), len(items), pagina)
            offers.extend(da_pagina)

            if not da_pagina:
                break  # pagina vazia: nao adianta pedir a proxima
        return offers

    # A vitrine nao tem "reconsulta por ID": ela e uma foto do momento. Produto
    # que saiu dela some sozinho, e o historico dele continua sendo mantido
    # pela coleta de catalogo se ele tambem estiver na watchlist.

    def affiliate_url(self, offer: Offer) -> str | None:
        """Mesmo Link Builder da fonte de catalogo."""
        from ..db import affiliate_link, connect, mark_affiliate_blocked, save_affiliate_link

        with connect() as conn:
            existente = affiliate_link(conn, offer.product_id)
        if existente:
            return existente
        if self._builder is None:
            return None

        try:
            resultado = self._builder.create([offer.url])
        except Exception as exc:  # noqa: BLE001 - painel fora do ar segura a oferta
            log.warning("Link Builder falhou para %s: %s", offer.external_id, exc)
            return None

        motivo = resultado.recusados.get(offer.url)
        if motivo:
            with connect() as conn:
                mark_affiliate_blocked(conn, offer.product_id, motivo)
            return None

        link = resultado.links.get(offer.url)
        if link:
            with connect() as conn:
                save_affiliate_link(conn, offer.product_id, link)
        return link

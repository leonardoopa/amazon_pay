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
import re
from dataclasses import replace

import httpx

from ..models import Offer

PROVIDER = "ml_ofertas"
SITE = "https://www.mercadolivre.com.br"
OFERTAS_URL = f"{SITE}/ofertas"

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


def _frete_gratis(card: dict) -> bool:
    """A vitrine anuncia frete gratis no componente `shipping_v2`.

    Ele existe em todo card -- 144 de 144 medidos em 06/09/2026 -- e traz a
    marcacao numa `key` que CONTEM `free_shipping`, em tres variacoes:

        free_shipping_single      105 de 144   "Frete gratis"
        next_day_free_shipping      1
        same_day_free_shipping      1

    Contem, e nao comeca nem termina: a primeira variacao tem o sufixo
    `_single` depois, as outras duas tem o prefixo do prazo antes. Casar pela
    substring cobre as tres e sobrevive a uma quarta.

    O texto visivel NAO serve de chave: ele vem localizado ("Frete gratis") e
    mudaria com o idioma da resposta.

    Isto estava simplesmente faltando. O campo `free_shipping` existe no
    modelo desde sempre e o copywriter ja sabe dizer "Frete gratis", mas so a
    fonte de catalogo o preenchia. Como a vitrine e 89% do que sai no grupo,
    na pratica quase nenhum post podia mencionar o frete -- inclusive os 74,3%
    que tinham.
    """
    componente = _componente(card, "shipping_v2") or {}
    for bloco in componente.get("shipping_v2") or []:
        for valor in bloco.get("values") or []:
            if "free_shipping" in (valor.get("key") or ""):
                return True
    return False


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
        free_shipping=_frete_gratis(card),
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

    def fetch(
        self, pages: int = 1, categories: list[tuple[str, int]] | None = None
    ) -> list[Offer]:
        """Ofertas da vitrine. Cada pagina custa UMA requisicao e da ~45 itens.

        Compare com a coleta por catalogo, que gasta uma chamada por produto:
        aqui 45 produtos custam 1. E por isso que da pra rodar de minuto em
        minuto sem estourar nada.

        `categories` sao pares (id da categoria, paginas). A vitrine crua e uma
        fatia so do topo, e marca de roupa nao chega la: medido em 01/09/2026,
        a pagina sem filtro trouxe 1.621 precos e ZERO ocorrencia de Nike,
        adidas, Puma, Hering, Lupo ou New Balance, enquanto `?category=MLB23262`
        (Calcados) trouxe 422 precos e 36 de adidas. O produto estava na
        campanha o tempo todo -- so nao estava nas duas primeiras paginas.

        Esta e a unica via que resta para anuncio de vendedor. A busca por
        anuncio da API (`/sites/MLB/search`) responde 403 desde 2025, e a
        pagina de busca publica redireciona servidor para verificacao de conta.
        A vitrine por categoria e a mesma pagina que ja liamos, com um
        parametro.
        """
        offers: list[Offer] = []
        for pagina in range(1, max(1, pages) + 1):
            offers.extend(self._pagina({"page": pagina} if pagina > 1 else None, None))
            if not offers and pagina > 1:
                break  # pagina vazia: nao adianta pedir a proxima

        for categoria, paginas in categories or []:
            for pagina in range(1, max(1, paginas) + 1):
                params = {"category": categoria}
                if pagina > 1:
                    params["page"] = pagina
                da_pagina = self._pagina(params, categoria)
                offers.extend(da_pagina)
                if not da_pagina:
                    break
        return offers

    def _pagina(self, params: dict | None, categoria: str | None) -> list[Offer]:
        """Uma requisicao da vitrine, ja parseada."""
        response = self._client.get(OFERTAS_URL, params=params)
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

        achadas = [o for item in items if (o := _offer(item.get("card") or {}))]
        if categoria:
            # Carimba a pagina de origem. A vitrine nao devolve categoria em
            # campo nenhum, e sem isto nao ha como dizer que um produto veio da
            # pagina de Perfumes -- o titulo de um perfume arabe generico nao
            # cita marca nenhuma, e a prioridade so olha titulo.
            achadas = [replace(o, vitrine_categoria=categoria) for o in achadas]
        log.info(
            "ofertas: %d de %d cards em %s",
            len(achadas),
            len(items),
            f"{categoria} pagina {(params or {}).get('page', 1)}"
            if categoria
            else f"pagina {(params or {}).get('page', 1)}",
        )
        return achadas

    def landing_com_preco(self, caminho: str) -> list[Offer]:
        """Rota A: os cards da landing que ja vem com preco.

        `vitrine_categoria` recebe o caminho da pagina, e nao um ID de
        categoria: e a mesma ideia -- saber de onde o produto veio --, e e o que
        deixa a prioridade por origem valer para estas paginas tambem.
        """
        resposta = self._client.get(f"{SITE}{caminho}")
        resposta.raise_for_status()
        if "account-verification" in str(resposta.url):
            raise ParseFailed(
                f"{caminho} redirecionou para verificacao de conta -- esta "
                "pagina nao responde a requisicao de servidor."
            )

        achadas = [
            replace(oferta, vitrine_categoria=caminho)
            for card in _cards_com_preco(_payload(resposta.text))
            if (oferta := _offer(card))
        ]
        log.info("landing %s: %d oferta(s) com preco", caminho, len(achadas))
        return achadas

    def landing_catalogo(self, caminho: str) -> list[tuple[str, str]]:
        """Rota B: (id de catalogo, titulo) dos produtos linkados na landing.

        Sem preco -- a pagina nao traz. Quem precifica e a API de catalogo, que
        a rodada ja chama para os produtos que acompanha.
        """
        resposta = self._client.get(f"{SITE}{caminho}")
        resposta.raise_for_status()
        if "account-verification" in str(resposta.url):
            raise ParseFailed(
                f"{caminho} redirecionou para verificacao de conta -- esta "
                "pagina nao responde a requisicao de servidor."
            )

        vistos: dict[str, str] = {}
        for slug, mlb in CATALOGO.findall(resposta.text):
            vistos.setdefault(mlb, titulo_do_slug(slug))
        log.info("landing %s: %d produto(s) de catalogo", caminho, len(vistos))
        return list(vistos.items())

    # A vitrine nao tem "reconsulta por ID": ela e uma foto do momento. Produto
    # que saiu dela some sozinho, e o historico dele continua sendo mantido
    # pela coleta de catalogo se ele tambem estiver na watchlist.

    def affiliate_url(self, offer: Offer) -> str | None:
        """Mesmo Link Builder da fonte de catalogo, mesma logica.

        Compartilhada de proposito: quando esta funcao era uma copia, ela
        gravava `affiliate_blocked` mas nunca lia -- entao produto recusado
        pelo programa voltava ao painel em toda rodada, pra sempre.
        """
        from .affiliate import resolve_affiliate_link

        return resolve_affiliate_link(self._builder, offer)


# ---------- landing pages (/c/ e /e/) ----------
#
# Sao paginas diferentes da vitrine: curadoria ("Beleza Premium", "mais
# vendidos da categoria") em vez da campanha de ofertas do dia. O dono mandou
# dez URLs em 18/09/2026 e pediu que a coleta lesse elas.
#
# Oito nao dao: todo `lista.mercadolivre.com.br` responde 200 mas redireciona
# para `/gz/account-verification`, e passar disso seria burlar deteccao de
# robo. As que sobraram estao no `landing_pages` do watchlist.
#
# Duas formas, porque o ML monta as duas paginas diferente:
#
#   Rota A (`/c/...`)  o card ja vem com preco, no mesmo formato da vitrine --
#                      o parser e o mesmo, so muda onde o card mora no payload.
#   Rota B (`/e/...`)  a pagina traz so link de produto de catalogo, sem preco.
#                      O ID sai do permalink e o preco vem da API que ja
#                      usamos. O titulo sai do proprio slug: ele e descritivo
#                      ("idole-lancome-perfume-feminino-eau-de-parfum-100ml") e
#                      nao custa uma segunda chamada.

CATALOGO = re.compile(r"/([a-z0-9][a-z0-9-]{8,})/p/(MLB\d{6,})")


def _cards_com_preco(no, achados: list | None = None, nivel: int = 0) -> list:
    """Todo card do payload que tenha componente de preco.

    Busca em profundidade porque a landing muda o caminho de pagina para
    pagina: em `/c/esportes-e-fitness` os cards moram sob `components[].items`,
    em outra sob `containers[]`. Fixar o caminho daria coleta vazia e silenciosa
    a cada remodelagem do ML.
    """
    achados = achados if achados is not None else []
    if nivel > 14:
        return achados
    if isinstance(no, dict):
        componentes = no.get("components")
        if isinstance(componentes, list) and any(
            c.get("type") == "price" for c in componentes if isinstance(c, dict)
        ):
            achados.append(no)
        for valor in no.values():
            _cards_com_preco(valor, achados, nivel + 1)
    elif isinstance(no, list):
        for valor in no:
            _cards_com_preco(valor, achados, nivel + 1)
    return achados


def titulo_do_slug(slug: str) -> str:
    """"idole-lancome-perfume-feminino-100ml" -> "Idole Lancome Perfume ...".

    O titulo importa mais que estetica: e nele que tema, prioridade e exclusao
    casam. Sem titulo o produto nao entra em grupo nenhum.
    """
    return " ".join(p.capitalize() for p in slug.split("-") if p)

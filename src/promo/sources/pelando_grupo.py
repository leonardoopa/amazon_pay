"""Ofertas que o grupo "Achadinhos Pelando Vip" posta, com o nosso link no lugar.

Este grupo e diferente do xet e do Economizei. Eles postam o link direto da
loja, ou um encurtador de afiliado que leva ao ML; o Achadinhos Vip posta um
link do PROPRIO Pelando:

    Smart TV LG 50'' QNED Mini LED 4K Processador A7
    💰 R$2.372,00
    🚚 Mercado Livre
    ➡️ https://pelando.promo/Jq9FA

Dois saltos levam ao produto, e nenhum deles passa por link de afiliado de
ninguem:

    pelando.promo/Jq9FA
      -> 302 www.pelando.com.br/d/smart-tv-lg-50-qned-...-99af?aff_id=pwpp
      -> 200, e a pagina da oferta traz o `sourceUrl`: o endereco LIMPO da loja,
         sem tag, como quem postou a oferta no Pelando o colou.

Do `sourceUrl` sai so o ID do produto -- ASIN na Amazon, `MLB...` do catalogo no
ML. O `redirectUrl` da mesma pagina (`dpl.pelando.com.br/r/...`) e o redirecio-
nador de afiliado do Pelando e NUNCA e usado: o link que vai para o nosso grupo
e montado do zero, com a nossa tag, pelo mesmo caminho dos outros grupos-fonte.

O que se aproveita e fato -- qual produto, por quanto, sob qual condicao --, nao
o texto. A redacao do post e do nosso copywriter.

Como o Pelando e lido: a pagina da oferta e HTML publico e o robots.txt libera
`/d/` para bot generico (o `sources/pelando.py` ja le o mesmo site pelo mesmo
motivo). O User-Agent diz o que o bot e. Dois pedidos por oferta, com pausa, e
cada link resolvido uma vez por processo.

Tres formas de produto do ML aparecem, e cada uma segue um caminho:

- Catalogo (`/p/MLB...`): a API mede o preco e traz a foto. Entra como pista sem
  preco e sem endereco.
- Produto de usuario (`/up/MLBU...`): a API `/products/{id}/items` responde e o
  preco tambem e medido por nos -- medido em 30/09/2026, 3 de 3 --, mas ela nao
  traz foto nem aceita `/products/{id}`. A foto e o endereco com o nome do
  produto vem do Pelando.
- Anuncio avulso (`MLB-123...`): a API recusa e a pagina direta do ML devolve
  `account-verification` para servidor. O preco e a foto so existem no Pelando,
  e o preco da comunidade costuma valer so com cupom empilhado. Por isso so
  entra quando nao ha cupom nem mencao a cupom na descricao: o numero tem que
  ser verdadeiro sozinho, ou com a condicao que da para dizer (Pix).

Qualquer outra loja fica de fora: o repasse existe para ML e Amazon.

A foto do Pelando (maior variante de `imageSrcset`, WebP de 480 px) e a imagem
que quem postou subiu. A `openGraphImageUrl` NAO serve: e um cartao com a marca
do Pelando desenhada, e iria para o nosso post com a logo deles.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from .amazon_grupo import OfertaAmazon, _condicoes_da_mensagem
from .amazon_link import extrair_asin
from .grupo_wa import Pista, _texto_da_mensagem
from .pelando import USER_AGENT

log = logging.getLogger("promo")

# O encurtador que o grupo usa. O codigo e alfanumerico curto.
LINK = re.compile(r"https?://pelando\.promo/[A-Za-z0-9_-]+", re.I)

# Onde o encurtador tem que aterrissar. Qualquer outro destino e descartado:
# o que se le a seguir e HTML, e HTML de um site que nao e o Pelando nao e
# para ser lido por aqui.
PAGINA_DA_OFERTA = re.compile(r"^https://www\.pelando\.com\.br/d/[a-z0-9-]+", re.I)

# Os formatos de produto do ML, lidos do caminho da URL (nunca da query: o
# `?pdp_filters=item_id:MLB...` de um link de afiliado nao e o produto).
CATALOGO_ML = re.compile(r"/p/(MLB\d{6,})(?:[/?#]|$)", re.I)
USUARIO_ML = re.compile(r"/up/(MLBU\d{6,})(?:[/?#]|$)", re.I)
ANUNCIO_ML = re.compile(r"/MLB-?(\d{6,})(?:[-_/]|$)", re.I)

# `props` de cada ilha do Astro. A oferta mora na que tem `deal` com `sourceUrl`.
_PROPS = re.compile(r'<astro-island[^>]*?\sprops="([^"]*)"')

# Condicao do preco que o Pelando escreve na descricao e a Amazon nao mostra.
# "Valor com desconto no pix" e o caso real: R$ 118 so no Pix, e o numero
# sozinho seria o preco do boleto.
NO_PIX = re.compile(r"\bpix\b", re.I)
PROGRAME_E_POUPE = re.compile(r"programe\s*e\s*poupe", re.I)

# Cada link resolvido uma vez por processo. A leitura do grupo acontece a cada
# 15 minutos sobre as mesmas 50 mensagens; sem isto cada rodada repetiria os
# dois pedidos por oferta.
_RESOLVIDOS: dict[str, "DealPelando | None"] = {}


@dataclass(frozen=True)
class DealPelando:
    """O que a pagina da oferta no Pelando diz, so o que o repasse usa."""

    titulo: str
    preco: float
    loja: str  # nome da loja no Pelando: "Amazon", "Mercado Livre"...
    url_da_loja: str  # `sourceUrl`, sem tag. Dele sai o ID e so o ID.
    cupom: str = ""
    descricao: str = ""  # texto simples, sem HTML
    ativa: bool = True
    imagem: str = ""  # a maior foto que o autor subiu; "" quando nao ha


def links_do_pelando(texto: str) -> list[str]:
    """Os links `pelando.promo` de uma mensagem, sem repetir, em ordem."""
    achados: list[str] = []
    for bruto in LINK.findall(texto):
        # Pontuacao gruda no fim do link quando a frase termina nele.
        url = bruto.rstrip(".,);:!?\"'")
        if url not in achados:
            achados.append(url)
    return achados


def _desserializa(valor):
    """O formato de props do Astro: `[0, x]` e escalar, `[1, [...]]` e lista.

    Os outros tipos (data, mapa, conjunto...) nao aparecem na oferta; se
    aparecerem, o valor cru passa adiante em vez de quebrar a leitura.
    """
    if isinstance(valor, list) and len(valor) == 2 and isinstance(valor[0], int):
        tipo, conteudo = valor
        if tipo == 0:
            if isinstance(conteudo, (dict, list)):
                return _desserializa(conteudo)
            return conteudo
        if tipo == 1:
            return [_desserializa(item) for item in conteudo]
        return conteudo
    if isinstance(valor, dict):
        return {chave: _desserializa(item) for chave, item in valor.items()}
    return valor


def _sem_html(texto: str) -> str:
    limpo = re.sub(r"<[^>]+>", " ", texto or "")
    return re.sub(r"\s+", " ", _html.unescape(limpo)).strip()


def _maior_foto(deal: dict) -> str:
    """A maior variante da foto do autor, ou a miniatura quando so ha ela.

    A `openGraphImageUrl` fica de fora de proposito: tem a marca do Pelando.
    """
    variantes = [
        v for v in (deal.get("imageSrcset") or []) if isinstance(v, dict) and v.get("url")
    ]
    if variantes:
        maior = max(variantes, key=lambda v: v.get("width") or 0)
        return str(maior["url"]).strip()
    return str(deal.get("imageUrl") or "").strip()


def deal_da_pagina(pagina: str) -> DealPelando | None:
    """A oferta descrita na pagina `/d/...`, ou None quando nao da para ler.

    Os dados vem das `props` serializadas de uma ilha do Astro, e nao do HTML
    visivel: o preco, a loja e o endereco da loja estao la como campos, e o
    HTML so os mostra formatados.
    """
    for bruto in _PROPS.findall(pagina):
        props = _html.unescape(bruto)
        if "sourceUrl" not in props:
            continue
        try:
            deal = (_desserializa(json.loads(props)) or {}).get("deal")
        except (ValueError, AttributeError):
            continue
        if not isinstance(deal, dict) or not deal.get("sourceUrl"):
            continue

        try:
            preco = float(deal.get("price") or 0)
        except (TypeError, ValueError):
            preco = 0.0
        return DealPelando(
            imagem=_maior_foto(deal),
            titulo=(deal.get("title") or "").strip(),
            preco=preco,
            loja=((deal.get("store") or {}).get("name") or "").strip(),
            url_da_loja=str(deal["sourceUrl"]).strip(),
            cupom=(deal.get("couponCode") or "").strip(),
            descricao=_sem_html(deal.get("shortDescription") or ""),
            ativa=deal.get("status") == "active",
        )
    return None


def deal_do_link(url: str, cliente: httpx.Client) -> DealPelando | None:
    """A oferta por tras de um `pelando.promo/...`, ou None.

    Os dois saltos sao lidos sem seguir redirecionamento automatico: o primeiro
    e so o cabecalho `Location`, e o segundo so aceita aterrissar na pagina de
    oferta do proprio Pelando.
    """
    if url in _RESOLVIDOS:
        return _RESOLVIDOS[url]

    try:
        salto = cliente.get(url)
        destino = salto.headers.get("location", "")
        if not PAGINA_DA_OFERTA.match(destino):
            log.debug("Link %s nao aterrissou numa oferta: %s", url[-8:], destino[:60])
            _RESOLVIDOS[url] = None
            return None
        # Sem a query: `aff_id` e `utm_*` sao rastreio do Pelando, e a pagina e
        # a mesma sem eles.
        pagina = cliente.get(destino.split("?", 1)[0])
        pagina.raise_for_status()
    except httpx.HTTPError as exc:
        # Falha de rede nao entra no cache: na proxima rodada tenta de novo.
        log.debug("Link %s nao abriu: %s", url[-8:], exc)
        return None

    deal = deal_da_pagina(pagina.text)
    _RESOLVIDOS[url] = deal
    return deal


def _condicoes_do_deal(deal: DealPelando) -> tuple[str, ...]:
    """O que precisa sair colado no preco da Amazon para ele ser verdadeiro.

    O preco do Pelando e o que a comunidade escreveu, e muitas vezes vale so
    com cupom, no Pix ou no Programe e Poupe. A Amazon repassada e publicada
    com esse numero, entao a condicao viaja junto e o post e recusado sem ela
    -- mesma regra do `amazon_grupo`.
    """
    condicoes: list[str] = []
    if deal.cupom:
        condicoes.append(f"com o cupom {deal.cupom}")
    if NO_PIX.search(deal.descricao):
        condicoes.append("no Pix")
    condicoes += [
        c for c in _condicoes_da_mensagem(deal.descricao) if c not in condicoes
    ]
    return tuple(condicoes)


def _sem_rastreio(url: str) -> str:
    """O endereco sem query e sem fragmento.

    A query de um link do ML carrega o rastreio de quem o postou (`matt_tool`
    e a ferramenta de afiliado dele). Levar isso para a nossa fila seria
    publicar o que e dos outros -- e o Link Builder nao precisa de nada alem do
    caminho.
    """
    return url.split("#", 1)[0].split("?", 1)[0]


def _classifica(deal: DealPelando) -> tuple[str, str, str] | None:
    """(loja, id do produto, tipo) quando o repasse sabe tratar esta oferta.

    O ID sai do endereco da loja que o Pelando guarda, nunca do redirecionador
    deles. `tipo` e "asin", "catalogo", "usuario" ou "anuncio". Devolve None
    para o que fica de fora -- ver o topo do modulo.
    """
    url = _sem_rastreio(deal.url_da_loja)
    if "amazon.com" in url:
        asin = extrair_asin(url)
        return ("amazon", asin, "asin") if asin else None
    if "mercadolivre.com" in url:
        catalogo = CATALOGO_ML.search(url)
        if catalogo:
            return ("mercadolivre", catalogo.group(1).upper(), "catalogo")
        usuario = USUARIO_ML.search(url)
        if usuario:
            return ("mercadolivre", usuario.group(1).upper(), "usuario")
        anuncio = ANUNCIO_ML.search(url)
        if anuncio:
            return ("mercadolivre", f"MLB{anuncio.group(1)}", "anuncio")
    return None


def _preco_sem_condicao_dizivel(deal: DealPelando) -> bool:
    """O preco do Pelando depende de cupom que o post nao saberia afirmar?

    So importa onde o preco do post e o do Pelando (anuncio avulso do ML). O
    cupom da comunidade costuma ser empilhado -- "aplique X e o cupom da loja
    de 10%" --, e o preco so vale com todos eles.
    """
    return bool(deal.cupom) or "cupom" in deal.descricao.lower()


def _o_que_so_o_pelando_sabe(deal: DealPelando, tipo: str) -> dict:
    """Os campos da pista que o ML nao entrega e o Pelando entrega.

    Catalogo: nenhum -- a API mede o preco e traz a foto, e a pista sem preco e
    sem endereco e o que a manda por esse caminho.

    Produto de usuario: o endereco com o nome do produto e a foto, mas NAO o
    preco, que a API mede por nos.

    Anuncio avulso: tudo, preco inclusive, e a condicao que o torna verdadeiro.
    """
    if tipo == "catalogo":
        return {}
    campos: dict = {"url": _sem_rastreio(deal.url_da_loja), "imagem": deal.imagem}
    if tipo == "anuncio":
        campos["preco"] = deal.preco
        if NO_PIX.search(deal.descricao):
            campos["condicao"] = "no Pix"
    return campos


def ofertas_do_grupo(
    registros: list[dict],
    origem: str = "",
    limite_links: int = 15,
    pausa: float = 0.5,
    timeout: float = 20.0,
) -> tuple[list[Pista], list[OfertaAmazon]]:
    """O que o grupo postou: (produtos do ML, ofertas da Amazon).

    O ML sai como `Pista`, e o que ela carrega decide o caminho em
    `collect_de_outros_grupos`: sem preco ela vai para a API, que mede o preco
    por nos; com preco ela vira oferta pelo numero do Pelando. Ver
    `_o_que_so_o_pelando_sabe`.

    O cupom do ML vai na pista; o da Amazon vira condicao do preco, porque o
    cupom do ML nao existe la e o preco so vale com ele.

    Falha nao-fatal em toda ponta, igual as outras fontes do grupo.
    """
    pistas: list[Pista] = []
    amazon: list[OfertaAmazon] = []
    vistos: set[str] = set()
    sem_repasse = inativas = condicionais = 0
    tentados = 0

    with httpx.Client(
        timeout=timeout, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    ) as cliente:
        for registro in registros:
            for url in links_do_pelando(_texto_da_mensagem(registro)):
                if tentados >= limite_links:
                    break
                tentados += 1
                ja_resolvido = url in _RESOLVIDOS
                deal = deal_do_link(url, cliente)
                if not ja_resolvido:
                    time.sleep(pausa)
                if deal is None:
                    continue
                if not deal.ativa or deal.preco <= 0 or not deal.titulo:
                    inativas += 1
                    continue
                destino = _classifica(deal)
                if destino is None:
                    sem_repasse += 1
                    continue

                loja, produto, tipo = destino
                if produto in vistos:
                    continue
                if tipo == "anuncio" and _preco_sem_condicao_dizivel(deal):
                    condicionais += 1
                    continue
                vistos.add(produto)
                if loja == "amazon":
                    amazon.append(
                        OfertaAmazon(
                            asin=produto,
                            titulo=deal.titulo,
                            preco=deal.preco,
                            condicoes=_condicoes_do_deal(deal),
                        )
                    )
                    continue

                pistas.append(
                    Pista(
                        external_id=produto,
                        titulo=deal.titulo,
                        origem=origem,
                        cupons=(deal.cupom,) if deal.cupom else (),
                        **_o_que_so_o_pelando_sabe(deal, tipo),
                    )
                )

    log.info(
        "Grupo %s: %d link(s) do Pelando lidos, %d produto(s) do ML, %d da "
        "Amazon, %d de outra loja, %d inativo(s), %d com preco que depende de "
        "cupom.",
        origem or "pelando",
        tentados,
        len(pistas),
        len(amazon),
        sem_repasse,
        inativas,
        condicionais,
    )
    return pistas, amazon

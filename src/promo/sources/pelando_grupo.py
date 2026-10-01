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

O que fica de fora, de proposito:

- Oferta do ML que nao e produto de catalogo (`/up/MLBU...`, `MLB-123...`). O
  `/products/{id}/items` so responde por catalogo, e a pagina direta do ML
  devolve `account-verification` para requisicao de servidor -- medido em
  30/09/2026, nenhum dos tres enderecos testados abriu. Sem foto e sem preco do
  ML, a oferta so teria o numero que a comunidade do Pelando escreveu.
- Qualquer outra loja. O repasse existe para ML e Amazon.
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

# O catalogo do ML: o unico formato que a API de preco aceita.
CATALOGO_ML = re.compile(r"/p/(MLB\d{6,})", re.I)

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


def _classifica(deal: DealPelando) -> tuple[str, str] | None:
    """(loja, id do produto) quando o repasse sabe tratar esta oferta.

    O ID sai do endereco da loja que o Pelando guarda, nunca do redirecionador
    deles. Devolve None para o que fica de fora -- ver o topo do modulo.
    """
    url = deal.url_da_loja
    if "amazon.com" in url:
        asin = extrair_asin(url)
        return ("amazon", asin) if asin else None
    if "mercadolivre.com" in url:
        catalogo = CATALOGO_ML.search(url)
        return ("mercadolivre", catalogo.group(1).upper()) if catalogo else None
    return None


def ofertas_do_grupo(
    registros: list[dict],
    origem: str = "",
    limite_links: int = 15,
    pausa: float = 0.5,
    timeout: float = 20.0,
) -> tuple[list[Pista], list[OfertaAmazon]]:
    """O que o grupo postou: (produtos do ML, ofertas da Amazon).

    O ML sai como `Pista` SEM preco e SEM endereco, de proposito. Assim a oferta
    segue o caminho da API de catalogo em `collect_de_outros_grupos`, que e o
    que mede o preco por nos e busca a foto. Com preco na pista ela viraria
    oferta pelo numero do Pelando, e sem foto.

    O cupom do ML vai na pista; o da Amazon vira condicao do preco, porque o
    cupom do ML nao existe la e o preco so vale com ele.

    Falha nao-fatal em toda ponta, igual as outras fontes do grupo.
    """
    pistas: list[Pista] = []
    amazon: list[OfertaAmazon] = []
    vistos: set[str] = set()
    sem_repasse = inativas = 0
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

                loja, produto = destino
                if produto in vistos:
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
                else:
                    pistas.append(
                        Pista(
                            external_id=produto,
                            titulo=deal.titulo,
                            origem=origem,
                            cupons=(deal.cupom,) if deal.cupom else (),
                        )
                    )

    log.info(
        "Grupo %s: %d link(s) do Pelando lidos, %d produto(s) do ML, %d da "
        "Amazon, %d fora do repasse (outra loja ou ML fora do catalogo), %d "
        "inativo(s).",
        origem or "pelando",
        tentados,
        len(pistas),
        len(amazon),
        sem_repasse,
        inativas,
    )
    return pistas, amazon

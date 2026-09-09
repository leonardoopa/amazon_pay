"""Descobre produtos pelo que outro grupo de WhatsApp esta postando.

O que este modulo faz e o que NAO faz importa mais aqui do que em qualquer
outra fonte.

FAZ: le as mensagens de um grupo em que o numero ja esta, extrai os links do
Mercado Livre e resolve cada um ate o ID do anuncio. Esses IDs entram na
carteira como qualquer outro achado, e dali em diante passam pelo funil
inteiro -- medicao de preco contra a nossa mediana, piso de desconto, cupom,
cooldown e texto proprio.

NAO FAZ: copiar o texto deles, nem reaproveitar o link deles. O fato "o
produto X esta a R$ Y" e livre; a headline que alguem escreveu, nao. E o link
carrega a tag de afiliado de quem achou a oferta -- republicar com ela seria
trabalhar de graca, e trocar a tag no link deles seria ficar com a comissao do
trabalho alheio. O que se aproveita e a PISTA, e a pista e um fato.

Na pratica isso tambem produz post melhor: o deles vem sem a nossa medicao de
baseline, sem os nossos pisos de desconto e sem o cooldown que impede
repeticao. Copiado, o post herdaria os problemas dele junto com o conteudo.

O link deles e o mesmo formato que o nosso Link Builder gera: `meli.la/XXXX`
redireciona para `/social/<nickname>`, e o ID do anuncio NAO aparece na URL
final -- ele mora no corpo, em `item_id`. Medido em 09/09/2026: 44 links em 50
mensagens, e o `item_id` apareceu em todos os que resolveram.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

# Sem User-Agent de navegador a pagina social devolve outra coisa, igual ao
# painel de afiliados.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# Links do ML em qualquer forma que apareca numa mensagem.
LINK = re.compile(
    r"https?://(?:meli\.la|(?:\w+\.)?mercadolivre\.com\.br)/\S+", re.I
)

# O ID do anuncio no corpo da pagina social. `item_id` e o campo do produto em
# destaque; os outros MLB que aparecem no HTML sao da vitrine do perfil e
# levariam ao produto errado -- medido, 191 ocorrencias de MLB numa pagina
# cujo produto era um so.
ITEM_ID = re.compile(r'"item_id"\s*:\s*"(MLB\d{6,})"')
TITULO = re.compile(r'<meta property="og:title" content="([^"]*)"')

# A foto do produto, da mesma pagina que ja esta baixada.
#
# Sem ela o post da pista saia so com texto: `fetch_by_ids` recebe a imagem de
# quem chama -- na reconsulta normal ela vem do banco -- e a pista nao tinha o
# que passar. Medido em 09/09/2026, 57 dos 90 posts vindos daqui foram sem
# foto, contra 0 de 210 posts normais.
#
# `/products/{id}/items` devolve as fotos do anuncio, mas seria uma segunda
# chamada por produto para pegar o que esta nesta pagina de graca.
IMAGEM = re.compile(r'<meta property="og:image" content="([^"]*)"')

log = logging.getLogger("promo")


@dataclass(frozen=True)
class Pista:
    """Um produto que outro grupo postou. So o ID e o titulo -- nada do texto."""

    external_id: str
    titulo: str
    origem: str  # nome do grupo, para o log
    imagem: str = ""  # og:image da pagina do produto, "" quando nao veio


def _texto_da_mensagem(registro: dict) -> str:
    """O texto de uma mensagem, venha ele de onde vier.

    Post de oferta chega como legenda de imagem na maioria das vezes, e como
    `conversation` ou `extendedTextMessage` quando nao tem foto. Ler so um dos
    tres perderia a maior parte.
    """
    msg = registro.get("message") or {}
    return (
        msg.get("conversation")
        or (msg.get("extendedTextMessage") or {}).get("text")
        or (msg.get("imageMessage") or {}).get("caption")
        or (msg.get("videoMessage") or {}).get("caption")
        or ""
    )


def links_das_mensagens(registros: list[dict]) -> list[str]:
    """Todo link do ML citado, sem repetir, na ordem em que apareceram."""
    vistos: list[str] = []
    for registro in registros:
        for bruto in LINK.findall(_texto_da_mensagem(registro)):
            # Pontuacao gruda no fim do link quando a frase termina nele.
            url = bruto.rstrip(".,);:!?\"'")
            if url not in vistos:
                vistos.append(url)
    return vistos


def ler_mensagens(
    base_url: str, instancia: str, chave: str, jid: str, limite: int = 50,
    timeout: float = 30.0,
) -> list[dict]:
    """As mensagens mais recentes do grupo, pela Evolution."""
    with httpx.Client(timeout=timeout) as cliente:
        resposta = cliente.post(
            f"{base_url.rstrip('/')}/chat/findMessages/{instancia}",
            headers={"apikey": chave, "Content-Type": "application/json"},
            content=json.dumps(
                {"where": {"key": {"remoteJid": jid}}, "limit": limite}
            ),
        )
        resposta.raise_for_status()
        corpo = resposta.json()

    if isinstance(corpo, dict):
        return (corpo.get("messages") or {}).get("records") or []
    return corpo if isinstance(corpo, list) else []


def produto_do_link(url: str, timeout: float = 25.0) -> tuple[str, str, str] | None:
    """(id do anuncio, titulo, imagem) do produto por tras do link, ou None.

    O link de afiliado nao carrega o ID na URL: ele redireciona para
    `/social/<nickname>` e o ID mora no corpo. Por isso a pagina inteira e
    baixada -- nao ha atalho.
    """
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as cliente:
            resposta = cliente.get(url)
            resposta.raise_for_status()
            html = resposta.text
    except httpx.HTTPError as exc:
        log.debug("Link %s nao abriu: %s", url[-24:], exc)
        return None

    # Link direto de produto ja traz o ID na propria URL.
    direto = re.search(r"/(MLB-?\d{6,})", str(resposta.url))
    if direto:
        item = direto.group(1).replace("-", "")
    else:
        achado = ITEM_ID.search(html)
        if not achado:
            return None
        item = achado.group(1)

    titulo = TITULO.search(html)
    imagem = IMAGEM.search(html)
    return (
        item,
        (titulo.group(1).strip() if titulo else ""),
        (imagem.group(1).strip() if imagem else ""),
    )


def pistas(
    base_url: str,
    instancia: str,
    chave: str,
    jid: str,
    nome: str = "",
    limite_mensagens: int = 50,
    limite_links: int = 25,
    pausa: float = 0.8,
) -> list[Pista]:
    """Produtos que o grupo postou recentemente.

    `limite_links` existe porque cada link e uma pagina de 370 KB baixada do
    ML. Medido em 09/09/2026, 50 mensagens do grupo "xet das promocoes | 39"
    tinham 44 links distintos -- baixar todos, a cada rodada, seria trafego
    demais para o ganho.

    Falha nao-fatal em toda ponta: sem esta fonte o bot descobre pelo que
    sempre descobriu.
    """
    try:
        registros = ler_mensagens(base_url, instancia, chave, jid, limite_mensagens)
    except Exception as exc:  # noqa: BLE001 - fonte extra nunca derruba a rodada
        log.warning("Nao consegui ler o grupo %s: %s", nome or jid, exc)
        return []

    urls = links_das_mensagens(registros)
    log.info(
        "Grupo %s: %d mensagens, %d link(s) do ML; resolvendo os %d mais recentes.",
        nome or jid,
        len(registros),
        len(urls),
        min(limite_links, len(urls)),
    )

    achadas: list[Pista] = []
    vistos: set[str] = set()
    for url in urls[:limite_links]:
        resolvido = produto_do_link(url)
        time.sleep(pausa)
        if not resolvido:
            continue
        item, titulo, imagem = resolvido
        if item in vistos:
            continue
        vistos.add(item)
        achadas.append(
            Pista(external_id=item, titulo=titulo, origem=nome or jid, imagem=imagem)
        )

    log.info("Grupo %s: %d produto(s) identificado(s).", nome or jid, len(achadas))
    return achadas

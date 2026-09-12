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

import html as _html
import json
import logging
import re
import time
from dataclasses import dataclass, replace

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

# O cartao do produto em destaque, dentro da pagina social.
#
# Esta pagina nao mostra um produto: mostra um, grande, e uma vitrine de
# recomendados embaixo. Todo preco que se leia solto no HTML pode ser de
# qualquer um dos recomendados -- medido em 12/09/2026, uma pagina tinha
# quatro `"price"` no corpo e so o primeiro era do produto certo, noutra o
# primeiro era de um recomendado.
#
# O que separa um do outro e o titulo: o `og:title` E o produto em destaque,
# entao o cartao dele e o primeiro pedaco do corpo onde esse titulo aparece.
# Dali para a frente, os primeiros "Antes"/"Agora" e o primeiro link canonico
# sao dele.
#
# Os precos vem do rotulo de acessibilidade porque e o unico lugar onde reais
# e centavos estao no mesmo atributo. Na marcacao visivel eles sao dois
# `<span>` separados por um terceiro com a virgula, e juntar isso a mao daria
# o mesmo resultado com mais chance de erro.
CANONICO = re.compile(r'href="(https://(?:www|produto)\.mercadolivre\.com\.br/[^"?#]+)')
DINHEIRO = re.compile(
    r'aria-label="(?:(Antes|Agora): )?(\d+) reais(?: com (\d+) centavos)?"'
)

# O cupom citado na mensagem. Ver `cupons_da_mensagem` para o porque de exigir
# o rotulo e o delimitador.
ROTULO_DE_CUPOM = re.compile(r"cupom|cupon|c[oó]digo", re.I)
CODIGO_DE_CUPOM = re.compile(r"[`*\"'“”]([A-Z][A-Z0-9]{4,24})[`*\"'“”]")

# Palavra em caixa alta que aparece entre delimitadores e nao e codigo. Lista
# curta de proposito: so o que ja apareceu de verdade, e so por igualdade
# exata -- "LEVEAGORA" e cupom, "AGORA" nao.
_NAO_SAO_CUPOM = frozenset(
    {"AGORA", "APENAS", "AQUI", "CLIQUE", "COMPRE", "FRETE", "GRATIS",
     "HOJE", "LINK", "OFERTA", "PROMO", "ULTIMAS"}
)

# Quanto do corpo, depois do titulo, ainda e o cartao do produto. Um cartao
# inteiro (foto, titulo, preco de antes, preco de agora, parcelamento) mede
# cerca de 2.500 bytes; 4.000 cobre com folga sem alcancar o cartao seguinte.
CARTAO_BYTES = 4000

log = logging.getLogger("promo")


@dataclass(frozen=True)
class Pista:
    """Um produto que outro grupo postou.

    Fato, nao texto: o que se guarda daqui e o produto, o preco que o anuncio
    mostra e o endereco dele no ML. A headline que o outro grupo escreveu e o
    link de afiliado deles ficam de fora -- o post sai com texto nosso e link
    nosso.
    """

    external_id: str
    titulo: str
    origem: str  # nome do grupo, para o log
    imagem: str = ""  # og:image da pagina do produto, "" quando nao veio
    # Endereco canonico do anuncio no ML (`/p/`, `/up/` ou `produto.`). E o
    # que o Link Builder precisa para gerar o NOSSO link: o endereco final da
    # pagina social e o perfil de afiliado deles, e nao serve.
    url: str = ""
    preco: float = 0.0  # "Agora" do cartao; 0.0 quando a pagina nao trouxe
    preco_antes: float = 0.0  # "Antes" riscado; 0.0 quando nao ha desconto
    # Codigos de cupom citados na MESMA mensagem que trouxe o link. O codigo e
    # da campanha do ML, nao deles: funciona no carrinho de qualquer um.
    cupons: tuple[str, ...] = ()


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


def cupons_da_mensagem(texto: str) -> tuple[str, ...]:
    """Os codigos de cupom citados na mensagem, na ordem em que aparecem.

    O codigo e um fato sobre a campanha do ML, igual ao preco: qualquer um que
    abra o site do ML acha o mesmo cupom, e ele funciona no carrinho de quem
    quer que seja. O que nao se aproveita e o texto em volta.

    Os dois grupos delimitam o codigo, e e isso que torna a leitura segura --
    sem delimitador, "PIX" e "OFF" virariam cupom. O xet usa crase
    ("Use o cupom: `EXCLUSIVONOMELI`") e o Economizei usa asterisco
    ("Cupom: *EXCLUSIVONOMELI*"). Medido em 12/09/2026: 36 das 50 mensagens do
    xet e 13 das 15 do Economizei citavam cupom, sempre assim.

    Exige a palavra "cupom" na mensagem: sem ela, qualquer palavra em caixa
    alta entre asteriscos -- que e como os dois grupos dao enfase -- viraria
    codigo.
    """
    if not ROTULO_DE_CUPOM.search(texto):
        return ()
    achados: list[str] = []
    for codigo in CODIGO_DE_CUPOM.findall(texto):
        if codigo in _NAO_SAO_CUPOM or codigo in achados:
            continue
        achados.append(codigo)
    return tuple(achados)


def links_e_cupons(registros: list[dict]) -> list[tuple[str, tuple[str, ...]]]:
    """(link do ML, cupons da MESMA mensagem), sem repetir link.

    O par importa: o cupom desses grupos e quase sempre de marca ou de loja,
    e o que diz a quem ele serve e o produto que veio junto dele. Um cupom
    lido de uma mensagem e colado noutro produto seria codigo que o checkout
    recusa.

    Mensagem de outra loja nao contamina: o `LINK` so casa dominio do ML,
    entao o post de Netshoes com `XETPROMOCOES` nao produz par nenhum.
    """
    vistos: list[tuple[str, tuple[str, ...]]] = []
    ja: set[str] = set()
    for registro in registros:
        texto = _texto_da_mensagem(registro)
        cupons = cupons_da_mensagem(texto)
        for bruto in LINK.findall(texto):
            # Pontuacao gruda no fim do link quando a frase termina nele.
            url = bruto.rstrip(".,);:!?\"'")
            if url in ja:
                continue
            ja.add(url)
            vistos.append((url, cupons))
    return vistos


def links_das_mensagens(registros: list[dict]) -> list[str]:
    """Todo link do ML citado, sem repetir, na ordem em que apareceram."""
    return [url for url, _cupons in links_e_cupons(registros)]


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


def _cartao_em_destaque(html: str, titulo: str) -> str:
    """O pedaco do HTML que descreve o produto do `og:title`.

    Vazio quando o titulo nao aparece no corpo -- acontece quando o link nao
    era de produto (pagina de lista, perfil sem destaque). Sem cartao nao ha
    preco, e a pista segue so com o ID.
    """
    if not titulo:
        return ""
    corpo = html[html.find("</head>") :] if "</head>" in html else html
    # O titulo aparece no `alt` da foto, escapado; a busca tenta as duas
    # formas porque o escape so muda algo em titulo com `&`, `<` ou aspas.
    for agulha in (_html.escape(titulo)[:40], titulo[:40], titulo[:24]):
        if not agulha:
            continue
        posicao = corpo.find(agulha)
        if posicao >= 0:
            return corpo[posicao : posicao + CARTAO_BYTES]
    return ""


def _precos_do_cartao(cartao: str) -> tuple[float, float]:
    """(preco de agora, preco riscado) do cartao. Zero onde nao houver.

    Anuncio sem desconto traz um valor so, e as vezes sem o rotulo "Agora" --
    por isso o rotulo vazio tambem conta como preco atual.
    """
    agora = antes = 0.0
    for achado in DINHEIRO.finditer(cartao):
        rotulo, reais, centavos = achado.group(1), achado.group(2), achado.group(3)
        valor = int(reais) + int(centavos or 0) / 100
        if rotulo == "Antes":
            antes = antes or valor
        else:  # "Agora", ou sem rotulo
            agora = agora or valor
    # Riscado menor que o preco atual e ruido de algum cartao vizinho, nao
    # desconto. Melhor perder o "de" do que anunciar aumento como promocao.
    if antes <= agora:
        antes = 0.0
    return agora, antes


def produto_do_link(url: str, timeout: float = 25.0) -> Pista | None:
    """A pista por tras de um link do outro grupo, ou None.

    O link de afiliado nao carrega o ID na URL: ele redireciona para
    `/social/<nickname>` e o ID mora no corpo. Por isso a pagina inteira e
    baixada -- nao ha atalho. Ja que ela vem inteira, sai dali tudo o que o
    post precisa: titulo, foto, preco, riscado e o endereco canonico.

    Antes daqui so saiam ID, titulo e foto, e o preco vinha da API de
    catalogo. Isso custava a maior parte da fonte: a rota `/products/{id}`
    so responde por produto de catalogo, e a maioria do que o outro grupo
    posta e anuncio de vendedor -- medido em 09/09/2026, 13 de 43. Com o
    preco lido da propria pagina, 24 dos 25 links da medicao de 12/09/2026
    viraram oferta completa.
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

    achado_titulo = TITULO.search(html)
    titulo = _html.unescape(achado_titulo.group(1).strip()) if achado_titulo else ""
    imagem = IMAGEM.search(html)

    cartao = _cartao_em_destaque(html, titulo)
    preco, antes = _precos_do_cartao(cartao)
    canonico = CANONICO.search(cartao) if cartao else None

    return Pista(
        external_id=item,
        titulo=titulo,
        origem="",  # quem chama sabe o nome do grupo; aqui ele nao existe
        imagem=(imagem.group(1).strip() if imagem else ""),
        url=canonico.group(1) if canonico else "",
        preco=preco,
        preco_antes=antes,
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

    pares = links_e_cupons(registros)
    log.info(
        "Grupo %s: %d mensagens, %d link(s) do ML; resolvendo os %d mais recentes.",
        nome or jid,
        len(registros),
        len(pares),
        min(limite_links, len(pares)),
    )

    achadas: list[Pista] = []
    vistos: set[str] = set()
    for url, cupons in pares[:limite_links]:
        pista = produto_do_link(url)
        time.sleep(pausa)
        if pista is None or pista.external_id in vistos:
            continue
        vistos.add(pista.external_id)
        achadas.append(replace(pista, origem=nome or jid, cupons=cupons))

    com_preco = sum(1 for p in achadas if p.preco > 0)
    com_cupom = sum(1 for p in achadas if p.cupons)
    log.info(
        "Grupo %s: %d produto(s) identificado(s), %d com preco na propria "
        "pagina, %d com cupom citado na mensagem.",
        nome or jid,
        len(achadas),
        com_preco,
        com_cupom,
    )
    return achadas

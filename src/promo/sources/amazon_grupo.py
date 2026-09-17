"""Oferta da Amazon que outro grupo postou, com o nosso link no lugar do deles.

O que os grupos-fonte publicam da Amazon nao e URL de produto: e o encurtador
do SiteStripe, `link.amazon/XXXX`, que carrega a tag de afiliado DELES. Medido
em 16/09/2026, 8 links da Amazon em 100 mensagens dos dois grupos, todos nesse
formato e nenhum com ASIN visivel.

Dois redirecionamentos resolvem:

    link.amazon/B01wQSA6J
      -> amzlinks.in/B01wQSA6J
      -> amazon.com.br/dp/B0FMLBQ136?...&tag=pietroxet-20

Do fim dessa cadeia sai o ASIN, e so o ASIN. A tag deles fica para tras: o link
que vai para o nosso grupo e montado do zero pelo `link_de_afiliado`, que e a
URL canonica mais a NOSSA tag. Reaproveitar link de afiliado alheio nao esta em
questao -- o produto e publico, o link nao.

Seguir redirecionamento nao e raspagem: le-se o cabecalho `Location`, nunca o
corpo da pagina. O preco NAO sai daqui, e nao pode sair -- o Operating Agreement
do Associates exige que preco exibido venha da API e proibe ferramenta de
extracao. O preco desta fonte vem do texto que o outro grupo publicou, que e
fato divulgado por eles, e o post sai marcado `verified=False`.

Duas armadilhas de preco medidas nas mensagens reais. Nenhuma das duas descarta
a oferta -- o pedido do dono e que o post saia igual ao deles -- mas as duas
viram CONDICAO, e a condicao e obrigatoria na linha do preco:

- "De R$ 129 por R$ 45 em 2x" -- R$ 45 e a PARCELA. Sozinho o numero e falso;
  com "em 2x" colado nele e o mesmo que o outro grupo publicou.
- "Selecione: Programe e Poupe" -- o preco so vale assinando a recorrencia.

Sem a condicao na linha do preco o post mente, e mente com o nosso link do
lado. Por isso ela nao e enfeite opcional: `condicoes` viaja ate o copywriter e
o post e recusado se ela nao aparecer, do mesmo jeito que o preco com cupom.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from .amazon_link import (
    PRODUTO as PRODUTO_CANONICO,
    extrair_asin,
    imagem_do_asin,
    link_de_afiliado,
)

log = logging.getLogger("promo")

# Encurtadores que os grupos usam. `link.amazon` e o do SiteStripe; `amzn.to`
# e o classico; `amzlinks.in` aparece no meio da cadeia do primeiro.
ENCURTADOR = re.compile(r"https?://(?:link\.amazon|amzn\.to|amzlinks\.in|a\.co)/\S+", re.I)
QUALQUER_AMAZON = re.compile(r"https?://\S*(?:amazon\.com|amzn\.to|amzlinks\.in|a\.co)/\S*", re.I)

# A propria Amazon, e nao um encurtador. Pedir uma pagina daqui esta fora de
# questao: o ASIN mora na URL, e o resto da pagina nao e nosso para ler.
DOMINIO_AMAZON = re.compile(r"https?://[^/]*\bamazon\.com", re.I)

# "R$ 1.999,99", "R$ 31", "R$ 79,99". O milhar e opcional e o centavo tambem:
# o xet escreve inteiro ("De R$ 31 por R$ 20") e o Economizei com centavos.
DINHEIRO = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)")

# "por R$ 45 em 2x", "R$ 45 em 3x sem juros", "2x de R$ 45". O numero de
# parcelas e capturado porque ele e o que torna o preco verdadeiro.
PARCELADO = re.compile(r"\b(\d{1,2})\s*x\b", re.I)
SEM_JUROS = re.compile(r"\bsem juros\b", re.I)

# A condicao que faz o preco valer. Sem ela no post, o preco esta errado para
# quem compra avulso.
PROGRAME_E_POUPE = re.compile(r"programe\s*e\s*poupe", re.I)

# Linha que e enfeite do post deles, nunca o nome do produto.
_NAO_E_TITULO = re.compile(
    r"^\s*(?:🛒|🎟|⚠|🫵|👉|https?://|`|de\s+r\$|por\s+r\$|r\$|cupom|selecione|"
    r"compre\s+aqui|link|vendido\s+por)",
    re.I,
)


@dataclass(frozen=True)
class OfertaAmazon:
    asin: str
    titulo: str
    preco: float
    preco_antes: float = 0.0
    # O que precisa estar na linha do preco para ele ser verdadeiro. Vazio
    # significa preco a vista, sem condicao nenhuma.
    condicoes: tuple[str, ...] = ()

    @property
    def condicao(self) -> str:
        """As condicoes como o post as escreve: "em 2x sem juros e Programe e Poupe"."""
        if not self.condicoes:
            return ""
        if len(self.condicoes) == 1:
            return self.condicoes[0]
        return " e ".join((", ".join(self.condicoes[:-1]), self.condicoes[-1]))


def _para_float(bruto: str) -> float:
    return float(bruto.replace(".", "").replace(",", "."))


def asin_do_link(url: str, timeout: float = 15.0) -> str | None:
    """O ASIN por tras de um link da Amazon, seguindo os redirecionamentos.

    Le so o `Location` de cada salto. Para no primeiro que ja tenha ASIN, e
    desiste depois de quatro -- cadeia mais longa que isso e sinal de que o
    link nao leva a produto nenhum (uma pagina de campanha, por exemplo).

    Devolve None quando nao da para ter certeza. Chutar sairia caro: ASIN
    errado vira post apontando para outro produto, com o preco deste.
    """
    direto = extrair_asin(url)
    if direto:
        return direto

    atual = url
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as cliente:
            for _ in range(4):
                if DOMINIO_AMAZON.search(atual):
                    # Chegou na Amazon sem ASIN na URL: e pagina de campanha,
                    # busca ou carrinho, e nao produto. Para aqui em vez de
                    # pedir a pagina -- o Operating Agreement proibe ferramenta
                    # de extracao, e o que nos interessa (o ASIN) so existe na
                    # URL. Medido em 16/09/2026: a Amazon devolve 503 para esse
                    # pedido de qualquer jeito.
                    return None
                resposta = cliente.get(atual)
                destino = resposta.headers.get("location")
                if not destino:
                    return None
                achado = extrair_asin(destino)
                if achado:
                    return achado
                atual = destino
    except Exception as exc:  # noqa: BLE001 - link ruim nao derruba a rodada
        log.info("Nao consegui resolver o link da Amazon %s: %s", url[:60], exc)
        return None
    return None


def _titulo_da_mensagem(texto: str) -> str:
    """O nome do produto: a primeira linha que nao e chamada, preco nem enfeite.

    Os dois grupos escrevem diferente. O xet abre com uma chamada em CAIXA ALTA
    e poe o produto na linha seguinte; o Economizei comeca direto no produto.
    Pular a linha que esta toda em maiuscula resolve os dois -- nome de produto
    de marketplace vem em Caixa De Titulo, nunca gritando.
    """
    for linha in texto.splitlines():
        limpa = linha.strip().strip("*~`_ ")
        if len(limpa) < 8 or _NAO_E_TITULO.match(limpa):
            continue
        letras = [c for c in limpa if c.isalpha()]
        if letras and all(c.isupper() for c in letras):
            continue  # chamada do post deles, nao o produto
        return limpa
    return ""


def _precos_da_mensagem(texto: str) -> tuple[float, float]:
    """(preco, riscado) do texto deles. (0, 0) quando nao da para confiar.

    Dois valores na mensagem significam "de X por Y" -- o maior e o riscado.
    Um valor sozinho e o preco atual, e o post sai sem "de". Tres ou mais nao
    da para desempatar sem adivinhar, entao a oferta cai fora.
    """
    achados = [_para_float(bruto) for bruto in DINHEIRO.findall(texto)]
    if not achados or len(achados) > 2:
        return 0.0, 0.0
    if len(achados) == 1:
        return achados[0], 0.0
    antes, agora = max(achados), min(achados)
    return agora, antes


def _condicoes_da_mensagem(texto: str) -> tuple[str, ...]:
    """O que precisa sair colado no preco para ele ser verdadeiro.

    "De R$ 129 por R$ 45 em 2x" e o caso real: R$ 45 tres vezes, nao R$ 45. O
    numero sozinho e falso; com "em 2x" do lado e exatamente o que o outro
    grupo publicou. Mesma coisa com o "Programe e Poupe", que e assinatura.

    Nao multiplicamos para achar o total: sem a API isso seria inventar numero,
    e "2x de R$ 45" ja diz tudo o que quem compra precisa saber.
    """
    achadas: list[str] = []
    parcelas = PARCELADO.search(texto)
    if parcelas:
        rotulo = f"em {parcelas.group(1)}x"
        if SEM_JUROS.search(texto):
            rotulo += " sem juros"
        achadas.append(rotulo)
    if PROGRAME_E_POUPE.search(texto):
        achadas.append("Programe e Poupe")
    return tuple(achadas)


def oferta_da_mensagem(texto: str, url: str) -> OfertaAmazon | None:
    """A oferta da Amazon que a mensagem deles descreve, ou None.

    O que sai daqui e FATO: qual produto, por quanto, sob qual condicao. O
    texto deles nao e copiado -- quem escreve o nosso post e o copywriter, a
    partir destes campos.
    """
    asin = asin_do_link(url)
    if not asin:
        return None

    preco, antes = _precos_da_mensagem(texto)
    if preco <= 0:
        return None
    if antes and antes <= preco:
        antes = 0.0

    titulo = _titulo_da_mensagem(texto)
    if not titulo:
        return None

    return OfertaAmazon(
        asin=asin,
        titulo=titulo,
        preco=preco,
        preco_antes=antes,
        condicoes=_condicoes_da_mensagem(texto),
    )


def nosso_link(asin: str, tag: str) -> str:
    """O link que vai para o nosso grupo. Montado do zero, com a NOSSA tag."""
    return link_de_afiliado(asin, tag)


def imagem(asin: str) -> str:
    """Imagem publica por ASIN -- permitida sem API, ao contrario do preco."""
    return imagem_do_asin(asin)


def ofertas_do_grupo(
    registros: list[dict],
    origem: str = "",
    limite_links: int = 10,
    pausa: float = 0.5,
) -> list[OfertaAmazon]:
    """As ofertas da Amazon que este grupo postou, na ordem em que apareceram.

    `limite_links` e baixo de proposito. Cada link custa dois pedidos a
    encurtador de terceiro, e a Amazon e minoria no que eles postam -- medido
    em 16/09/2026, 8 links da Amazon em 100 mensagens dos dois grupos. Nao ha
    o que ganhar abrindo a torneira.

    Falha nao-fatal em toda ponta, igual a fonte do ML: link que nao resolve
    sai da lista e a rodada segue.
    """
    from .grupo_wa import _texto_da_mensagem

    achadas: list[OfertaAmazon] = []
    vistos: set[str] = set()
    tentados = 0
    for registro in registros:
        texto = _texto_da_mensagem(registro)
        encontrados = ENCURTADOR.findall(texto) or [
            u for u in QUALQUER_AMAZON.findall(texto) if extrair_asin(u)
        ]
        for bruto in encontrados:
            if tentados >= limite_links:
                break
            url = bruto.rstrip(".,);:!?\"'")
            tentados += 1
            oferta = oferta_da_mensagem(texto, url)
            time.sleep(pausa)
            if oferta is None or oferta.asin in vistos:
                continue
            vistos.add(oferta.asin)
            achadas.append(oferta)

    if achadas:
        com_condicao = sum(1 for o in achadas if o.condicoes)
        log.info(
            "Grupo %s: %d oferta(s) da Amazon, %d com condicao no preco "
            "(parcelado ou Programe e Poupe).",
            origem or "fonte",
            len(achadas),
            com_condicao,
        )
    return achadas


class AmazonDoGrupo:
    """Fonte da Amazon sem API: so sabe montar o link de afiliado.

    Existe porque o `sources/amazon.py` de verdade exige a Creators API, que a
    conta ainda nao liberou, e sem nenhuma fonte chamada "amazon" o pipeline
    levanta KeyError ao pedir o link de uma oferta repassada.

    `search` devolve vazio de proposito: esta fonte nao descobre nada sozinha.
    O que ela recebe vem inteiro dos grupos-fonte, por `ofertas_do_grupo`.
    """

    name = "amazon"

    def __init__(self, tag: str) -> None:
        self._tag = tag

    def search(self, keyword: str, limit: int = 50) -> list:
        return []

    def affiliate_url(self, offer) -> str:
        if not self._tag:
            return ""
        return link_de_afiliado(offer.external_id, self._tag)

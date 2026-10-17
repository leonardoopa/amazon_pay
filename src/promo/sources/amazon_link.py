"""Link de afiliado da Amazon sem API e sem raspagem.

A Creators API (ex-PA-API) exige vendas qualificadas na conta Associates para
liberar, e a conta ainda nao tem. Enquanto isso, duas coisas continuam
possiveis e permitidas:

- montar o link de afiliado, que e so a URL canonica do produto com a tag de
  associado em `?tag=`. Nao precisa de API nem de sessao logada. O encurtador
  `link.amazon/...` do SiteStripe faz o mesmo, mas exige navegar logado e nao
  sai em lote;
- usar a imagem publica do produto, servida por ASIN em
  `m.media-amazon.com/images/P/<ASIN>.jpg`.

O que NAO esta aqui, de proposito, e raspagem de preco. O Operating Agreement
do Associates proibe "any use of data mining, robots, or similar data gathering
and extraction tools", e exige que preco exibido venha da API. Raspar para
montar post de oferta bate nas duas clausulas, e o risco cai justamente sobre a
conta que precisa sobreviver ate liberar a API.

Por isso o preco entra a mao, pelo comando `promo amazon-add`: quem escolhe a
oferta e le o preco e uma pessoa, o que nao e raspagem nem afirmacao nossa de
medicao. O post sai marcado `verified=False`, igual ao repasse da vitrine do
ML -- nao medimos essa baseline.
"""

from __future__ import annotations

import re

# ASIN: dez caracteres, letras maiusculas e digitos. Produtos comecam com B0;
# livros usam o ISBN-10, que pode terminar em X. A regex aceita os dois.
_ASIN = r"[A-Z0-9]{10}"

# Os caminhos onde a Amazon poe o ASIN. `/dp/` cobre a maioria, inclusive a URL
# longa com o nome do produto na frente:
#   /MAX-TITANIUM-CREATINA-300-MONOHIDRATADA/dp/B07DVJC66X?ref=...
_CAMINHOS = re.compile(
    r"/(?:dp|gp/product|gp/aw/d|gp/offer-listing|product)/(" + _ASIN + r")(?:[/?#]|$)"
)
_ASIN_PURO = re.compile(r"^(" + _ASIN + r")$")

IMAGEM = "https://m.media-amazon.com/images/P/{asin}.jpg"
PRODUTO = "https://www.amazon.com.br/dp/{asin}"


def extrair_asin(entrada: str) -> str | None:
    """O ASIN de uma URL da Amazon, ou o proprio ASIN se ja vier so ele.

    Devolve None quando nao da para ter certeza. Chutar aqui sairia caro: um
    ASIN errado vira post apontando para outro produto, com o preco deste.
    """
    texto = (entrada or "").strip()
    if not texto:
        return None

    direto = _ASIN_PURO.match(texto.upper())
    if direto:
        return direto.group(1)

    # A parte do caminho e sensivel a caixa no ASIN, mas o dominio nao. Procura
    # no texto inteiro: a URL pode vir com ou sem esquema, com ou sem `www`.
    achado = _CAMINHOS.search(texto)
    return achado.group(1) if achado else None


def link_de_afiliado(asin: str, tag: str) -> str:
    """URL canonica do produto com a tag de associado.

    Canonica de proposito: a URL que o usuario copia do navegador carrega
    `ref=`, `pf_rd_r`, `sbo` e afins -- rastreadores da sessao DELE. Levar isso
    para o grupo publica o que nao e nosso e ainda concorre com a nossa tag.
    """
    return f"{PRODUTO.format(asin=asin)}?tag={tag}"


def imagem_do_asin(asin: str) -> str:
    """Imagem publica do produto, servida por ASIN.

    Nem todo ASIN tem: quando nao tem, a Amazon devolve um GIF transparente de
    1x1 em vez de 404, entao quem chama precisa checar o tamanho se quiser
    garantia. O comando aceita `--imagem` para o caso de a automatica sair ruim.
    """
    return IMAGEM.format(asin=asin)


# Abaixo disso nao e foto de produto. O pixel que a Amazon devolve no lugar da
# foto ausente tem 43 bytes; a menor foto real medida tinha 14 KB.
FOTO_MINIMA_BYTES = 1024

# Uma consulta por ASIN enquanto o processo vive. A rodada rele os mesmos grupos
# a cada 3 minutos, e a resposta da Amazon para um ASIN nao muda nesse prazo.
_FOTO_EXISTE: dict[str, bool] = {}


def foto_existe(asin: str) -> bool:
    """A Amazon tem foto de verdade para este ASIN?

    Medido em 17/10/2026: 142 de 505 posts da Amazon em 7 dias sairam com o GIF
    transparente de 1x1 no lugar da foto. A Evolution aceita o pixel e devolve
    201, entao nada acusava o erro -- o post chegava ao grupo sem imagem.

    Falha de rede conta como "tem foto": a duvida nao pode custar a oferta, e o
    envio ja tem as proprias tentativas.
    """
    if asin in _FOTO_EXISTE:
        return _FOTO_EXISTE[asin]

    import httpx

    try:
        resposta = httpx.get(imagem_do_asin(asin), timeout=10, follow_redirects=True)
    except httpx.HTTPError:
        return True

    tipo = resposta.headers.get("content-type", "")
    existe = (
        resposta.status_code == 200
        and "gif" not in tipo
        and len(resposta.content) >= FOTO_MINIMA_BYTES
    )
    _FOTO_EXISTE[asin] = existe
    return existe

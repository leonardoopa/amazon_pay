"""Le os cupons do Mercado Livre publicados no Pelando.

Por que aqui e nao no painel do proprio ML: o painel so mostra o cupom DEPOIS
que o dono clica em ativar. O bot enxergaria apenas o que ja foi ativado a
mao, e a leitura automatica seria falsa -- foi o que aconteceu, e por isso
`promo cupons` devolvia um cupom so.

O caminho e o sitemap que o proprio site publica para crawlers:

    /sitemaps/sitemap.xml -> /sitemaps/coupons_recent.xml

Medido em 08/09/2026: 290 cupons recentes, 121 do Mercado Livre. O robots.txt
libera bot generico (`User-agent: * Allow: /`, e `/d/` nao esta na lista de
Disallow) e os termos nao proibem leitura automatizada -- diferente do
Promobit, que proibe em clausula expressa.

O que da para saber sem abrir a pagina, direto do slug:

    cupom-mercado-livre-10porcento-off-acima-de-rdollar79-limitado-a-rdollar30
              -> 10%, minimo R$ 79, teto R$ 30

E o que so a pagina diz: o CODIGO, em `coupon-code-copiable`, e se ele ainda
vale, no `data-inactive` do mesmo elemento.

Cuidado que o codigo desta fonte exige: a pagina lista varios cupons na
lateral, e todos aparecem em `data-copy-code`. O da oferta e outro elemento.
Confundir os dois publicaria o cupom errado com a regra certa.

Fonte de comunidade: quem posta e usuario, e a data do slug e a da POSTAGEM,
nao a da validade. Por isso `data-inactive` manda, e por isso o alcance e
limitado ao que o site ainda marca como ativo.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

SITEMAP = "https://www.pelando.com.br/sitemaps/coupons_recent.xml"

# Identificacao honesta: diz o que e e para que serve, em vez de fingir
# navegador. O robots.txt do site libera bot generico.
USER_AGENT = "promo-bot/1.0 (leitor de cupons; contato via comunidadedodesconto.com.br)"

# O codigo da oferta mora aqui. `data-copy-code` e a lateral de relacionados,
# e traz cupom de outra oferta -- medido: os mesmos 8 codigos em 4 paginas
# diferentes.
# O recorte tambem aparece no CORPO da pagina, e nem sempre no slug. Medido em
# 09/09/2026: o TORCIDA saiu como se valesse para tudo -- o slug dizia so
# "10porcento-off-acima-de-rdollar79" --, e a pagina trazia "Em itens
# Selecionados". O post foi para o grupo com o cupom num whey onde ele nao
# aplicava.
RECORTE_NA_PAGINA = re.compile(r"Em\s+itens?\s+Selecionados?", re.I)

CODIGO = re.compile(
    r'class="coupon-code-copiable"[^>]*data-inactive="(?P<inativo>[^"]*)"'
    r'[^>]*>\s*<span class="code"[^>]*>(?P<code>[^<]{3,24})</span>',
    re.S,
)

MESES = {
    "jan": 1,
    "fev": 2,
    "mar": 3,
    "abr": 4,
    "mai": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "set": 9,
    "out": 10,
    "nov": 11,
    "dez": 12,
}

# "em selecionados" aparece em 46 dos 121 slugs do ML. Quando vem sozinho, o
# cupom vale so num recorte que ninguem publica -- e publicar como se valesse
# para tudo e o erro classico: a pessoa clica, tenta, falha e desconfia do
# grupo. Quando vem qualificado, o qualificador diz onde vale, e vira tema.
QUALIFICADOR = re.compile(r"selecionados-([a-z-]+?)-\d{2}-[a-z]{3}-\d{4}")
RESTRITO = re.compile(r"\bselecionados\b")

# O qualificador do slug traduzido para palavra de titulo. "full" fica de fora
# de proposito: e o deposito do ML, nao uma categoria de produto -- casaria com
# nada e faria o cupom sumir em silencio.
TEMAS_DO_QUALIFICADOR = {
    "moda-e-beleza": ["camiseta", "vestido", "calca", "perfume", "batom", "shampoo"],
    "casa-e-decor": ["sofa", "mesa", "cadeira", "cortina", "tapete", "luminaria"],
    "bebes-e-pets": ["bebe", "fralda", "racao", "pet"],
    "tvs-e-celulares": ["smart tv", "televisor"],
    "supermercado": ["cafe", "arroz", "feijao", "leite", "achocolatado"],
    "cell-pc-e-tech": ["notebook", "monitor", "teclado", "mouse"],
}

log = logging.getLogger("promo")


@dataclass(frozen=True)
class CupomPelando:
    code: str
    postado_em: str  # ISO
    desconto: float
    tipo: str  # PERCENT | FIXED
    minimo: float
    teto: float
    url: str
    temas: tuple[str, ...] = ()
    # Vale so "em itens selecionados", sem dizer quais. Nao impede o cupom de
    # sair -- ele funciona em parte do catalogo --, mas impede o post de
    # anunciar o preco ja com o desconto dele.
    restrito: bool = False

    def como_dict(self, validade_dias: int) -> dict:
        """No formato que `cupom_para` entende.

        `ate` e estimado a partir da POSTAGEM, porque o Pelando nao publica
        validade -- a data do slug e quando o usuario postou. A janela curta e
        a defesa: cupom desta fonte sai do ar sozinho depois de poucos dias,
        em vez de ficar sendo publicado para sempre.

        `temas` vazio aqui significa cupom de site inteiro, e nao ausencia de
        informacao: cupom com recorte que nao da para traduzir foi descartado
        antes, em `buscar`. A diferenca importa porque `cupom_para` trata
        lista vazia como "vale para tudo".
        """
        vence = date.fromisoformat(self.postado_em) + timedelta(days=validade_dias)
        return {
            "code": self.code,
            "ate": vence.isoformat(),
            "minimo": self.minimo,
            "teto": self.teto,
            "desconto": self.desconto,
            "tipo": self.tipo,
            "titulo": f"{self.desconto:g}{'%' if self.tipo == 'PERCENT' else ' BRL'} OFF",
            "categorias": [],
            "temas": list(self.temas),
            "restrito": self.restrito,
            "fonte": "pelando",
        }


def regra_do_slug(url: str) -> dict:
    """A regra que da para ler do endereco, sem abrir a pagina.

    Vale por si: 121 URLs do ML no sitemap, e a maioria traz desconto, minimo
    e teto no proprio slug. Abrir pagina e o custo; isto e de graca.
    """
    slug = url.rstrip("/").split("/")[-1]
    dados: dict = {}

    if m := re.search(r"(\d+)porcento", slug):
        dados["desconto"], dados["tipo"] = float(m.group(1)), "PERCENT"
    elif m := re.search(r"rdollar[- ]?(\d+)[- ]?off", slug):
        dados["desconto"], dados["tipo"] = float(m.group(1)), "FIXED"

    if m := re.search(r"acima-de-rdollar[- ]?(\d+)", slug):
        dados["minimo"] = float(m.group(1))
    if m := re.search(r"limitado-a-rdollar[- ]?(\d+)", slug):
        dados["teto"] = float(m.group(1))

    if m := re.search(r"(\d{2})-([a-z]{3})-(\d{4})", slug):
        mes = MESES.get(m.group(2))
        if mes:
            dados["postado_em"] = f"{m.group(3)}-{mes:02d}-{m.group(1)}"

    # Onde o cupom vale. Tres respostas possiveis, e as tres importam:
    #   temas preenchidos -> vale nesse recorte
    #   restrito e sem temas -> vale num recorte que nao da para saber
    #   nem um nem outro -> vale no site todo
    if achado := QUALIFICADOR.search(slug):
        dados["temas"] = TEMAS_DO_QUALIFICADOR.get(achado.group(1), [])
        dados["restrito"] = not dados["temas"]
    else:
        dados["temas"] = []
        dados["restrito"] = bool(RESTRITO.search(slug))
    return dados


def codigo_da_pagina(html: str) -> tuple[str, bool, bool] | None:
    """(codigo, ativo, restrito) da oferta principal, ou None se nao houver.

    `ativo` vem do `data-inactive`: o site marca quando a comunidade reporta
    que o cupom parou de funcionar. Nao e garantia -- e conteudo de usuario --,
    mas e melhor do que publicar tudo.

    `restrito` vem do texto "Em itens Selecionados", e ele NAO esta sempre no
    slug. Medido em 09/09/2026: o TORCIDA tinha slug limpo
    ("10porcento-off-acima-de-rdollar79") e a restricao so no corpo. O post
    saiu no grupo com o cupom num whey onde ele nao aplicava, e funcionou num
    pre-treino onde aplicava -- que e o pior dos mundos, porque parece
    aleatorio para quem le.
    """
    achado = CODIGO.search(html)
    if not achado:
        return None
    inativo = achado.group("inativo").strip().lower() in {"true", "1"}
    restrito = bool(RECORTE_NA_PAGINA.search(html))
    return achado.group("code").strip(), not inativo, restrito


def _e_do_mercado_livre(url: str) -> bool:
    return "mercado-livre" in url or "-meli-" in url


def buscar(
    limite: int = 20, pausa: float = 1.2, timeout: float = 25.0
) -> list[CupomPelando]:
    """Os cupons do ML mais recentes que o Pelando ainda marca como ativos.

    `limite` existe porque sao 121 URLs do ML no sitemap e abrir todas seria
    121 requisicoes a um site de terceiro a cada leitura. Os mais recentes sao
    os que importam: cupom de campanha dura dias, e o sitemap vem em ordem.

    `pausa` entre paginas por educacao com quem hospeda -- nao ha nada a
    ganhar indo rapido, a leitura roda a cada tres horas.
    """
    with httpx.Client(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as cliente:
        indice = cliente.get(SITEMAP)
        indice.raise_for_status()
        urls = [
            u
            for u in re.findall(r"<loc>([^<]+)</loc>", indice.text)
            if _e_do_mercado_livre(u)
        ]
        log.info(
            "Pelando: %d cupons do ML no sitemap; lendo os %d mais recentes.",
            len(urls),
            min(limite, len(urls)),
        )

        cupons: list[CupomPelando] = []
        vistos: set[str] = set()
        for url in urls[:limite]:
            regra = regra_do_slug(url)
            if "desconto" not in regra or "postado_em" not in regra:
                continue

            # "em selecionados" sem dizer quais. Sao 46 dos 121 slugs do ML, e
            # publicar um deles como se valesse para tudo e o erro que o cupom
            # deveria evitar: a pessoa clica, tenta e falha. Nao ha de onde
            # tirar a lista -- ela so existe dentro do carrinho do ML.
            try:
                pagina = cliente.get(url)
                pagina.raise_for_status()
            except httpx.HTTPError as exc:
                log.debug("Pelando: %s nao abriu (%s).", url[-40:], exc)
                continue

            achado = codigo_da_pagina(pagina.text)
            time.sleep(pausa)
            if not achado:
                continue
            codigo, ativo, restrito_na_pagina = achado
            if not ativo:
                log.debug("Pelando: %s marcado inativo; fora.", codigo)
                continue
            # O slug nem sempre diz; a pagina diz. Medido em 09/09/2026: o
            # TORCIDA tinha slug limpo e "Em itens Selecionados" no corpo.
            #
            # Restrito NAO descarta o cupom. Ele funciona em parte do catalogo
            # -- o mesmo TORCIDA aplicou num pre-treino e numa progressiva, e
            # nao aplicou num whey. Descartar tiraria os dois que deram certo.
            # O que ele proibe e mexer no PRECO: anunciar o valor ja com
            # desconto de um cupom que talvez nao aplique e pior do que nao
            # citar cupom nenhum.
            restrito = restrito_na_pagina or bool(regra.get("restrito"))
            if codigo in vistos:
                continue
            vistos.add(codigo)

            cupons.append(
                CupomPelando(
                    code=codigo,
                    postado_em=regra["postado_em"],
                    desconto=regra["desconto"],
                    tipo=regra.get("tipo", "PERCENT"),
                    minimo=regra.get("minimo", 0.0),
                    teto=regra.get("teto", 0.0),
                    url=url,
                    temas=tuple(regra.get("temas") or ()),
                    restrito=restrito,
                )
            )

    restritos = sum(1 for c in cupons if c.restrito)
    log.info(
        "Pelando: %d cupom(ns) ativo(s) do ML (%s); %d valem so em itens "
        "selecionados e por isso nao entram no preco anunciado.",
        len(cupons),
        ", ".join(c.code for c in cupons) or "nenhum",
        restritos,
    )
    return cupons

"""Le os cupons de campanha vigentes no painel de cupons do Mercado Livre.

Isto NAO e API publica -- e a mesma situacao do `ml_linkbuilder`, e usa o mesmo
cookie de sessao. Nao ha alternativa oficial: sondadas em 08/09/2026, 17 rotas
de cupom da API devolvem 404 "resource not found", e a unica viva
(`/seller-promotions`) e do lado vendedor, para inscrever os proprios anuncios.

Duas paginas, e so uma serve:

    /cupons?source_page=mperfil            42 cupons, ZERO com codigo digitavel
    /cupons/active?source_page=...         13 cupons, com os codigos de campanha

A primeira lista os cupons ATRIBUIDOS a conta -- os que a pessoa ativa
clicando. Os `code` que ela traz sao tokens de 88 caracteres, identificadores
de ativacao pessoal, inuteis para publicar. A segunda traz o que interessa: o
`OFERTASEMPRE` que o grupo concorrente postou apareceu aqui, com validade,
minimo e teto.

O formato de cada cupom, medido:

    {"campaign_id": "...", "code": "OFERTASEMPRE",
     "title": "18% OFF com OFERTASEMPRE",
     "discount_type": "PERCENT", "discount_value": 18,
     "min_amount": 79, "cap_amount": 50,
     "expiration_date": "2026-09-09T02:59:00Z",
     "segmentations": {"categories": ["MLB1430", "MLB107292", ...]},
     "item_ids": [...]}

A regra de categoria vem como ID do ML, nao como texto -- e por isso vale mais
do que qualquer heuristica de titulo. Nos gravamos `products.category` com o
mesmo vocabulario.

O que este modulo assume, igual ao linkbuilder:

- Quebra sem aviso. E pagina interna, nao contrato publico. Todo erro aqui e
  nao-fatal: sem cupom o post sai como sempre saiu, e assim foi nos 2.953 posts
  entre 29/08 e 08/09.
- O cookie expira, e a saida e recapturar no navegador.
- O cookie e a conta inteira. Mora no .env e nunca aparece em log: as mensagens
  daqui citam o nome da variavel, nunca o valor.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

import httpx

ATIVOS = (
    "https://www.mercadolivre.com.br/cupons/active"
    "?source_page=int_quick_access_active_button"
)
MAIS_USADOS = "https://www.mercadolivre.com.br/cupons/filter?most_used=true&page={page}"

# Paginas do filtro "mais usados". Medido em 08/09/2026: 3 paginas dao 92
# cupons distintos e a quarta nao acrescentou codigo digitavel nenhum. Ler
# mais custa requisicao para repetir o que ja veio.
PAGINAS = 3

# Sem User-Agent de navegador o ML devolve 403 -- medido: 2.586 bytes, sem
# nenhum cupom. Mesma necessidade do painel de afiliados.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# O estado da pagina nao esta num `window.__X__` unico: cada cupom aparece
# solto no HTML, comecando nesta chave. Decodificar um a um a partir dela e
# mais robusto do que tentar achar o array inteiro, que muda de lugar.
ABERTURA = re.compile(r'\{"campaign_id":')

# Codigo de campanha e curto e digitavel. Os de 88 caracteres da outra pagina
# sao token de ativacao pessoal, e publicar um deles seria mandar o grupo
# digitar algo que so vale para a conta do dono.
MAX_CODIGO = 24

log = logging.getLogger("promo")


class SessionExpired(RuntimeError):
    """O cookie do painel nao vale mais; so recapturando no navegador."""


@dataclass(frozen=True)
class Cupom:
    """Um cupom de campanha, ja normalizado para o formato do watchlist."""

    code: str
    ate: str  # ISO, so a data
    minimo: float
    teto: float
    desconto: float
    tipo: str  # PERCENT | FIXED
    titulo: str
    categorias: list[str]  # IDs do ML, ex.: MLB1430

    def como_dict(self) -> dict:
        return {
            "code": self.code,
            "ate": self.ate,
            "minimo": self.minimo,
            "teto": self.teto,
            "desconto": self.desconto,
            "tipo": self.tipo,
            "titulo": self.titulo,
            "categorias": self.categorias,
        }


def _cupons_do_html(html: str) -> list[dict]:
    """Todo objeto de cupom embutido na pagina, sem repetir campanha."""
    achados: dict[str, dict] = {}
    for marca in ABERTURA.finditer(html):
        try:
            obj, _ = json.JSONDecoder().raw_decode(html[marca.start() :])
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("campaign_id"):
            achados[obj["campaign_id"]] = obj
    return list(achados.values())


def _validade(cru: dict) -> str | None:
    """A data de expiracao como ISO simples, ou None se vier ilegivel.

    O ML manda com fuso Z. Guardamos so a data porque o post nao tem precisao
    de hora, e um cupom que vence 02:59Z ja nao vale no dia seguinte aqui.
    """
    bruto = cru.get("expiration_date") or ""
    if not bruto:
        return None
    try:
        return datetime.fromisoformat(bruto.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        log.warning("Cupom %s com data ilegivel: %r", cru.get("code"), bruto)
        return None


def normalizar(crus: list[dict]) -> list[Cupom]:
    """So os cupons publicaveis: codigo digitavel, no prazo, com regra.

    Descarta em silencio o que nao serve, porque a maioria nao serve por
    construcao -- dos 13 ativos medidos em 08/09/2026, 12 nao tinham codigo:
    sao cupons aplicados automaticamente na conta, sem nada a digitar.
    """
    hoje = date.today()
    cupons = []
    for cru in crus:
        codigo = (cru.get("code") or "").strip()
        if not codigo or len(codigo) > MAX_CODIGO:
            continue

        ate = _validade(cru)
        if not ate:
            log.info("Cupom %s sem data de validade legivel; fora.", codigo)
            continue
        if date.fromisoformat(ate) < hoje:
            log.info("Cupom %s venceu em %s; fora.", codigo, ate)
            continue

        segmentacao = cru.get("segmentations") or {}
        cupons.append(
            Cupom(
                code=codigo,
                ate=ate,
                minimo=float(cru.get("min_amount") or 0),
                teto=float(cru.get("cap_amount") or 0),
                desconto=float(cru.get("discount_value") or 0),
                tipo=(cru.get("discount_type") or "").upper(),
                titulo=(cru.get("title") or "").strip(),
                categorias=[str(c) for c in segmentacao.get("categories") or []],
            )
        )
    return cupons


def _pagina(cliente: httpx.Client, url: str, cookie: str) -> list[dict]:
    """Uma requisicao ao painel, ja parseada. Levanta se a sessao caiu."""
    resposta = cliente.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cookie": cookie,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "pt-BR,pt;q=0.9",
        },
    )

    if resposta.status_code in (401, 403):
        raise SessionExpired(
            f"O painel de cupons respondeu {resposta.status_code}. O cookie "
            "provavelmente expirou: capture de novo no navegador e atualize "
            "ML_AFFILIATE_COOKIE no .env."
        )
    resposta.raise_for_status()

    # Login devolve 200 com a pagina de entrada, entao o status nao basta.
    if "/cupons" not in str(resposta.url):
        raise SessionExpired(
            f"O painel redirecionou para {str(resposta.url)[:80]}. Sessao caiu; "
            "atualize ML_AFFILIATE_COOKIE."
        )

    return _cupons_do_html(resposta.text)


def buscar(cookie: str, timeout: float = 25.0) -> list[Cupom]:
    """Os cupons de campanha publicaveis agora.

    Varre as duas listas -- os ativos da conta e o filtro "mais usados",
    paginado -- porque elas nao coincidem, e uniformiza por `campaign_id`.

    O rendimento e baixo por natureza, e isso e da fonte, nao do codigo.
    Medido em 08/09/2026: 92 cupons distintos nas tres paginas de "mais
    usados", e UM com codigo digitavel. Os outros sao cupons de ativacao --
    a pessoa clica no painel dela e o desconto entra sozinho, sem nada a
    digitar, e nada disso pode ser publicado para terceiro.

    Levanta `SessionExpired` quando o cookie caiu.
    """
    if not cookie:
        raise SessionExpired(
            "ML_AFFILIATE_COOKIE nao configurado; sem ele nao da para ler os cupons."
        )

    crus: dict[str, dict] = {}
    with httpx.Client(timeout=timeout, follow_redirects=True) as cliente:
        for cru in _pagina(cliente, ATIVOS, cookie):
            crus[cru["campaign_id"]] = cru
        for pagina in range(1, PAGINAS + 1):
            achados = _pagina(cliente, MAIS_USADOS.format(page=pagina), cookie)
            if not achados:
                break
            for cru in achados:
                crus.setdefault(cru["campaign_id"], cru)

    cupons = normalizar(list(crus.values()))
    log.info(
        "Cupons do ML: %d distintos no painel, %d publicaveis (%s).",
        len(crus),
        len(cupons),
        ", ".join(c.code for c in cupons) or "nenhum",
    )
    return cupons

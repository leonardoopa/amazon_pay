"""Gera link de afiliado do ML pelo mesmo endpoint que o Link Builder usa.

Isto NAO e API publica. E o backend do painel de afiliados, autenticado por
cookie de sessao do navegador -- o mesmo que o `promo link` substituia com
colagem manual. Nao ha alternativa oficial: o ML nao expoe API de afiliado, e
o link nao pode ser montado a mao (a URL final aponta pra /social/<nickname>
com um `ref` de ~150 bytes assinado pelo servidor; o ID do produto nem aparece
nela).

O que isso implica, e que o codigo assume:

- Quebra sem aviso. E pagina interna, versionada pelo time deles, nao contrato
  publico. Por isso todo erro aqui e nao-fatal: o pipeline volta a segurar a
  oferta e o `promo link` manual continua valendo.
- O cookie expira. Quando expirar, a unica saida e capturar de novo no
  navegador -- por isso a mensagem de erro diz exatamente como.
- O cookie e a conta inteira, nao so o painel. Ele mora no .env (gitignored) e
  nunca aparece em log: as mensagens daqui citam o nome da variavel, nunca o
  valor.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

PAINEL = "https://www.mercadolivre.com.br/afiliados/linkbuilder"
ENDPOINT = (
    "https://www.mercadolivre.com.br/affiliate-program/api/v2/affiliates/createLink"
)

# O painel e uma pagina normal do site: sem User-Agent de navegador o ML
# devolve outra coisa (ou nada) e o token nao aparece.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# O x-csrf-token NAO e o cookie _csrf -- sao valores distintos, e o do header
# muda a cada carregamento da pagina. Entao ele tem que ser raspado na hora,
# nao guardado no .env.
CSRF_META = re.compile(r'name="csrf-token"\s+content="([^"]+)"')

log = logging.getLogger("promo")


class SessionExpired(RuntimeError):
    """O cookie do painel nao vale mais; so recapturando no navegador."""


@dataclass(frozen=True)
class LinkResult:
    """Saida de uma chamada em lote.

    `recusados` nao e "falhou": e o ML dizendo que aquele anuncio esta fora do
    programa. Produto recusado nunca vai gerar link, entao quem chama precisa
    guardar isso e parar de pedir -- e a diferenca entre uma consulta por
    rodada pra sempre e nenhuma.
    """

    links: dict[str, str]  # url de origem -> short_url
    recusados: dict[str, str]  # url de origem -> motivo do ML


class LinkBuilder:
    def __init__(self, cookie: str, tag: str) -> None:
        self._tag = tag
        self._client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"user-agent": USER_AGENT, "cookie": cookie},
        )
        self._csrf: str | None = None
        self._sessao_morta: str | None = None

    def _token(self, forcar: bool = False) -> str:
        # A sessao morta e lembrada: sem isso, uma rodada com 5 ofertas baixava
        # o painel de 770 KB cinco vezes pra falhar cinco vezes igual.
        if self._sessao_morta is not None and not forcar:
            raise SessionExpired(self._sessao_morta)

        """Raspa o csrf-token do painel, reaproveitando entre chamadas.

        Reaproveitar importa: sem cache seria uma pagina de 770 KB baixada pra
        cada link gerado. O `forcar` existe pro caso do token virar durante uma
        rodada longa -- ai a gente busca de novo em vez de falhar a oferta.
        """
        if self._csrf and not forcar:
            return self._csrf

        response = self._client.get(PAINEL, headers={"accept": "text/html"})
        if response.status_code >= 400:
            self._sessao_morta = (
                f"O painel de afiliados respondeu {response.status_code}. "
                "Recapture ML_AFFILIATE_COOKIE (veja o README)."
            )
            raise SessionExpired(self._sessao_morta)

        achado = CSRF_META.search(response.text)
        if not achado:
            self._sessao_morta = (
                "Nao achei o csrf-token no painel de afiliados. O cookie de "
                "ML_AFFILIATE_COOKIE expirou (ou o ML mudou a pagina). "
                "Recapture no navegador -- veja o README."
            )
            # Deslogado o ML serve a pagina de login, que tambem e 200 -- entao
            # a ausencia do token e o sinal confiavel, nao o status.
            raise SessionExpired(self._sessao_morta)

        self._sessao_morta = None
        self._csrf = achado.group(1)
        return self._csrf

    def create(self, urls: list[str]) -> "LinkResult":
        """Gera os links em lote.

        Em lote de proposito: o endpoint aceita varias URLs numa chamada, e o
        backfill do `link-all` passa por dezenas de produtos de uma vez.

        Chamar de novo pro mesmo produto e seguro -- o ML devolve o mesmo id,
        nao cria link duplicado.

        A resposta e 200 mesmo quando uma URL e recusada: o erro vem por item,
        em `message`/`error_code` no lugar do `short_url`. Separar os dois casos
        importa porque eles pedem tratamento oposto -- falha de rede merece nova
        tentativa na proxima rodada, "URL not allowed in affiliates program"
        (codigo 111) nao merece nenhuma, nunca.
        """
        if not urls:
            return LinkResult({}, {})

        response = self._post(urls)
        if response.status_code in (401, 403):
            # Token velho e sessao morta dao o mesmo status. Tenta renovar o
            # token uma vez; se o cookie e que morreu, o _token avisa direito.
            self._token(forcar=True)
            response = self._post(urls)

        if response.status_code >= 400:
            raise RuntimeError(
                f"createLink recusou ({response.status_code}): {response.text[:200]}"
            )

        gerados: dict[str, str] = {}
        recusados: dict[str, str] = {}
        for item in response.json().get("urls", []):
            origem = item.get("origin_url")
            if not origem:
                continue
            curto = item.get("short_url")
            if curto:
                gerados[origem] = curto
            elif item.get("message") or item.get("error_code"):
                # Recusa de verdade: o ML disse o motivo.
                recusados[origem] = item.get("message") or f"erro {item['error_code']}"
            else:
                # Item ecoado sem short_url e sem motivo -- soluco do backend
                # deles. Tratar como recusa custaria 30 dias de bloqueio (e de
                # comissao) por uma falha que passa na proxima rodada.
                log.warning(
                    "Painel devolveu %s sem link e sem motivo; tentaremos depois.",
                    origem,
                )
        return LinkResult(gerados, recusados)

    def _post(self, urls: list[str]) -> httpx.Response:
        return self._client.post(
            ENDPOINT,
            json={"urls": urls, "tag": self._tag},
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/plain, */*",
                "origin": "https://www.mercadolivre.com.br",
                # Sem o referer do painel o endpoint recusa: ele so espera
                # chamada vinda da propria pagina.
                "referer": PAINEL,
                "x-csrf-token": self._token(),
            },
        )

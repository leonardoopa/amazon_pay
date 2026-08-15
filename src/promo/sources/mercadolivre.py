"""Mercado Livre.

O ML so aceita grant_type=authorization_code e refresh_token -- nao existe
client_credentials. Entao voce autoriza uma vez no navegador (`promo ml-auth`)
e a partir dai o refresh_token guardado no SQLite mantem o acesso vivo.

O DevCenter exige HTTPS no redirect URI e recusa localhost, entao nao da pra
subir um servidor de callback local. O fluxo e manual: o navegador redireciona
para o endereco registrado, a pagina nao serve pra nada, e voce cola a URL da
barra de enderecos aqui -- o `code` esta nela.

Sobre o endereco de redirect: prefira um dominio seu, ou um inerte como
example.com (reservado pela IANA). Evite os servicos "cole seu code aqui" --
eles existem pra ler exatamente esse valor. O risco e limitado porque trocar
o code por token tambem exige o client_secret, que nunca sai da sua maquina,
mas nao ha motivo pra entregar o code a terceiros.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import urllib.parse
import webbrowser
from datetime import timedelta

import httpx

from ..config import MercadoLivreConfig, products_per_category, products_per_keyword
from ..db import (
    affiliate_blocked,
    affiliate_link,
    connect,
    mark_affiliate_blocked,
    load_token,
    now,
    save_affiliate_link,
    save_token,
)
from ..models import Offer

AUTH_HOST = "https://auth.mercadolivre.com.br"
API_HOST = "https://api.mercadolibre.com"
PROVIDER = "mercadolivre"

log = logging.getLogger("promo")

# `offline_access` e o que faz o ML devolver refresh_token; sem ele o acesso
# morre com o access_token e o daemon para sozinho ate alguem reautorizar.
# `read` basta: o bot so consulta catalogo publico, nunca escreve na conta.
SCOPES = "offline_access read"

# Site publico: o catalogo nao devolve permalink (vem string vazia), entao a
# URL do produto e montada a partir do ID.
SITE_HOST = "https://www.mercadolivre.com.br"

# Quantos produtos de catalogo pegar por termo da watchlist. Cada produto
# custa um GET a mais pra descobrir o preco, entao isso multiplica a rodada.
PRODUCTS_PER_KEYWORD = 10

# Idem para os mais vendidos de uma categoria. O endpoint sempre devolve 20,
# mas aqui cada produto custa DOIS GETs (nome/foto + preco) porque a lista de
# destaques so traz IDs -- entao o teto e mais apertado que o da busca.
PRODUCTS_PER_CATEGORY = 10


def _best_image(product: dict) -> str | None:
    """Maior imagem do produto de catalogo.

    O catalogo ja entrega `pictures` na resolucao cheia (sufixo -F na CDN,
    ~39 KB contra 1,7 KB do thumbnail), entao nao ha o que promover aqui.
    O fallback do thumbnail fica pra quem vier de uma rota mais pobre.
    """
    for picture in product.get("pictures") or []:
        url = picture.get("secure_url") or picture.get("url")
        if url:
            return url

    thumbnail = product.get("thumbnail")
    if thumbnail and "-I." in thumbnail:
        return thumbnail.replace("-I.", "-O.")
    return thumbnail


def new_pkce_pair() -> tuple[str, str]:
    """Gera (code_verifier, code_challenge) no metodo S256 do RFC 7636.

    Importa aqui porque o redirect registrado e um dominio de terceiro: o
    `code` aparece na URL que o navegador visita. Com PKCE ele e inutil sem o
    verifier, que so existe nesta execucao e nunca sai da maquina.
    """
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


class MercadoLivre:
    name = PROVIDER

    def __init__(self, config: MercadoLivreConfig) -> None:
        self.config = config
        self._client = httpx.Client(timeout=20.0)
        self._builder = None  # LinkBuilder, criado sob demanda

    # ---------- OAuth ----------

    def authorize_url(self, state: str, code_challenge: str | None = None) -> str:
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "state": state,
            "scope": SCOPES,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{AUTH_HOST}/authorization?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str, code_verifier: str | None = None) -> None:
        payload = {
            "grant_type": "authorization_code",
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code": code,
            "redirect_uri": self.config.redirect_uri,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        data = self._token_request(payload)
        if not data.get("refresh_token"):
            # Falhar aqui em vez de daqui a algumas horas, quando o access_token
            # expirar e o daemon parar sem ninguem entender por que.
            raise RuntimeError(
                "O Mercado Livre nao devolveu refresh_token. O app foi criado sem "
                "a permissao offline_access -- sem ela o acesso expira em poucas "
                "horas e o daemon para. Ajuste as permissoes no DevCenter e rode "
                f"ml-auth de novo. (scopes concedidos: {data.get('scope', 'nenhum')})"
            )

    def _refresh(self, refresh_token: str) -> None:
        self._token_request(
            {
                "grant_type": "refresh_token",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "refresh_token": refresh_token,
            }
        )

    def _token_request(self, payload: dict[str, str]) -> dict:
        response = self._client.post(
            f"{API_HOST}/oauth/token",
            data=payload,
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            # A mensagem do ML diz o que houve (redirect_uri divergente, code
            # expirado, secret errado); o raise_for_status sozinho esconderia.
            raise RuntimeError(
                f"Mercado Livre recusou o token ({response.status_code}): {response.text[:300]}"
            )
        data = response.json()
        expires_at = now() + timedelta(seconds=int(data.get("expires_in", 21600)) - 60)
        with connect() as conn:
            save_token(
                conn,
                PROVIDER,
                data["access_token"],
                data.get("refresh_token"),
                expires_at,
            )
        return data

    def access_token(self) -> str:
        from datetime import datetime

        with connect() as conn:
            row = load_token(conn, PROVIDER)

        if row is None:
            raise RuntimeError(
                "Mercado Livre nao autorizado ainda. Rode o comando `ml-auth`."
            )

        if datetime.fromisoformat(row["expires_at"]) > now():
            return row["access_token"]

        if not row["refresh_token"]:
            raise RuntimeError(
                "Token do ML expirou e nao ha refresh_token. Rode ml-auth de novo."
            )

        self._refresh(row["refresh_token"])
        with connect() as conn:
            return load_token(conn, PROVIDER)["access_token"]

    # ---------- Busca ----------

    def search(self, keyword: str, limit: int | None = None) -> list[Offer]:
        """Descobre produtos de catalogo para um termo.

        Uma chamada ja traz nome e fotos de cada produto; o preco vem depois,
        um GET por produto em /products/{id}/items.
        """
        limit = limit or products_per_keyword()
        response = self._client.get(
            f"{API_HOST}/products/search",
            params={
                "site_id": self.config.site_id,
                "status": "active",
                "q": keyword,
                "limit": limit,
            },
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        response.raise_for_status()

        offers = []
        for product in response.json().get("results", [])[:limit]:
            offer = self._offer_for(
                product["id"],
                title=product.get("name") or "",
                image_url=_best_image(product),
            )
            if offer is not None:
                offers.append(offer)
        return offers

    def highlights(self, category_id: str, limit: int | None = None) -> list[Offer]:
        """Mais vendidos de uma categoria.

        Este e o mais perto que o ML chega de "me diga o que esta bombando":
        nao existe endpoint de ofertas/promocoes para afiliado, entao a
        descoberta por categoria substitui ter que adivinhar termos de busca.

        Cuidado com o custo: a lista traz so IDs, entao cada produto vira duas
        chamadas -- uma pro nome e foto, outra pro preco.
        """
        limit = limit or products_per_category()
        response = self._client.get(
            f"{API_HOST}/highlights/{self.config.site_id}/category/{category_id}",
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        if response.status_code == 404:
            log.warning("Categoria %s nao tem destaques (ou nao existe)", category_id)
            return []
        response.raise_for_status()

        # USER_PRODUCT e anuncio de um vendedor especifico, nao produto de
        # catalogo -- /products/{id}/items nao responde por ele. Vem misturado
        # na lista (4 em 20 em algumas categorias).
        ids = [
            item["id"]
            for item in response.json().get("content", [])
            if item.get("type") == "PRODUCT" and item.get("id")
        ][:limit]

        offers = []
        for product_id in ids:
            meta = self._product_meta(product_id)
            if meta is None:
                continue
            title, image_url = meta
            offer = self._offer_for(product_id, title=title, image_url=image_url)
            if offer is not None:
                offers.append(offer)
        return offers

    def _product_meta(self, product_id: str) -> tuple[str, str | None] | None:
        """Nome e foto de um produto de catalogo.

        So e preciso na descoberta por categoria: a busca por termo ja devolve
        os dois na mesma resposta, e a reconsulta le do banco.
        """
        response = self._client.get(
            f"{API_HOST}/products/{product_id}",
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        product = response.json()
        return (product.get("name") or ""), _best_image(product)

    def categories(self) -> list[tuple[str, str]]:
        """Categorias raiz do site: (id, nome). Usado pelo `ml-categories`."""
        response = self._client.get(
            f"{API_HOST}/sites/{self.config.site_id}/categories",
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        response.raise_for_status()
        return [(c["id"], c["name"]) for c in response.json()]

    def fetch_by_ids(self, tracked: list[tuple[str, str, str | None]]) -> list[Offer]:
        """Reconsulta produtos ja conhecidos: (external_id, titulo, imagem).

        Nome e foto vem do banco em vez de uma segunda chamada -- eles quase
        nao mudam, e o que interessa aqui e o preco de hoje. Um GET por
        produto; o teto por rodada e ML_TRACK_LIMIT.
        """
        offers = []
        for external_id, title, image_url in tracked:
            offer = self._offer_for(external_id, title=title, image_url=image_url)
            if offer is not None:
                offers.append(offer)
        return offers

    def _offer_for(
        self, product_id: str, title: str, image_url: str | None
    ) -> Offer | None:
        """Menor preco entre os anuncios ativos de um produto de catalogo.

        O mesmo produto tem dezenas de anuncios de vendedores diferentes (27
        num celular tipico, variando de R$ 1.289 a R$ 1.899). O que importa
        pro grupo e o menor preco disponivel, entao a baseline e o historico
        do minimo -- nao de um anuncio especifico, que pode sumir amanha.
        """
        response = self._client.get(
            f"{API_HOST}/products/{product_id}/items",
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        if response.status_code == 404:
            return None  # produto saiu do catalogo
        response.raise_for_status()

        # `condition` importa alem da qualidade: anuncio usado fica fora das
        # regras do programa de afiliados.
        candidatos = [
            listing
            for listing in response.json().get("results", [])
            if listing.get("price") and listing.get("condition") == "new"
        ]
        if not candidatos:
            return None

        melhor = min(candidatos, key=lambda listing: listing["price"])
        shipping = melhor.get("shipping") or {}
        return Offer(
            source=PROVIDER,
            external_id=product_id,
            title=title,
            price=float(melhor["price"]),
            original_price=(
                float(melhor["original_price"])
                if melhor.get("original_price")
                else None
            ),
            url=f"{SITE_HOST}/p/{product_id}",
            currency=melhor.get("currency_id", "BRL"),
            image_url=image_url,
            category=melhor.get("category_id"),
            available=True,  # ter anuncio ativo na lista ja e a disponibilidade
            free_shipping=bool(shipping.get("free_shipping")),
            official_store=bool(melhor.get("official_store_id")),
        )

    # ---------- Afiliado ----------

    def _link_builder(self) -> "LinkBuilder | None":
        """Cliente do painel, ou None se a sessao nao foi configurada."""
        from .ml_linkbuilder import LinkBuilder

        if not (self.config.affiliate_cookie and self.config.affiliate_tag):
            return None
        if self._builder is None:
            self._builder = LinkBuilder(
                self.config.affiliate_cookie, self.config.affiliate_tag
            )
        return self._builder

    def affiliate_url(self, offer: Offer) -> str | None:
        """Link de afiliado do produto: do banco, ou gerado pelo painel.

        Nao da pra montar esse link. Um link real do programa aponta pra
        `mercadolivre.com.br/social/<nickname>?...&ref=<blob>`, onde o blob
        tem ~150 bytes assinados pelo servidor e o ID do produto nem aparece
        na URL. Concatenar matt_word/matt_tool na URL do produto -- que e o
        que varios projetos por ai fazem -- produz um endereco diferente do
        que o programa emite.
        """
        from .affiliate import resolve_affiliate_link

        return resolve_affiliate_link(self._link_builder(), offer)


def probe(config: MercadoLivreConfig) -> list[tuple[str, int, str]]:
    """Bate nos endpoints candidatos com o token real e diz quais respondem.

    O ML foi fechando a API publica sem anunciar, e endpoint por endpoint: hoje
    `/sites/{site}/search` devolve 403 por politica mesmo com token valido e
    app registrado, enquanto `/products/search` responde normal. Como isso muda
    de novo sem aviso, a estrategia de coleta nao pode ser deduzida da doc --
    tem que ser medida na conta que vai rodar.

    Devolve (rotulo, status HTTP, resumo) na ordem em que foram testados.
    """
    client = MercadoLivre(config)
    headers = {"Authorization": f"Bearer {client.access_token()}"}
    site = config.site_id

    BUSCA_CATALOGO = "catalogo: busca por termo (usado hoje)"
    ANUNCIOS = "anuncios do produto (de onde sai o preco)"

    alvos = [
        (
            BUSCA_CATALOGO,
            f"{API_HOST}/products/search",
            {"site_id": site, "status": "active", "q": "fone bluetooth", "limit": 1},
        ),
        # URL vazia: so da pra montar depois que a busca acima devolver um ID.
        (ANUNCIOS, "", {}),
        (
            "busca de anuncios (bloqueada desde 2025)",
            f"{API_HOST}/sites/{site}/search",
            {"q": "fone bluetooth", "limit": 1},
        ),
        (
            "mais vendidos da categoria",
            f"{API_HOST}/highlights/{site}/category/MLB1051",
            {},
        ),
        (
            "categorias do site",
            f"{API_HOST}/sites/{site}/categories",
            {},
        ),
    ]

    resultados: list[tuple[str, int, str]] = []
    primeiro_produto: str | None = None

    for rotulo, url, params in alvos:
        if rotulo == ANUNCIOS:
            if primeiro_produto is None:
                resultados.append(
                    (rotulo, 0, "pulado: a busca de catalogo nao devolveu produto")
                )
                continue
            url = f"{API_HOST}/products/{primeiro_produto}/items"

        try:
            response = client._client.get(url, params=params, headers=headers)
        except (
            Exception
        ) as exc:  # noqa: BLE001 - rede fora nao pode matar o diagnostico
            resultados.append((rotulo, 0, f"erro de rede: {exc}"))
            continue

        resumo = _resumo_da_resposta(response)
        if rotulo == BUSCA_CATALOGO and response.status_code == 200:
            achados = response.json().get("results") or []
            if achados:
                primeiro_produto = achados[0].get("id")

        resultados.append((rotulo, response.status_code, resumo))

    return resultados


def _resumo_da_resposta(response: httpx.Response) -> str:
    """Uma linha util: quantos itens vieram, ou o motivo da recusa."""
    if response.status_code >= 400:
        try:
            corpo = response.json()
        except ValueError:
            return response.text[:120]
        return str(corpo.get("message") or corpo.get("error") or corpo)[:120]

    try:
        corpo = response.json()
    except ValueError:
        return f"{len(response.content)} bytes"

    if isinstance(corpo, list):
        return f"{len(corpo)} itens"
    resultados = corpo.get("results")
    if isinstance(resultados, list):
        return f"{len(resultados)} resultados"
    return f"ok ({', '.join(list(corpo)[:4])})"


def parse_callback(
    pasted: str, expected_state: str, expected_redirect_uri: str | None = None
) -> str:
    """Extrai o `code` da URL de callback colada, validando state e redirect.

    Aceita tanto a URL inteira quanto so a query string, porque e comum a
    pessoa colar so um pedaco.

    A conferencia do redirect_uri existe porque a divergencia entre o que esta
    no .env e o que esta registrado no DevCenter e silenciosa ate o pior
    momento: a autorizacao no navegador funciona (quem manda ali e o DevCenter),
    e so a troca do code falha, com um `invalid_grant` generico que nao diz
    qual dos dois lados esta errado. O code e de uso unico, entao cada tentativa
    custa uma volta inteira no navegador. Melhor barrar aqui, com o nome dos
    dois valores na tela.
    """
    pasted = pasted.strip()
    if not pasted:
        raise RuntimeError("Nada colado.")

    parsed = urllib.parse.urlparse(pasted)
    query = parsed.query or pasted.lstrip("?")
    params = urllib.parse.parse_qs(query)

    # So compara quando veio URL inteira; colar so a query string e legitimo.
    if expected_redirect_uri and parsed.scheme:
        veio = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        esperado = expected_redirect_uri.rstrip("/")
        if veio.rstrip("/") != esperado:
            raise RuntimeError(
                "O redirect_uri do .env nao e o que o Mercado Livre usou.\n"
                f"  ML_REDIRECT_URI (.env) : {expected_redirect_uri}\n"
                f"  para onde o ML mandou  : {veio}\n"
                "O valor do .env tem que ser identico ao registrado no DevCenter "
                "-- e o segundo acima e justamente o que esta registrado la. "
                "Ajuste o .env e rode ml-auth de novo."
            )

    if "error" in params:
        detail = params.get("error_description", params["error"])[0]
        raise RuntimeError(f"Mercado Livre recusou a autorizacao: {detail}")

    state = (params.get("state") or [None])[0]
    if state != expected_state:
        raise RuntimeError(
            "State divergente -- a URL nao veio desta execucao. Rode ml-auth de novo."
        )

    code = (params.get("code") or [None])[0]
    if not code:
        raise RuntimeError(
            f"Nao achei o parametro `code` na URL colada: {pasted[:120]}"
        )
    return code


def run_auth_flow(config: MercadoLivreConfig) -> None:
    """Autoriza o app: abre a URL, recebe de volta a URL de callback colada."""
    client = MercadoLivre(config)
    state = secrets.token_urlsafe(16)
    verifier, challenge = new_pkce_pair()
    url = client.authorize_url(state, code_challenge=challenge)

    print("\n1) Abra esta URL e autorize o app:\n")
    print(f"   {url}\n")
    print("2) O navegador vai redirecionar para", config.redirect_uri)
    print("   A pagina VAI FALHAR ao carregar. Isso e esperado -- nao ha servidor ali.")
    print("   O que importa esta na barra de enderecos.\n")
    print("3) Copie a URL inteira da barra de enderecos e cole aqui.\n")

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - sem navegador (container), a URL acima basta
        pass

    pasted = input("URL de callback: ")
    code = parse_callback(pasted, state, expected_redirect_uri=config.redirect_uri)
    client.exchange_code(code, code_verifier=verifier)
    print("\nToken do Mercado Livre salvo. Ja pode rodar `collect`.")

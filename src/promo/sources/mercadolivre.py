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
import secrets
import urllib.parse
import webbrowser
from datetime import timedelta

import httpx

from ..config import MercadoLivreConfig
from ..db import connect, load_token, now, save_token
from ..models import Offer

AUTH_HOST = "https://auth.mercadolivre.com.br"
API_HOST = "https://api.mercadolibre.com"
PROVIDER = "mercadolivre"

# `offline_access` e o que faz o ML devolver refresh_token; sem ele o acesso
# morre com o access_token e o daemon para sozinho ate alguem reautorizar.
# `read` basta: o bot so consulta catalogo publico, nunca escreve na conta.
SCOPES = "offline_access read"


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
            raise RuntimeError("Token do ML expirou e nao ha refresh_token. Rode ml-auth de novo.")

        self._refresh(row["refresh_token"])
        with connect() as conn:
            return load_token(conn, PROVIDER)["access_token"]

    # ---------- Busca ----------

    def search(self, keyword: str, limit: int = 50) -> list[Offer]:
        response = self._client.get(
            f"{API_HOST}/sites/{self.config.site_id}/search",
            params={"q": keyword, "limit": min(limit, 50)},
            headers={"Authorization": f"Bearer {self.access_token()}"},
        )
        response.raise_for_status()
        return [self._to_offer(item) for item in response.json().get("results", [])]

    def _to_offer(self, item: dict) -> Offer:
        shipping = item.get("shipping") or {}
        return Offer(
            source=PROVIDER,
            external_id=item["id"],
            title=item["title"],
            price=float(item["price"]),
            original_price=(
                float(item["original_price"]) if item.get("original_price") else None
            ),
            url=item.get("permalink", ""),
            currency=item.get("currency_id", "BRL"),
            image_url=item.get("thumbnail"),
            category=item.get("category_id"),
            available=item.get("available_quantity", 0) > 0,
            free_shipping=bool(shipping.get("free_shipping")),
        )

    # ---------- Afiliado ----------

    def affiliate_url(self, offer: Offer) -> str:
        """Anexa os params do seu programa de afiliado ML.

        O ML entrega esses params no painel de Afiliados (ex.: matt_tool /
        matt_word). Deixe ML_AFFILIATE_PARAMS vazio pra postar link limpo.
        """
        if not self.config.affiliate_params:
            return offer.url
        separator = "&" if "?" in offer.url else "?"
        return f"{offer.url}{separator}{self.config.affiliate_params}"


def parse_callback(pasted: str, expected_state: str) -> str:
    """Extrai o `code` da URL de callback colada, validando o state.

    Aceita tanto a URL inteira quanto so a query string, porque e comum a
    pessoa colar so um pedaco.
    """
    pasted = pasted.strip()
    if not pasted:
        raise RuntimeError("Nada colado.")

    query = urllib.parse.urlparse(pasted).query or pasted.lstrip("?")
    params = urllib.parse.parse_qs(query)

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
        raise RuntimeError(f"Nao achei o parametro `code` na URL colada: {pasted[:120]}")
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
    client.exchange_code(parse_callback(pasted, state), code_verifier=verifier)
    print("\nToken do Mercado Livre salvo. Ja pode rodar `collect`.")

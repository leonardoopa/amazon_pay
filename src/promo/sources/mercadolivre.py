"""Mercado Livre.

O ML so aceita grant_type=authorization_code e refresh_token -- nao existe
client_credentials. Entao voce autoriza uma vez no navegador (`promo ml-auth`)
e a partir dai o refresh_token guardado no SQLite mantem o acesso vivo.
"""

from __future__ import annotations

import http.server
import secrets
import threading
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


class MercadoLivre:
    name = PROVIDER

    def __init__(self, config: MercadoLivreConfig) -> None:
        self.config = config
        self._client = httpx.Client(timeout=20.0)

    # ---------- OAuth ----------

    def authorize_url(self, state: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "state": state,
        }
        return f"{AUTH_HOST}/authorization?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> None:
        self._token_request(
            {
                "grant_type": "authorization_code",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "code": code,
                "redirect_uri": self.config.redirect_uri,
            }
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

    def _token_request(self, payload: dict[str, str]) -> None:
        response = self._client.post(
            f"{API_HOST}/oauth/token",
            data=payload,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
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

    def access_token(self) -> str:
        from datetime import datetime

        with connect() as conn:
            row = load_token(conn, PROVIDER)

        if row is None:
            raise RuntimeError(
                "Mercado Livre nao autorizado ainda. Rode: python -m promo ml-auth"
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


def run_auth_flow(config: MercadoLivreConfig) -> None:
    """Sobe um servidor local, abre o navegador e captura o ?code= do redirect."""
    client = MercadoLivre(config)
    state = secrets.token_urlsafe(16)
    parsed = urllib.parse.urlparse(config.redirect_uri)
    captured: dict[str, str] = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - assinatura da stdlib
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            captured.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in captured and captured.get("state") == state
            message = "Autorizado. Pode fechar esta aba." if ok else "Falhou. Veja o terminal."
            self.wfile.write(f"<h2>{message}</h2>".encode())
            done.set()

        def log_message(self, *args: object) -> None:
            pass  # silencia o log do servidor de uso unico

    server = http.server.HTTPServer((parsed.hostname or "localhost", parsed.port or 80), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = client.authorize_url(state)
    print(f"Abrindo o navegador para autorizar o app...\nSe nao abrir, acesse:\n{url}\n")
    webbrowser.open(url)

    if not done.wait(timeout=300):
        server.shutdown()
        raise TimeoutError("Timeout esperando a autorizacao do Mercado Livre.")
    server.shutdown()

    if captured.get("state") != state:
        raise RuntimeError("State divergente no callback -- possivel CSRF. Tente de novo.")
    if "code" not in captured:
        raise RuntimeError(f"Callback sem code: {captured}")

    client.exchange_code(captured["code"])
    print("Token do Mercado Livre salvo.")

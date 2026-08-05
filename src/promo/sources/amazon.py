"""Amazon Creators API.

Substituta da PA-API 5.0, aposentada em 15/05/2026. Diferencas: OAuth 2.0
client_credentials no lugar do AWS SigV4, e campos em lowerCamelCase.

Gate importante: a conta Associates precisa ter 3 vendas qualificadas em 180
dias pra liberar o acesso, e perde o acesso se ficar 30 dias sem venda. Por
isso o pipeline trata esta fonte como opcional e segue so com o ML se ela
nao estiver configurada.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx

from ..config import AmazonConfig
from ..db import connect, load_token, now, save_token
from ..models import Offer

API_BASE = "https://creatorsapi.amazon/catalog/v1"
PROVIDER = "amazon"
SCOPE = "creatorsapi::default"

RESOURCES = [
    "itemInfo.title",
    "itemInfo.features",
    "images.primary.large",
    "offers.listings.price",
    "offers.listings.savingBasis",
    "offers.listings.availability.message",
]


class Amazon:
    name = PROVIDER

    def __init__(self, config: AmazonConfig) -> None:
        self.config = config
        self._client = httpx.Client(timeout=20.0)

    def access_token(self) -> str:
        with connect() as conn:
            row = load_token(conn, PROVIDER)
        if row and datetime.fromisoformat(row["expires_at"]) > now():
            return row["access_token"]

        response = self._client.post(
            self.config.token_endpoint,
            json={
                "grant_type": "client_credentials",
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "scope": SCOPE,
            },
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        expires_at = now() + timedelta(seconds=int(data.get("expires_in", 3600)) - 60)
        with connect() as conn:
            save_token(conn, PROVIDER, data["access_token"], None, expires_at)
        return data["access_token"]

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token()}",
            "Content-Type": "application/json",
            "x-marketplace": self.config.marketplace,
        }

    def search(self, keyword: str, limit: int = 10) -> list[Offer]:
        response = self._client.post(
            f"{API_BASE}/searchItems",
            headers=self._headers(),
            json={
                "keywords": keyword,
                "partnerTag": self.config.partner_tag,
                "marketplace": self.config.marketplace,
                "itemCount": min(limit, 10),  # a API limita a 10 por pagina
                "resources": RESOURCES,
            },
        )
        response.raise_for_status()
        items = response.json().get("searchResult", {}).get("items", [])
        offers = [self._to_offer(item) for item in items]
        return [offer for offer in offers if offer is not None]

    def _to_offer(self, item: dict) -> Offer | None:
        listings = (item.get("offers") or {}).get("listings") or []
        if not listings:
            return None
        listing = listings[0]

        price = (listing.get("price") or {}).get("amount")
        if price is None:
            return None
        saving_basis = (listing.get("savingBasis") or {}).get("amount")
        availability = ((listing.get("availability") or {}).get("message") or "").lower()

        info = item.get("itemInfo") or {}
        title = ((info.get("title") or {}).get("displayValue")) or item.get("asin", "")
        image = (((item.get("images") or {}).get("primary") or {}).get("large") or {}).get("url")

        return Offer(
            source=PROVIDER,
            external_id=item["asin"],
            title=title,
            price=float(price),
            original_price=float(saving_basis) if saving_basis else None,
            url=item.get("detailPageURL", ""),
            currency=(listing.get("price") or {}).get("currency", "BRL"),
            image_url=image,
            available="indisponí" not in availability and "unavailable" not in availability,
        )

    def affiliate_url(self, offer: Offer) -> str:
        """A Creators API ja devolve detailPageURL com a partnerTag embutida."""
        if not offer.url:
            return f"https://{self.config.marketplace}/dp/{offer.external_id}?tag={self.config.partner_tag}"
        if "tag=" in offer.url:
            return offer.url
        separator = "&" if "?" in offer.url else "?"
        return f"{offer.url}{separator}tag={self.config.partner_tag}"

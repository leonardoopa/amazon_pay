"""Contrato comum das lojas, pra o pipeline nao saber de qual API veio a oferta."""

from __future__ import annotations

from typing import Protocol

from ..models import Offer


class Source(Protocol):
    name: str

    def search(self, keyword: str, limit: int = 50) -> list[Offer]:
        """Busca ofertas ativas para um termo."""
        ...

    def affiliate_url(self, offer: Offer) -> str:
        """Aplica a tag de afiliado na URL do produto."""
        ...

    # Opcional, fora do Protocol de proposito: `fetch_by_ids(ids) -> list[Offer]`,
    # que reconsulta produtos ja conhecidos sem passar pela busca. Quem
    # implementa mantem o historico avancando mesmo quando o produto sai do
    # ranking; o pipeline detecta por getattr e simplesmente pula quem nao tem.

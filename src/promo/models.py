"""Formato unico de oferta, independente da loja de origem."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Offer:
    """Uma observacao de preco de um produto num instante."""

    source: str  # "mercadolivre" | "amazon"
    external_id: str  # MLB1234567890 / B0XXXXXXXX
    title: str
    price: float
    url: str
    currency: str = "BRL"
    original_price: float | None = None  # preco "de" anunciado pela loja
    image_url: str | None = None
    category: str | None = None
    available: bool = True
    free_shipping: bool = False

    @property
    def product_id(self) -> str:
        return f"{self.source}:{self.external_id}"


@dataclass(frozen=True)
class ScoredOffer:
    """Oferta que passou no filtro de desconto real."""

    offer: Offer
    baseline: float  # mediana do menor preco diario na janela
    discount_pct: float  # desconto contra a baseline, nao contra o "de" da loja
    observations: int  # dias distintos de historico usados
    lowest_ever: bool

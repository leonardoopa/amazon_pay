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
    # Anuncio de loja oficial no ML. Vale citar no post: e sinal de confianca,
    # e o anuncio mais barato quase nunca e de loja oficial.
    official_store: bool = False

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
    # True  = a queda foi medida contra o NOSSO historico de precos.
    # False = e repasse da vitrine do ML, e o "de" e o preco riscado da loja.
    #
    # A diferenca nao e tecnica, e editorial: no primeiro caso o post pode
    # afirmar que o preco caiu de verdade, porque alguem mediu. No segundo ele
    # so pode repetir o que a loja alega -- e a loja infla o riscado na vespera.
    verified: bool = True

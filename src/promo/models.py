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
    # True = nao ha desconto NENHUM a anunciar, nem medido nem riscado.
    #
    # E o caso da pista vinda de outro grupo: o produto e novo no nosso radar,
    # entao nao ha baseline, e o anuncio pode nao ter `original_price`. O que
    # justifica o post e a curadoria de quem achou, nao um numero.
    #
    # Quando isto e True, `baseline` vale o proprio preco e `discount_pct` e
    # zero -- e o texto nao pode escrever "De X por Y" nem percentual algum,
    # porque nao ha "de". Ver `score_pista`.
    sem_medicao: bool = False

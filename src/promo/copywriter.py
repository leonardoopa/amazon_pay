"""Gera o texto do post com o Claude.

Regras que importam: nada de preco inventado, nada de urgencia falsa, e a
divulgacao de afiliado e obrigatoria pelos dois programas.
"""

from __future__ import annotations

from anthropic import Anthropic

from .config import copy_model
from .models import ScoredOffer

SYSTEM = """Voce escreve posts curtos de oferta para um grupo de WhatsApp brasileiro.

Tom: direto, animado sem ser forcado, como um amigo que achou um bom preco.
Portugues do Brasil, informal.

Formato (siga exatamente):
- 1 linha de gancho com no maximo 1 emoji
- nome do produto (pode encurtar nomes gigantes de marketplace, sem mudar o sentido)
- preco atual e a comparacao com a media historica
- se for o menor preco ja registrado, diga isso
- o link, sozinho numa linha
- ultima linha: "Link de afiliado - o preco pra voce nao muda."

Regras rigidas:
- Use SOMENTE os numeros que eu passar. Nunca invente preco, desconto ou prazo.
- Nao escreva "ultimas unidades", "so hoje" ou qualquer urgencia que eu nao tenha informado.
- Nao prometa qualidade do produto: voce nao testou.
- Maximo 6 linhas. Sem markdown, sem asteriscos de titulo.
- Formatacao do WhatsApp: *negrito* so no preco.

Responda apenas com o texto do post, nada mais."""


class Copywriter:
    def __init__(self, client: Anthropic | None = None) -> None:
        self._client = client or Anthropic()
        self._model = copy_model()

    def write(self, scored: ScoredOffer, link: str) -> str:
        offer = scored.offer
        facts = [
            f"Produto: {offer.title}",
            f"Preco agora: R$ {offer.price:.2f}",
            f"Media historica ({scored.observations} dias): R$ {scored.baseline:.2f}",
            f"Desconto contra a media: {scored.discount_pct:.0f}%",
            f"Loja: {'Mercado Livre' if offer.source == 'mercadolivre' else 'Amazon'}",
            f"Link: {link}",
        ]
        if scored.lowest_ever:
            facts.append("Este e o menor preco desde que comecamos a monitorar.")
        if offer.free_shipping:
            facts.append("Frete gratis.")

        response = self._client.messages.create(
            model=self._model,
            max_tokens=400,
            system=SYSTEM,
            messages=[{"role": "user", "content": "\n".join(facts)}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()


def fallback_copy(scored: ScoredOffer, link: str) -> str:
    """Texto sem IA, usado se a chamada ao Claude falhar."""
    offer = scored.offer
    lines = [
        "Achei um bom preco 👇",
        offer.title,
        f"*R$ {offer.price:.2f}* — {scored.discount_pct:.0f}% abaixo da media de R$ {scored.baseline:.2f}",
    ]
    if scored.lowest_ever:
        lines.append("Menor preco desde que comecamos a monitorar.")
    lines.append(link)
    lines.append("Link de afiliado - o preco pra voce nao muda.")
    return "\n".join(lines)

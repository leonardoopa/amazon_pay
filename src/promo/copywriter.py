"""Gera o texto do post com a Gemini API.

Regras que importam: nada de preco inventado, nada de urgencia falsa, e a
divulgacao de afiliado e obrigatoria pelos dois programas de afiliados.
"""

from __future__ import annotations

import re

from google import genai
from google.genai import types

from .config import copy_model, gemini_api_key
from .models import ScoredOffer

# Texto de divulgacao exigido por cada programa.
#
# O da Amazon e literal: a Clausula 5 do Contrato Operacional obriga esta frase
# exata, e a Clausula 6 classifica qualquer violacao da 5 como descumprimento
# material -- ou seja, motivo de encerramento da conta. Nao parafraseie.
# Folga sobre as ~6 linhas do post, pra truncamento so acontecer se algo
# estiver realmente errado.
MAX_OUTPUT_TOKENS = 800

DISCLOSURES = {
    "amazon": (
        "Como participante do Programa de Associados da Amazon, "
        "sou remunerado pelas compras qualificadas efetuadas"
    ),
    "mercadolivre": "Link de afiliado - o preco pra voce nao muda.",
}

SYSTEM = """Voce escreve posts curtos de oferta para um grupo de WhatsApp brasileiro.

Tom: direto, animado sem ser forcado, como um amigo que achou um bom preco.
Portugues do Brasil, informal.

Formato (siga exatamente):
- 1 linha de gancho com no maximo 1 emoji
- nome do produto (pode encurtar nomes gigantes de marketplace, sem mudar o sentido)
- preco atual e a comparacao com a media historica
- se for o menor preco ja registrado, diga isso
- o link, sozinho numa linha
- ultima linha: exatamente o texto que eu informar em "Divulgacao obrigatoria"

Regras rigidas:
- Use SOMENTE os numeros que eu passar. Nunca invente preco, desconto ou prazo.
- Nao escreva "ultimas unidades", "so hoje" ou qualquer urgencia que eu nao tenha informado.
- Nao prometa qualidade do produto: voce nao testou.
- A linha de divulgacao obrigatoria deve ser copiada CARACTERE POR CARACTERE.
  Nao reescreva, nao traduza, nao encurte, nao adicione emoji nela.
- Maximo 6 linhas. Sem markdown, sem asteriscos de titulo.
- Formatacao do WhatsApp: *negrito* so no preco.

Responda apenas com o texto do post, nada mais."""


class Copywriter:
    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client or genai.Client(api_key=gemini_api_key())
        self._model = copy_model()

    def write(self, scored: ScoredOffer, link: str) -> str:
        response = self._client.models.generate_content(
            model=self._model,
            contents=_facts(scored, link),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                temperature=0.8,
                # Os modelos "thinking" gastam o orcamento de saida raciocinando
                # antes de escrever, e o post sai cortado no meio. Escrever 5
                # linhas de oferta nao precisa disso.
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

        _reject_truncated(response)

        text = (response.text or "").strip()
        if not text:
            # Acontece quando o filtro de seguranca corta a resposta inteira.
            raise RuntimeError("Gemini devolveu resposta vazia")

        _reject_unfounded_claims(text, scored)
        return _enforce_disclosure(text, scored.offer.source)


def _reject_truncated(response) -> None:
    """Nao deixa post cortado no meio chegar ao grupo.

    Sem isso, um estouro de max_output_tokens vira uma mensagem sem preco e
    sem link -- que e exatamente o formato de um post inutil.
    """
    for candidate in getattr(response, "candidates", None) or []:
        reason = getattr(candidate, "finish_reason", None)
        if reason is not None and getattr(reason, "name", str(reason)) == "MAX_TOKENS":
            raise RuntimeError(
                f"Gemini truncou a resposta (max_output_tokens={MAX_OUTPUT_TOKENS}). "
                "Aumente o limite ou verifique se o modelo gasta tokens de thinking."
            )


def brl(value: float) -> str:
    """Formata em real brasileiro: 1234.5 -> "1.234,50".

    O modelo copia os numeros do prompt caractere por caractere -- e ainda bem,
    e o que impede ele de inventar preco. Mas isso significa que um "27.00" no
    prompt vira "R$ 27.00" no post, formato americano, num grupo brasileiro.
    A formatacao tem que estar certa aqui, nao no prompt.
    """
    inteiro, _, centavos = f"{value:,.2f}".partition(".")
    return f"{inteiro.replace(',', '.')},{centavos}"


# Afirmacoes que o post so pode fazer se o dado sustentar.
#
# "menor preco ja registrado" e verificavel: ou o preco de hoje empata com o
# minimo do historico, ou nao. Quando nao empata e o post afirma mesmo assim,
# isso e informacao falsa saindo pro grupo com um link de afiliado do lado --
# que e a combinacao que derruba a confianca do grupo e, no limite, a conta no
# programa.
MENOR_PRECO = re.compile(
    r"(menor|melhor)\s+pre[çc]o|pre[çc]o\s+mais\s+baixo|nunca\s+esteve\s+t[aã]o",
    re.IGNORECASE,
)

# O prompt ja proibe, mas urgencia falsa e o tipo de coisa que o modelo produz
# sozinho porque "soa como anuncio". Nenhuma dessas informacoes existe no
# nosso dado: nao sabemos estoque nem prazo de promocao.
URGENCIA_FALSA = re.compile(
    r"[uú]ltimas?\s+unidades?|s[oó]\s+hoje|corre\s+que\s+acaba|por\s+tempo\s+limitado"
    r"|acaba\s+hoje|[uú]ltima\s+chance|estoque\s+limitado",
    re.IGNORECASE,
)


def _reject_unfounded_claims(text: str, scored: ScoredOffer) -> None:
    """Barra post que afirma o que o dado nao sustenta.

    Pedir no system prompt nao basta -- um modelo de temperatura 0.8 escrevendo
    copy de oferta puxa pro superlativo sozinho. Observado na pratica: com
    lowest_ever=False, e sem o fato no prompt, o post saiu com "(menor preco ja
    registrado!)".

    Levanta RuntimeError de proposito: quem chama ja trata falha do Gemini
    caindo pro `fallback_copy`, que e deterministico. Post sem graca e melhor
    que post mentiroso.
    """
    if not scored.lowest_ever and MENOR_PRECO.search(text):
        raise RuntimeError(
            "O texto afirma ser o menor preco, mas lowest_ever e False. "
            "Descartado pra nao publicar afirmacao falsa."
        )

    achado = URGENCIA_FALSA.search(text)
    if achado:
        raise RuntimeError(
            f"O texto inventou urgencia ({achado.group(0)!r}); nao temos esse dado."
        )


def _facts(scored: ScoredOffer, link: str) -> str:
    offer = scored.offer
    facts = [
        f"Produto: {offer.title}",
        f"Preco agora: R$ {brl(offer.price)}",
        f"Media historica ({scored.observations} dias): R$ {brl(scored.baseline)}",
        f"Desconto contra a media: {scored.discount_pct:.0f}%",
        f"Loja: {'Mercado Livre' if offer.source == 'mercadolivre' else 'Amazon'}",
        f"Link: {link}",
    ]
    if scored.lowest_ever:
        facts.append("Este e o menor preco desde que comecamos a monitorar.")
    if offer.free_shipping:
        facts.append("Frete gratis.")
    facts.append(f"Divulgacao obrigatoria (copie literalmente): {disclosure_for(offer.source)}")
    return "\n".join(facts)


def disclosure_for(source: str) -> str:
    return DISCLOSURES.get(source, DISCLOSURES["mercadolivre"])


def _enforce_disclosure(text: str, source: str) -> str:
    """Garante a linha de divulgacao mesmo se o modelo tiver reescrito ela.

    Pedir no prompt nao basta: um LLM pode parafrasear, e no caso da Amazon a
    frase e contratualmente literal. Aqui a conformidade fica deterministica.
    """
    disclosure = disclosure_for(source)
    if disclosure in text:
        return text
    return f"{text}\n{disclosure}"


def fallback_copy(scored: ScoredOffer, link: str) -> str:
    """Texto sem IA, usado se a chamada ao Gemini falhar."""
    offer = scored.offer
    lines = [
        "Achei um bom preco",
        offer.title,
        f"*R$ {brl(offer.price)}* — {scored.discount_pct:.0f}% abaixo da media de R$ {brl(scored.baseline)}",
    ]
    if scored.lowest_ever:
        lines.append("Menor preco desde que comecamos a monitorar.")
    lines.append(link)
    lines.append(disclosure_for(offer.source))
    return "\n".join(lines)

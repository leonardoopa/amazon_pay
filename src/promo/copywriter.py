"""Gera o texto do post com a Gemini API.

Regras que importam: nada de preco inventado, nada de urgencia falsa, e a
divulgacao de afiliado e obrigatoria pelos dois programas de afiliados.
"""

from __future__ import annotations

import logging
import re
import time

from google import genai
from google.genai import types

from .config import copy_model, gemini_api_key
from .models import ScoredOffer

log = logging.getLogger("promo")

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

SYSTEM = """Voce escreve posts de oferta para um grupo de WhatsApp brasileiro.

O post tem que fazer a pessoa QUERER o produto antes de olhar o preco. Quem le
esta rolando o feed: se a primeira linha nao segurar, o resto nao existe.

Tom: brasileiro, informal, bem-humorado. Como um amigo que achou o preco e
avisa o grupo. Pode ser engracado, mas nunca forcado nem apelativo.

Estrutura do post, linha a linha. NUNCA copie os rotulos abaixo para o texto:
eles descrevem o que escrever, nao sao o que escrever.

  1. chamada em CAIXA ALTA
  2. vazia
  3. nome do produto
  4. vazia
  5. o preco, no formato: De R$ <media> por *R$ <preco>*
  6. vazia
  7. a URL, sozinha
  8. vazia
  9. a divulgacao obrigatoria

Tres linhas opcionais, que voce SO escreve quando eu mandar explicitamente:
- cupom: entra logo depois do preco, como "Use o cupom: CODIGO 🎟️"
- loja oficial: entra logo antes da URL, como "Loja oficial no ML"
- selo de acompanhamento: entra logo depois do preco, so quando eu disser que
  ACOMPANHAMOS o produto. Escreva com suas palavras, em 1 linha, usando os dias
  e o preco medio que eu passar. Exemplos do tom:
    "📊 Acompanhamos ha 12 dias: nunca vimos tao barato"
    "📊 12 dias de olho nesse preco, e hoje e o fundo do poco"
  Esse selo e o que diferencia o grupo: significa que alguem mediu o preco ao
  longo do tempo em vez de repetir o desconto que a loja alega.

Quando eu disser que NAO ha cupom, que NAO e loja oficial, ou que NAO
acompanhamos o produto, a linha correspondente simplesmente nao existe no post. Nao invente, nao adapte, nao
escreva variacao ("loja verificada", "vendedor oficial"). Loja oficial e um
selo do Mercado Livre, nao um adjetivo.

Sobre a linha de chamada -- e a linha que decide se o post e lido:
- CAIXA ALTA, curta, no maximo 1 emoji.
- Pode usar giria ("conto", "pila", "sai correndo", "toma"). Nao invente
  numero: se citar preco, use exatamente o que eu passei, podendo arredondar
  pra baixo ao real inteiro (R$ 27,00 pode virar "27 CONTO").

Varie o ANGULO da chamada. Escolha o que combina com o produto, e nao repita o
mesmo tipo duas vezes seguidas:
- preco como espanto: "37 CONTO DA POLO DA HERING"
- para quem serve: "O TRIO PERFEITO PRO SEU ROSTO"
- a dor que resolve: "CHEGA DE FRITAR NO OLEO"
- comparacao do dia a dia: "MAIS BARATO QUE O TEU IFOOD DE ONTEM"
- a pergunta incredula: "QUEM AUTORIZOU ESSE PRECO?"
- o caso de uso concreto: "PRO CAFE DA MANHA EM 5 MINUTOS"
- conselho de amigo: "COMPRA LOGO QUE EU JA COMPREI"

Nao comece toda chamada com o nome da categoria do produto. "AIR FRYER POR X"
seguido de "AIR FRYER POR Y" e o erro mais comum e o mais chato de ler.

Regras rigidas:
- Use SOMENTE os numeros que eu passar. Nunca invente preco, desconto, cupom,
  prazo, parcela ou forma de pagamento.
- Nao escreva "ultimas unidades", "so hoje", "corre que acaba" nem qualquer
  urgencia que eu nao tenha informado. Escassez inventada e mentira.
- Nao prometa qualidade nem resultado: voce nao testou o produto.
- Nao cite loja oficial se eu nao informar.
- Quando eu disser que NAO acompanhamos o produto, nao escreva nada que sugira
  medicao nossa ("acompanhamos", "monitoramos", "menor preco que ja vimos",
  "de olho ha dias"). Nesse caso o desconto e o que a loja alega, e so.
- A divulgacao obrigatoria vai copiada CARACTERE POR CARACTERE. Nao reescreva,
  nao traduza, nao encurte, nao adicione emoji nela.
- Formatacao do WhatsApp: *negrito* so no preco final.
- Sem markdown de titulo, sem asterisco fora do preco.

Responda apenas com o texto do post, nada mais."""


class Copywriter:
    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client or genai.Client(api_key=gemini_api_key())
        self._model = copy_model()

    def write(
        self,
        scored: ScoredOffer,
        link: str,
        coupon: str | None = None,
        avoid: list[str] | None = None,
    ) -> str:
        response = self._generate(_facts(scored, link, coupon, avoid))

        _reject_truncated(response)

        text = (response.text or "").strip()
        if not text:
            # Acontece quando o filtro de seguranca corta a resposta inteira.
            raise RuntimeError("Gemini devolveu resposta vazia")

        _reject_unfounded_claims(text, scored)
        return _enforce_disclosure(text, scored.offer.source)

    def _generate(self, contents: str, tentativas: int = 3):
        """Chama o Gemini, respeitando o 429 de cota.

        O free tier corta em 20 requisicoes por dia por modelo, e o erro traz
        `retryDelay` com quantos segundos esperar. Sem esse retry, um pico de
        rajada derruba o post pro `fallback_copy` -- que sai sem linha de
        chamada, ou seja, exatamente o post sem graca que o grupo ignora.

        Nao resolve cota esgotada: se o dia acabou, acabou. Resolve o limite
        por minuto, que e o que aparece numa rodada com varias ofertas.
        """
        for tentativa in range(tentativas):
            try:
                return self._raw_generate(contents)
            except Exception as exc:  # noqa: BLE001 - a lib nao expoe tipo estavel
                espera = _retry_delay(exc)
                if espera is None or tentativa == tentativas - 1:
                    raise
                log.warning(
                    "Gemini pediu espera de %.0fs (tentativa %d/%d).",
                    espera,
                    tentativa + 1,
                    tentativas,
                )
                time.sleep(espera)
        raise RuntimeError("inalcancavel")

    def _raw_generate(self, contents: str):
        return self._client.models.generate_content(
            model=self._model,
            contents=contents,
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


# O 429 do Gemini traz quanto esperar, em 'retryDelay': '12s'. Ler isso e melhor
# que backoff cego: o servidor sabe quando a janela reabre.
RETRY_DELAY = re.compile(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'")

# Teto de seguranca: cota diaria estourada devolve delays enormes, e travar a
# rodada esperando por eles seria pior que cair pro texto padrao.
MAX_RETRY_WAIT = 30.0


def _retry_delay(exc: Exception) -> float | None:
    """Segundos a esperar, ou None se o erro nao for de cota."""
    texto = str(exc)
    if "RESOURCE_EXHAUSTED" not in texto and "429" not in texto:
        return None
    achado = RETRY_DELAY.search(texto)
    if not achado:
        return None
    espera = float(achado.group(1)) + 1  # folga: o servidor conta o segundo em curso
    return espera if espera <= MAX_RETRY_WAIT else None


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


# "Loja oficial" e selo do ML, nao adjetivo. Observado na pratica: com
# official_store=False e sem o fato no prompt, o post saiu com "Loja oficial no
# ML" mesmo assim -- o modelo preenche o formato que aprendeu. Atribuir selo
# que o vendedor nao tem engana o grupo e e reclamacao direta pro programa.
LOJA_OFICIAL = re.compile(r"loja\s+oficial", re.IGNORECASE)

# Instrucao do proprio prompt que vazou pro texto. Barato de checar e obvio
# quando acontece -- e um post com "o link sozinho numa linha" no meio destroi
# a credibilidade do grupo de uma vez.
VAZOU_PROMPT = re.compile(
    r"linha\s+de\s+chamada|caixa\s+alta|em\s+branco|sozinh[ao]\s+numa\s+linha"
    r"|divulgacao\s+obrigatoria|<[a-z_]+>",
    re.IGNORECASE,
)


# Linguagem que so o post verificado pode usar. Repassar oferta da vitrine
# dizendo "acompanhamos ha dias" e mentira sobre o proprio metodo -- e o metodo
# e o unico ativo que o grupo tem contra os que so espelham campanha.
# So primeira pessoa do plural. A versao anterior batia em "monitor" e barrava
# todo post de monitor -- o produto -- e "acompanha" pegava "acompanha 2
# baterias" na descricao. Guarda que rejeita post legitimo custa oferta.
ACOMPANHAMENTO = re.compile(
    r"acompanhamos|monitoramos|estamos de olho|venho acompanhando"
    r"|(j[aá]|nunca) vimos|nossa m[eé]dia|de olho h[aá] \d+",
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

    if not scored.offer.official_store and LOJA_OFICIAL.search(text):
        raise RuntimeError(
            "O texto diz 'loja oficial', mas o anuncio nao e de loja oficial."
        )

    if not scored.verified:
        achado = ACOMPANHAMENTO.search(text)
        if achado:
            raise RuntimeError(
                f"O texto sugere acompanhamento ({achado.group(0)!r}), mas esta "
                "oferta e repasse da vitrine do ML -- nao medimos nada nela."
            )

    achado = VAZOU_PROMPT.search(text)
    if achado:
        raise RuntimeError(
            f"Instrucao do prompt vazou pro post ({achado.group(0)!r})."
        )


def _facts(
    scored: ScoredOffer,
    link: str,
    coupon: str | None = None,
    avoid: list[str] | None = None,
) -> str:
    offer = scored.offer
    facts = [
        f"Produto: {offer.title}",
        # O "De" e a nossa mediana, nao o preco riscado da loja -- esse vem
        # nulo na maioria dos anuncios, e quando vem e o numero inflado na
        # vespera que o projeto inteiro existe pra ignorar.
        f"De (media de {scored.observations} dias): R$ {brl(scored.baseline)}",
        f"Por (preco agora): R$ {brl(offer.price)}",
        f"Desconto contra a media: {scored.discount_pct:.0f}%",
        f"Loja: {'Mercado Livre' if offer.source == 'mercadolivre' else 'Amazon'}",
        f"Link: {link}",
    ]
    if scored.lowest_ever:
        facts.append("Este e o menor preco desde que comecamos a monitorar.")
    if offer.free_shipping:
        facts.append("Frete gratis.")
    # Sempre dizer o estado das duas linhas opcionais, inclusive quando e
    # "nao". Deixar o assunto de fora nao equivale a proibir: o modelo preenche
    # o formato que aprendeu, e escreve "Loja oficial no ML" sozinho -- foi o
    # que aconteceu em 3 de 3 posts antes desta instrucao existir.
    facts.append(
        f"Cupom: {coupon}" if coupon else "Cupom: NAO ha. NAO escreva linha de cupom."
    )
    facts.append(
        "Loja oficial: SIM, e anuncio de loja oficial no ML."
        if offer.official_store
        else "Loja oficial: NAO. NAO escreva 'loja oficial' nem variacao disso."
    )
    if scored.verified:
        facts.append(
            f"Acompanhamos: SIM, ha {scored.observations} dias. O 'De' acima e a "
            "media que NOS medimos nesse periodo, nao o preco riscado da loja. "
            "Escreva o selo de acompanhamento."
        )
    else:
        facts.append(
            "Acompanhamos: NAO. O 'De' acima e o preco riscado pela propria loja. "
            "NAO escreva selo de acompanhamento nem sugira medicao nossa."
        )
    if avoid:
        # O modelo nao tem memoria entre chamadas: sem isso ele reencontra a
        # mesma piada boa toda vez, e o grupo le a mesma formula o dia inteiro.
        facts.append(
            "Chamadas ja usadas nos posts recentes -- NAO repita a formula nem "
            "o angulo delas:\n" + "\n".join(f"  - {linha}" for linha in avoid)
        )
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


def fallback_copy(
    scored: ScoredOffer, link: str, coupon: str | None = None
) -> str:
    """Texto sem IA, usado quando o Gemini falha ou inventa.

    Segue o mesmo formato do prompt, menos a linha de chamada -- que e
    justamente a parte que precisa de criatividade e que nao da pra fabricar
    com template sem soar automatica. Post sem gracinha e melhor que oferta
    perdida, e melhor ainda que post mentiroso.
    """
    offer = scored.offer
    lines = [
        offer.title,
        "",
        f"De R$ {brl(scored.baseline)} por *R$ {brl(offer.price)}*",
    ]
    if coupon:
        lines.append(f"Use o cupom: {coupon} 🎟️")
    if scored.lowest_ever:
        lines.append("Menor preco desde que comecamos a monitorar.")
    lines.append("")
    if offer.official_store:
        lines.append("Loja oficial no ML")
    lines.append(link)
    lines.append("")
    lines.append(disclosure_for(offer.source))
    return "\n".join(lines)

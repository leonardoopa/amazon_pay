"""Gera o texto do post com a Gemini API.

Regras que importam: nada de preco inventado, nada de urgencia falsa, e a
divulgacao de afiliado e obrigatoria pelos dois programas de afiliados.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata

from google import genai
from google.genai import types

from .config import copy_model, gemini_api_key, gemini_min_interval_seconds
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

# Nome da loja como o post deve cita-la, por fonte.
#
# Era um ternario binario (`'Mercado Livre' if source == 'mercadolivre' else
# 'Amazon'`), escrito quando so existiam duas fontes. Quando `ml_ofertas` e
# `demo` entraram, os dois cairam no else: 100% dos posts da vitrine iam pro
# Gemini com "Loja: Amazon" ao lado de um link meli.la e da divulgacao do ML.
# Nenhuma guarda checa nome de loja, entao sairia inteiro pro grupo.
LOJAS = {
    "mercadolivre": "Mercado Livre",
    "ml_ofertas": "Mercado Livre",
    "amazon": "Amazon",
    "demo": "Mercado Livre",
}


def store_name(source: str) -> str:
    """Nome da loja. Fonte desconhecida falha alto em vez de virar Amazon."""
    try:
        return LOJAS[source]
    except KeyError:
        raise RuntimeError(
            f"Fonte {source!r} sem nome de loja em LOJAS. Cadastre antes de postar "
            "-- sem isso o post sairia atribuindo a oferta a loja errada."
        ) from None


DISCLOSURES = {
    "amazon": (
        "Como participante do Programa de Associados da Amazon, "
        "sou remunerado pelas compras qualificadas efetuadas"
    ),
    # Encurtada de "Link de afiliado - o preco pra voce nao muda." a pedido:
    # ocupava tres linhas no celular e quase ninguem lia a segunda metade.
    #
    # O que NAO pode sair e a identificacao em si. Publicidade tem que ser
    # reconhecivel como tal (CDC art. 36), e o programa de afiliados do ML
    # exige a divulgacao -- some ela e o risco nao e um post feio, e a conta
    # encerrada, que leva junto a unica receita do projeto.
    "mercadolivre": "Link de afiliado.",
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
  3. nome do produto, EXATAMENTE como eu passar
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

Varie o ANGULO da chamada. Escolha o que combina com o produto:
- o preco como espanto
- para quem o produto serve
- a dor que ele resolve
- comparacao com um gasto do dia a dia
- a pergunta incredula
- o caso de uso concreto
- conselho de amigo

Os exemplos abaixo mostram o TOM. Sao PROIBIDOS no texto final -- escreva os
seus, sobre ESTE produto:
  "37 CONTO DA POLO DA HERING" / "O TRIO PERFEITO PRO SEU ROSTO"
  "CHEGA DE FRITAR NO OLEO" / "MAIS BARATO QUE O TEU IFOOD DE ONTEM"
  "QUEM AUTORIZOU ESSE PRECO?" / "COMPRA LOGO QUE EU JA COMPREI"

Nao comece toda chamada com o nome da categoria do produto. "AIR FRYER POR X"
seguido de "AIR FRYER POR Y" e o erro mais comum e o mais chato de ler.

Sobre o nome do produto: copie o titulo que eu passar, INTEIRO e sem mexer.
Nao encurte, nao resuma, nao troque palavra, nao reordene, nao corrija
maiuscula. Titulo de marketplace e comprido mesmo ("Smartphone Motorola Moto
G17 4g - 128gb 4gb Ram + 8gb Ram Boost, Camera 50mp Sony Lytia 600, Tela Fhd+
60hz, Bateria 5200 Mah - Roxo") e e assim que ele deve sair: e por esse nome
que a pessoa confere se o produto e o mesmo ao abrir o link.

Regras rigidas:
- Use SOMENTE os numeros que eu passar. Nunca invente preco, desconto, cupom,
  prazo, parcela ou forma de pagamento.
- Nao escreva "ultimas unidades", "so hoje", "corre que acaba" nem qualquer
  urgencia que eu nao tenha informado. Escassez inventada e mentira.
- Nao prometa qualidade nem resultado: voce nao testou o produto.
- O nome do produto sai identico ao que eu passei. Encurtar e proibido.
- Nao cite loja oficial se eu nao informar.
- Quando eu disser que NAO acompanhamos o produto, nao escreva nada que sugira
  medicao nossa ("acompanhamos", "monitoramos", "menor preco que ja vimos",
  "de olho ha dias"). Nesse caso o desconto e o que a loja alega, e so.
- A divulgacao obrigatoria vai copiada CARACTERE POR CARACTERE. Nao reescreva,
  nao traduza, nao encurte, nao adicione emoji nela.
- Formatacao do WhatsApp: *negrito* so no preco final.
- Sem markdown de titulo, sem asterisco fora do preco.

Responda apenas com o texto do post, nada mais."""


def _chave_da_chamada(texto: str) -> str:
    """Primeira linha reduzida ao que o leitor reconhece como "a mesma frase".

    Compara sem acento, sem caixa, sem emoji e sem pontuacao: no grupo,
    "MAIS BARATO QUE UM LANCHE DE PADARIA 🦷" e a mesma piada que
    "mais barato que um lanche de padaria!" -- trocar o enfeite nao torna a
    chamada nova para quem le.
    """
    primeira = texto.strip().splitlines()[0] if texto.strip() else ""
    sem_acento = unicodedata.normalize("NFKD", primeira)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    so_texto = re.sub(r"[^a-z0-9 ]+", " ", sem_acento.casefold())
    return re.sub(r"\s+", " ", so_texto).strip()


class Copywriter:
    def __init__(self, client: genai.Client | None = None) -> None:
        self._client = client or genai.Client(api_key=gemini_api_key())
        self._model = copy_model()
        # None, nao 0.0: monotonic() pode devolver 0.0 e um float falsy
        # desligaria o limitador em silencio na segunda chamada.
        self._ultima_chamada: float | None = None
        # Descoberto na primeira chamada: alguns modelos recusam thinking_config.
        self._sem_thinking = False

    def write(
        self,
        scored: ScoredOffer,
        link: str,
        coupon: str | None = None,
        avoid: list[str] | None = None,
        preco_com_cupom: float | None = None,
    ) -> str:
        """Escreve o post, recusando chamada que ja saiu no grupo.

        O prompt sempre listou as chamadas recentes pedindo para nao repetir, e
        mesmo assim elas voltavam -- medido no banco: seis repeticoes, todas a
        2 a 6 posts de distancia, dentro da mesma rodada e portanto dentro da
        lista que o modelo tinha em maos. Pedir nao basta.

        Este arquivo ja trata o resto da saida do Gemini assim: `_reject_*` e
        `_enforce_*` conferem o que voltou em vez de confiar. A chamada
        repetida era a unica regra do prompt sem essa contrapartida.

        Duas tentativas, porque a segunda amostra do modelo quase sempre difere.
        Se ainda repetir, levanta -- e o `deliver()` cai no `fallback_copy`, que
        abre com o titulo do produto e por construcao nunca repete. Post sem
        gracinha e melhor do que o grupo ler a mesma frase duas vezes.
        """
        proibidas = {_chave_da_chamada(linha) for linha in (avoid or [])}
        proibidas.discard("")

        for tentativa in range(2):
            text = self._escrever_uma_vez(scored, link, coupon, avoid, preco_com_cupom)
            if _chave_da_chamada(text) not in proibidas:
                return text
            log.info(
                "Gemini repetiu a chamada %r (tentativa %d/2).",
                text.strip().splitlines()[0].strip()[:60],
                tentativa + 1,
            )

        raise RuntimeError("Gemini insistiu numa chamada ja usada nos posts recentes")

    def _escrever_uma_vez(
        self,
        scored: ScoredOffer,
        link: str,
        coupon: str | None,
        avoid: list[str] | None,
        preco_com_cupom: float | None = None,
    ) -> str:
        response = self._generate(
            _facts(scored, link, coupon, avoid, preco_com_cupom)
        )

        _reject_truncated(response)

        text = (response.text or "").strip()
        if not text:
            # Acontece quando o filtro de seguranca corta a resposta inteira.
            raise RuntimeError("Gemini devolveu resposta vazia")

        _reject_unfounded_claims(text, scored)
        _reject_preco_de_cupom_solto(text, coupon, preco_com_cupom)
        text = _enforce_title(text, scored.offer.title)
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
                self._respeita_rpm()
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

    def _respeita_rpm(self) -> None:
        """Espaca as chamadas pra nao estourar o limite por minuto.

        Uma rodada escreve ate MAX_OFFERS_PER_RUN posts, e sem isso as chamadas
        saem todas no mesmo segundo. O 429 por minuto tem retry (`_generate`),
        mas retry gasta tentativa e atrasa a rodada; espacar na origem e mais
        barato que se recuperar depois.

        Nao contorna cota diaria -- essa nao tem contorno tecnico.
        """
        intervalo = gemini_min_interval_seconds()
        if intervalo <= 0:
            return

        if self._ultima_chamada is not None:
            desde = time.monotonic() - self._ultima_chamada
            if desde < intervalo:
                time.sleep(intervalo - desde)
        self._ultima_chamada = time.monotonic()

    def _raw_generate(self, contents: str):
        try:
            return self._client.models.generate_content(
                model=self._model, contents=contents, config=self._config(pensar=False)
            )
        except Exception as exc:  # noqa: BLE001 - a lib nao expoe tipo estavel
            if "INVALID_ARGUMENT" not in str(exc):
                raise
            # gemini-3.5-flash-lite e gemini-flash-lite-latest recusam
            # thinking_config com 400 INVALID_ARGUMENT. Sao justamente os
            # modelos sem o teto diario de 20 do 2.5-flash, entao vale o
            # segundo caminho em vez de excluir eles da escolha.
            self._sem_thinking = True
            return self._client.models.generate_content(
                model=self._model, contents=contents, config=self._config(pensar=True)
            )

    def _config(self, pensar: bool):
        opcoes = dict(
            system_instruction=SYSTEM,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.8,
        )
        if not (pensar or self._sem_thinking):
            # Os modelos "thinking" gastam o orcamento de saida raciocinando
            # antes de escrever, e o post sai cortado no meio. Escrever 5
            # linhas de oferta nao precisa disso.
            opcoes["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(**opcoes)


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


# As chamadas de exemplo do SYSTEM. Medido: 5 de 9 posts saiam com uma delas
# literal -- o modelo trata a lista como cardapio, e a instrucao de nao repetir
# perde pra ela. Rejeitar e o unico jeito que funciona.
EXEMPLOS_CHAMADA = (
    "37 conto da polo da hering",
    "o trio perfeito pro seu rosto",
    "chega de fritar no oleo",
    "mais barato que o teu ifood de ontem",
    "quem autorizou esse preco",
    "compra logo que eu ja comprei",
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
        raise RuntimeError(f"Instrucao do prompt vazou pro post ({achado.group(0)!r}).")

    chamada = _normaliza(text.strip().splitlines()[0]).rstrip("!?.")
    if chamada in EXEMPLOS_CHAMADA:
        raise RuntimeError(
            f"A chamada {chamada!r} e um exemplo do prompt, nao um texto sobre "
            "este produto."
        )


def _facts(
    scored: ScoredOffer,
    link: str,
    coupon: str | None = None,
    avoid: list[str] | None = None,
    preco_com_cupom: float | None = None,
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
        f"Loja: {store_name(offer.source)}",
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
    if coupon and preco_com_cupom is not None:
        # O preco que a pessoa PAGA, e nao o do anuncio. Pedido em 09/09/2026:
        # o post saia "por R$ 117,35" quando o cupom levava a R$ 105,61, e o
        # numero mais atraente ficava escondido numa linha depois. O grupo
        # concorrente ja faz assim -- "De R$ 154 por R$ 70 no Pix".
        #
        # So entra quando o cupom vale MESMO para este produto: cupom "em
        # itens selecionados" nao chega aqui, porque anunciar um preco que o
        # checkout nao vai dar e pior do que nao citar cupom nenhum.
        facts.append(
            f"Cupom: {coupon}. O preco JA E com o cupom aplicado: escreva "
            f"R$ {brl(preco_com_cupom)} na linha do preco, e diga na mesma "
            f"linha que e com o cupom. O anuncio mostra R$ {brl(offer.price)} "
            "antes do cupom."
        )
    elif coupon:
        # "se ainda estiver valendo" nao e enfeite. Cupom do ML morre por
        # consumo, dentro da validade, e nenhuma fonte publica o contador --
        # medido em 09/09/2026, cinco codigos testados no carrinho pelo dono
        # responderam "O cupom esgotou", dois deles no mesmo dia em que
        # funcionaram. Sem a ressalva, o post promete um desconto que na maior
        # parte do tempo nao existe mais.
        facts.append(
            f"Cupom: {coupon}. Escreva a linha do cupom deixando claro que "
            "pode ja ter esgotado -- algo como 'se ainda estiver valendo'. O "
            "PRECO do post NAO leva o desconto do cupom: use o preco que eu "
            "passei em 'Por'."
        )
    else:
        facts.append("Cupom: NAO ha. NAO escreva linha de cupom.")
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
    facts.append(
        f"Divulgacao obrigatoria (copie literalmente): {disclosure_for(offer.source)}"
    )
    return "\n".join(facts)


def disclosure_for(source: str) -> str:
    """Divulgacao obrigatoria da fonte.

    O default do ML nao e chute: as fontes que caem aqui (`ml_ofertas`, `demo`)
    sao todas do Mercado Livre. Mas o mapa LOJAS e quem valida isso -- fonte
    desconhecida estoura la antes de chegar neste ponto.
    """
    return DISCLOSURES.get(source, DISCLOSURES["mercadolivre"])


def _enforce_title(text: str, titulo: str) -> str:
    """Garante o titulo do anuncio inteiro, como o ML publica.

    Reparo em vez de rejeicao: a chamada em CAIXA ALTA e a parte cara de
    produzir, e descartar o post inteiro por causa do titulo jogaria fora um
    gancho bom pra cair no texto padrao.

    Importa porque o titulo e como a pessoa confere que o produto e o mesmo ao
    abrir o link. "Motorola Moto G17 128GB" e um resumo do modelo; se o anuncio
    for de outra variante de cor ou memoria, ninguem percebe.
    """
    if titulo in text:
        return text

    linhas = text.splitlines()
    # Estrutura fixa: chamada, vazia, titulo. Se o modelo mexeu nisso, o
    # VAZOU_PROMPT ou o proprio formato ja teriam denunciado antes.
    for indice, linha in enumerate(linhas[1:], start=1):
        if not linha.strip():
            continue
        if linha.lstrip().startswith(("De R$", "http", "*")):
            break  # passou do titulo sem achar: nao inventa lugar pra ele
        log.info("Titulo encurtado pelo modelo; restaurando o do anuncio.")
        linhas[indice] = titulo
        return "\n".join(linhas)
    return text


def _normaliza(texto: str) -> str:
    """Sem acento, minusculo, espacos colapsados. Só pra comparar."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def _reject_preco_de_cupom_solto(
    text: str, coupon: str | None, preco_com_cupom: float | None
) -> None:
    """O preco com cupom precisa dizer que e com cupom.

    O numero so e verdadeiro atrelado a condicao. Cupom que vale "em itens
    selecionados" as vezes nao aplica -- medido em 09/09/2026, o TORCIDA
    passou num pre-treino e numa progressiva e falhou num whey. Escrito como
    preco do anuncio, ele seria falso nesses casos.

    Levanta em vez de corrigir: a linha do preco e a linha que o post inteiro
    existe para entregar, e remendar ela no automatico produziria frase torta.
    Quem chama cai no `fallback_copy`, que monta a linha certa por construcao.
    """
    if not coupon or preco_com_cupom is None:
        return

    alvo = brl(preco_com_cupom)
    if alvo not in text:
        raise RuntimeError(
            f"O post nao traz o preco com cupom (R$ {alvo}); o Gemini usou "
            "outro numero na linha do preco."
        )

    for linha in text.splitlines():
        if alvo in linha:
            if re.search(r"com\s+(o\s+)?cupom", linha, re.I):
                return
            raise RuntimeError(
                f"A linha do preco traz R$ {alvo} sem dizer que e com o "
                f"cupom: {linha.strip()!r}"
            )


def _enforce_disclosure(text: str, source: str) -> str:
    """Garante a linha de divulgacao, exatamente uma vez.

    Pedir no prompt nao basta: um LLM parafraseia, e no caso da Amazon a frase
    e contratualmente literal.

    A comparacao ignora acento e caixa de proposito. Observado com
    gemini-3.5-flash: ele escreveu "o preço pra voce" (com acento) e a
    comparacao literal nao achou -- entao a linha canonica foi ANEXADA e o post
    saiu com a divulgacao duplicada, uma acentuada e outra nao. Detectar a
    variante e troca-la pela canonica corrige as duas coisas de uma vez.
    """
    disclosure = disclosure_for(source)
    alvo = _normaliza(disclosure)

    linhas = text.splitlines()
    achou = False
    saida = []
    for linha in linhas:
        if _normaliza(linha) == alvo:
            if achou:
                continue  # duplicata: descarta
            achou = True
            saida.append(disclosure)  # normaliza pra forma canonica
        else:
            saida.append(linha)

    if not achou:
        saida.append(disclosure)

    return _cola_no_link(saida, disclosure)


def _cola_no_link(linhas: list[str], disclosure: str) -> str:
    """Junta a divulgacao a linha do link, tirando a linha em branco entre as
    duas.

    A divulgacao nao pode sair -- publicidade tem que ser reconhecivel como tal
    (CDC art. 36), e o programa de afiliados exige. Mas ela nao precisa ocupar
    um paragrafo proprio: colada ao link, some uma linha em branco do post e a
    identificacao continua inteira, no mesmo lugar que a pessoa olha antes de
    clicar.

    So mexe quando a divulgacao e a ultima linha e o link e a anterior (fora a
    linha em branco). Qualquer outro arranjo fica como esta -- reposicionar as
    cegas produziria post torto.
    """
    if len(linhas) < 3 or linhas[-1] != disclosure:
        return "\n".join(linhas)

    miolo, branco, fim = linhas[:-2], linhas[-2], linhas[-1]
    if branco.strip() or not miolo or not miolo[-1].startswith("http"):
        return "\n".join(linhas)

    return "\n".join(miolo[:-1] + [f"{miolo[-1]}\n{fim}"])


def fallback_copy(
    scored: ScoredOffer,
    link: str,
    coupon: str | None = None,
    preco_com_cupom: float | None = None,
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
        f"De R$ {brl(scored.baseline)} por *R$ {brl(preco_com_cupom)}* com o cupom"
        if coupon and preco_com_cupom is not None
        else f"De R$ {brl(scored.baseline)} por *R$ {brl(offer.price)}*",
    ]
    if coupon:
        lines.append(
            f"Tenta o cupom {coupon} 🎟️ (se ainda estiver valendo)"
            if preco_com_cupom is None
            else f"Use o cupom: {coupon} 🎟️"
        )
    if scored.lowest_ever:
        lines.append("Menor preco desde que comecamos a monitorar.")
    lines.append("")
    if offer.official_store:
        lines.append("Loja oficial no ML")
    lines.append(link)
    lines.append(disclosure_for(offer.source))
    # Sem linha em branco entre o link e a divulgacao, igual ao caminho do
    # Gemini (ver `_cola_no_link`). Os dois textos vao para o mesmo grupo e
    # nao podem ter formato diferente -- o fallback entra justamente nas
    # rodadas em que o Gemini falha, que sao imprevisiveis.
    return "\n".join(lines)

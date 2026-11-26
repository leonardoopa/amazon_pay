"""O Gemini entende o pedido e escreve a resposta -- presa ao que o banco tem.

Duas chamadas, e nenhuma decide preco:

1. `entender`: a mensagem vira um pedido estruturado (o que procurar, faixa de
   preco). Saida em JSON validado; se o Gemini falhar, uma leitura simples por
   palavras cobre.
2. `_escrever`: uma abertura e uma legenda por produto, a partir dos itens ja
   formatados. Cada legenda e conferida antes de sair -- todo "R$" tem que ser
   um dos nossos e o link tem que ser o do proprio produto. Reprovou qualquer
   uma, saem as legendas fixas, que nunca erram numero.

A resposta de produto e um cartao por item (foto + legenda) e um fechamento.

E o mesmo desenho do copywriter do grupo: o modelo escreve, o codigo confere.
"""

from __future__ import annotations

import html
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from ..config import (
    amazon_partner_tag,
    assistente_gemini_modelo,
    gemini_api_key,
    group_invite_url,
    ml_affiliate,
)
from ..copywriter import brl
from ..db import now, sem_acento
from ..delivery.evolution import CAPTION_LIMIT
from ..models import Offer
from .banco import Contexto
from .busca import (
    DIAS_MINIMOS,
    JANELA_DA_MEDIANA,
    Busca,
    Historico,
    Resultado,
    detalhes,
    historico,
    termos,
)

log = logging.getLogger("promo")

LOJAS = {
    "mercadolivre": "Mercado Livre",
    "ml_ofertas": "Mercado Livre",
    "amazon": "Amazon",
}

DIVULGACAO = (
    "Os links são de afiliado: a Comunidade do Desconto pode ganhar comissão, "
    "sem custo nenhum para você."
)

# Teto de tamanho da resposta. Mensagem longa no WhatsApp ninguem le inteira.
MAX_CARACTERES = 1500
# Teto do nome do produto no modelo fixo, e as palavras que nao fecham um nome
# cortado ("... Preto com" vira "... Preto…").
TAMANHO_DO_NOME = 70
PONTAS_SOLTAS = {"com", "de", "da", "do", "das", "dos", "e", "para", "em", ""}

SEM_HISTORICO = "Ainda sem histórico suficiente para comparar o preço."
JANELA_DO_HISTORICO = JANELA_DA_MEDIANA

PEDE_O_PRODUTO = "Me diga de qual produto você quer saber o preço."
PRODUTO_SUMIU = "Esse produto não aparece mais disponível nas lojas que acompanhamos."

PRECO = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*,\d{2}")
LINK = re.compile(r"https?://\S+")


class Pedido(BaseModel):
    tipo: Literal["produto", "historico", "conversa"] = "produto"
    consulta: str = ""
    preco_min: float | None = None
    preco_max: float | None = None
    # So no tipo "historico": qual produto da ultima resposta (1 a 3) e o preco
    # que a pessoa diz ter visto.
    item: int | None = None
    preco_citado: float | None = None


class Escrita(BaseModel):
    abertura: str = ""
    legendas: list[str] = []


@dataclass(frozen=True)
class Cartao:
    """Um produto na resposta: a foto dele e a legenda que vai embaixo."""

    legenda: str
    imagem: str | None


@dataclass(frozen=True)
class Resposta:
    texto: str
    consulta: str | None
    resultados: int
    cartoes: tuple[Cartao, ...] = ()
    produtos: tuple[str, ...] = ()  # os product_id mostrados: a memoria da conversa


ENTENDER = """Voce le mensagens de WhatsApp enviadas ao assistente de um grupo de \
ofertas brasileiro e diz o que a pessoa procura.

Responda SO o JSON pedido:
- tipo: "produto" quando a pessoa procura um produto ou um preco; "historico" \
quando, com uma conversa anterior, ela pergunta sobre o preco ou o historico de \
preco de um produto que JA foi mostrado ("esse foi o melhor preco?", "ja \
esteve mais barato?"); "conversa" para cumprimento, agradecimento ou qualquer \
outra coisa.
- item: so no "historico", qual produto da conversa anterior (1, 2 ou 3). \
Sem indicacao, 1.
- preco_citado: so no "historico", o valor em reais que a pessoa diz ter visto \
("4k" e 4000, "4 mil" e 4000). Sem valor, null.
- consulta: o produto, com as palavras que estariam no titulo do anuncio \
(marca, modelo, tipo). Sem "barato", "promocao", "tem", "quero", "mande". \
Corrija erro de digitacao, mas mantenha siglas como a pessoa escreveu ("ps5", \
"gta 6"): a busca ja conhece os sinonimos. Nao acrescente palavra que a pessoa \
nao disse ("ps5" nao vira "console ps5"). Com conversa anterior, uma busca nova \
que completa a anterior sai com a consulta inteira ("e o console?" depois de \
"ps5" vira "console ps5").
- preco_min e preco_max: so se a pessoa disser um valor ("ate 400", "menos de \
mil", "entre 100 e 200"). Em reais, numero puro. Sem valor, null."""

ESCREVER = """Voce responde, no WhatsApp, quem perguntou por um produto ao \
assistente da Comunidade do Desconto, um grupo de ofertas.

Use SO os itens do JSON. Nao invente produto, preco, loja, data nem link, e nao \
prometa que o preco continua valendo.

Responda SO o JSON pedido:
- abertura: uma frase curta, sem exagero, sem preco e sem link.
- legendas: uma por item de "achados", na mesma ordem. Cada legenda tem o nome \
(encurtado se for longo, sem mudar o modelo), o preco exatamente como esta, a \
loja, o "visto", o "grupo" quando houver, o "veredito", e o link sozinho na \
ultima linha.
- Escreva em portugues do Brasil, com acentos.
- Texto puro, sem entidades HTML (escreva "opções", nunca "op&ccedil;&otilde;es").
- Negrito do WhatsApp com *asteriscos* so no nome do produto. Nada de markdown \
de titulo, tabela ou emoji em excesso."""


# Mensagem feita so destas palavras e conversa, nao pedido de produto.
CUMPRIMENTOS = {
    "oi", "ola", "obrigado", "obrigada", "valeu", "bom", "dia", "boa", "tarde",
    "noite", "tudo", "bem", "blz",
}  # fmt: skip


SINAL_DE_HISTORICO = re.compile(
    r"melhor preco|menor preco|mais barat[oa]|ja esteve|ja teve|baixou|esse foi"
    r"|historico"
)
ORDINAIS = {"primeir": 1, "segund": 2, "terceir": 3}
ORDINAL = re.compile(r"\b(primeir|segund|terceir)[oa]\b")
# Posicao so em formas como "o 2", "a 2", "item 2", "numero 2"; numero seguido de
# k, mil, reais ou de unidade de tempo nao e posicao.
POSICAO = re.compile(
    r"\b(?:o|a|item|numero|n|no)\s+([1-3])\b"
    r"(?!\s*(?:k\b|mil\b|reais|dias?\b|semanas?\b|meses\b|horas?\b|[.,]\d))"
)
# Valor so conta depois de palavra de preco: "tv 4k" nao e um preco.
PALAVRA_DE_PRECO = r"\b(?:por|vi|tava|estava|custava|custou|paguei|de)\s+"
VALOR_COM_K = re.compile(PALAVRA_DE_PRECO + r"(\d+(?:[.,]\d+)?)\s*(?:k|mil)\b")
VALOR_SIMPLES = re.compile(
    PALAVRA_DE_PRECO + r"(\d[\d.]*(?:,\d+)?)\b"
    r"(?!\s*(?:k\b|mil\b|dias?\b|semanas?\b|meses\b|horas?\b|x\b|vezes\b|%))"
)
VALOR_COM_RS = re.compile(r"r\$\s*(\d[\d.]*(?:,\d+)?)")
VALOR_EM_REAIS = re.compile(r"(\d[\d.]*(?:,\d+)?)\s*reais")
# Palavras de pergunta de historico que nao descrevem produto. Sobrando alguma
# outra palavra ("controle", "geladeira"), e busca nova, nao pergunta sobre o
# produto ja mostrado.
PALAVRAS_DE_HISTORICO = {
    "menor", "maior", "esteve", "estava", "teve", "tinha", "tava", "foi", "era",
    "ja", "vi", "viu", "visto", "custava", "custou", "paguei", "caiu", "baixou",
    "subiu", "esses", "essas", "dias", "semana", "semanas", "atras", "antes",
    "ontem", "historico", "primeiro", "primeira", "segundo", "segunda",
    "terceiro", "terceira", "item", "numero", "mil", "reais", "r",
}  # fmt: skip


def _numero(texto: str) -> float | None:
    try:
        return float(texto.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def preco_citado(texto: str) -> float | None:
    """O valor que a pessoa diz ter visto: "4k", "4 mil", "R$ 3.799", "3799 reais".

    "4k" e numero solto so valem depois de palavra de preco ("vi por 4k"): em
    "essa tv 4k" o 4k e a resolucao.
    """
    limpo = sem_acento(texto).lower()
    com_k = VALOR_COM_K.search(limpo)
    if com_k:
        # "4,5k" e "4.5k" sao quatro e meio; so o ponto de milhar e que sai.
        base = _numero(com_k.group(1).replace(".", ","))
        return base * 1000 if base is not None else None
    for padrao in (VALOR_COM_RS, VALOR_EM_REAIS, VALOR_SIMPLES):
        achado = padrao.search(limpo)
        if achado:
            valor = _numero(achado.group(1))
            # "de 3" ou "por 2" sao quantidade, nao preco.
            return valor if valor is not None and valor >= 10 else None
    return None


def _item_citado(limpo: str) -> int:
    ordinal = ORDINAL.search(limpo)
    if ordinal:
        return ORDINAIS[ordinal.group(1)]
    posicao = POSICAO.search(limpo)
    return int(posicao.group(1)) if posicao else 1


def _termos_do_produto(palavras: list[str]) -> list[str]:
    """O que sobra da pergunta de historico depois de tirar o que nao e produto
    (numero solto e "4k" tambem saem: sao preco ou posicao)."""
    return [
        t
        for t in palavras
        if t not in PALAVRAS_DE_HISTORICO and not re.fullmatch(r"\d+k?", t)
    ]


def pedido_simples(texto: str, tem_contexto: bool = False) -> Pedido:
    """A leitura sem Gemini: as palavras do texto e um "ate N" se houver.

    Pergunta de preco sem nenhuma palavra de produto vira pedido de historico:
    com conversa anterior responde sobre o produto mostrado; sem ela, o
    `responder` pede o produto. Sobrou palavra de produto ("tem controle de ps5
    mais barato?"), e busca nova.
    """
    sem = sem_acento(texto).lower()
    palavras = termos(texto)
    if SINAL_DE_HISTORICO.search(sem):
        palavras = [t for t in palavras if t not in PALAVRAS_DE_HISTORICO]
        if not _termos_do_produto(palavras):
            return Pedido(
                tipo="historico",
                item=_item_citado(sem),
                preco_citado=preco_citado(texto),
            )
    limpo = texto.lower()
    teto = re.search(
        r"(?:ate|até|menos de|abaixo de)\s*(?:r\$\s*)?(\d[\d.]*(?:,\d+)?)", limpo
    )
    preco_max = None
    if teto:
        try:
            preco_max = float(teto.group(1).replace(".", "").replace(",", "."))
        except ValueError:
            preco_max = None
    consulta = " ".join(palavras)
    if preco_max is not None:
        consulta = " ".join(
            t for t in consulta.split() if not t.replace(".", "").isdigit()
        )
    cumprimento = set(palavras) <= CUMPRIMENTOS
    return Pedido(
        tipo="conversa" if cumprimento or not consulta else "produto",
        consulta=consulta,
        preco_max=preco_max,
    )


def _em_brasilia(iso: str) -> datetime:
    try:
        from zoneinfo import ZoneInfo

        fuso = ZoneInfo("America/Sao_Paulo")
    except Exception:  # noqa: BLE001 - sem tzdata, fica a hora local da maquina
        fuso = None
    return datetime.fromisoformat(iso).astimezone(fuso)


def _dia_e_hora(iso: str, agora: datetime | None = None) -> str:
    """'hoje às 14:20', 'ontem às 09:05' ou 'em 12/11'.

    Hora de Brasilia de proposito, e nao a do servidor: e a que a pessoa le.
    """
    momento = _em_brasilia(iso)
    hoje = (agora or now()).astimezone(momento.tzinfo).date()
    if momento.date() == hoje:
        return f"hoje às {momento:%H:%M}"
    if momento.date() == hoje - timedelta(days=1):
        return f"ontem às {momento:%H:%M}"
    return f"em {momento:%d/%m}"


def quando(visto_em: str, agora: datetime | None = None) -> str:
    """'visto hoje às 14:20': quando o bot mediu este preco."""
    return f"visto {_dia_e_hora(visto_em, agora)}"


def no_grupo(r: Resultado, agora: datetime | None = None) -> str | None:
    """'Saiu no grupo hoje às 22:11.', quando a oferta foi para o grupo."""
    return f"Saiu no grupo {_dia_e_hora(r.no_grupo, agora)}." if r.no_grupo else None


def veredito(r: Resultado) -> str:
    """O que o nosso historico diz deste preco. E o que loja nenhuma conta.

    Preco que nao mudou na janela nao e "o menor dos ultimos 60 dias": em
    18/11/2026 um gel parado em R$ 69,90 saiu com esse selo, porque o menor, a
    mediana e o de hoje eram o mesmo numero.
    """
    if r.mediana is None or r.dias < DIAS_MINIMOS:
        return SEM_HISTORICO
    mediana = f"R$ {brl(r.mediana)}"
    variacao = (r.preco - r.mediana) / r.mediana
    parado = r.menor is not None and r.menor >= r.mediana * 0.99
    if r.menor is not None and (
        r.preco < r.menor or (r.preco <= r.menor and not parado)
    ):
        return f"Menor preço dos últimos 60 dias (a mediana é {mediana})."
    if parado and abs(variacao) < 0.03:
        return f"Mesmo preço dos últimos 60 dias ({mediana})."
    if variacao <= -0.03:
        return f"Abaixo da mediana de 60 dias ({mediana})."
    if variacao >= 0.03:
        return f"Acima da mediana de 60 dias ({mediana}): vale esperar."
    return f"Na média dos últimos 60 dias ({mediana})."


def _dd_mm(iso: str) -> str:
    """'DD/MM' de uma data ISO (so o dia, ou o instante em hora de Brasilia)."""
    if len(iso) == 10:
        return f"{iso[8:10]}/{iso[5:7]}"
    return f"{_em_brasilia(iso):%d/%m}"


def texto_do_historico(
    h: Historico,
    link: str,
    preco_citado: float | None,
    agora: datetime | None = None,
) -> str:
    """O historico de um produto so com o que o bot mediu, sem Gemini no meio."""
    r = h.resultado
    agora_br = _em_brasilia((agora or now()).isoformat())
    visto_hoje = _em_brasilia(r.visto_em).date() == agora_br.date()
    linhas = [
        f"*{nome_curto(nome_de_vitrine(r.titulo))}*",
        f"{'Hoje' if visto_hoje else 'Último preço'}: R$ {brl(r.preco)} no "
        f"{LOJAS.get(r.fonte, r.fonte)}, {quando(r.visto_em, agora)}.",
    ]
    suficiente = r.dias >= DIAS_MINIMOS and h.menor_preco is not None
    if suficiente:
        linhas.append(
            f"Menor preço que medimos em {JANELA_DO_HISTORICO} dias: "
            f"R$ {brl(h.menor_preco)}, em {_dd_mm(h.menor_em)}."
        )
    else:
        linhas.append(SEM_HISTORICO)
    if h.grupo_em and h.grupo_preco is not None:
        linhas.append(
            f"Saiu no grupo em {_dd_mm(h.grupo_em)} por R$ {brl(h.grupo_preco)}."
        )
    if preco_citado is not None and not (
        r.preco > 0 and 0.5 <= preco_citado / r.preco <= 2
    ):
        # Preco citado longe do preco deste produto e de outro produto: em
        # 26/11/2026 o "4k" de um console foi comparado com um acessorio de
        # R$ 1.534,99, e a resposta afirmou que medimos "abaixo dos R$ 4.000".
        linhas.append(
            f"O preço que você citou (R$ {brl(preco_citado)}) não parece ser "
            "deste produto."
        )
    elif preco_citado is not None and h.menor_preco is not None and h.dias > 0:
        # Com pouco historico a janela nao e de 60 dias: fala dos dias que existem.
        # Nao contradiz o "sem historico suficiente" -- so diz o que foi medido.
        menor = f"R$ {brl(h.menor_preco)} em {_dd_mm(h.menor_em)}"
        citado = f"R$ {brl(preco_citado)}"
        if h.menor_preco <= preco_citado:
            relacao = "igual ou abaixo" if h.menor_preco == preco_citado else "abaixo"
            inicio = (
                "Sim: medimos"
                if suficiente
                else f"Nos {h.dias} dias que medimos, o menor preço foi"
            )
            linhas.append(f"{inicio} {menor}, {relacao} dos {citado} que você viu.")
        elif suficiente:
            linhas.append(
                f"Não medimos esse produto a {citado} ou menos "
                f"nos últimos {JANELA_DO_HISTORICO} dias."
            )
        else:
            linhas.append(
                f"Nos {h.dias} dias que medimos, o preço não chegou a {citado} "
                f"(o menor foi R$ {brl(h.menor_preco)})."
            )
    parecer = veredito(r)
    if parecer != SEM_HISTORICO or suficiente:
        linhas.append(parecer)
    linhas += [link, "", DIVULGACAO]
    return "\n".join(linhas)


def nome_curto(nome: str, limite: int = TAMANHO_DO_NOME) -> str:
    """O nome cortado numa palavra inteira, com reticencias.

    Titulo de anuncio do ML passa de 200 caracteres ("... Corrida Caminhada
    Ciclismo Natacao Monitoramento Sono ..."), e o modelo fixo imprimia tudo.
    O Gemini recebe o nome inteiro e encurta sozinho, sem mudar o modelo.
    """
    if len(nome) <= limite:
        return nome
    palavras = nome[:limite].rsplit(" ", 1)[0].split()
    while palavras and palavras[-1].lower().strip("(-+|/,") in PONTAS_SOLTAS:
        palavras.pop()
    return " ".join(palavras).rstrip(" -+|/,(") + "…"


def nome_de_vitrine(titulo: str) -> str:
    """O titulo sem o preco colado no fim ("... - R$ 4.650") e sem o (R).

    Anuncio repassado de outro grupo chega com o preco no titulo; na resposta
    ele brigaria com o preco medido, que esta na linha de baixo.
    """
    nome = re.sub(r"\s*[-–|]\s*R\$\s*[\d.,]+\s*$", "", titulo).replace("®", "")
    return re.sub(r"\s{2,}", " ", nome).strip()


class Links:
    """O link de cada resultado: de afiliado sempre que der, o do produto se nao.

    Reaproveita as mesmas peças do grupo -- `resolve_affiliate_link` (banco do
    bot, recusa registrada, painel) e a tag da Amazon --, entao um produto que o
    programa recusa nao gasta consulta ao painel de novo.
    """

    def __init__(self) -> None:
        self._builder = None

    def _link_builder(self):
        if self._builder is None:
            cookie, tag = ml_affiliate()
            if cookie and tag:
                from ..sources.ml_linkbuilder import LinkBuilder

                self._builder = LinkBuilder(cookie, tag)
        return self._builder

    def de(self, r: Resultado) -> str:
        try:
            if r.fonte == "amazon":
                from ..sources.amazon_link import link_de_afiliado

                tag = amazon_partner_tag()
                return link_de_afiliado(r.external_id, tag) if tag else r.url
            if r.fonte in ("mercadolivre", "ml_ofertas"):
                from ..sources.affiliate import resolve_affiliate_link

                oferta = Offer(
                    source=r.fonte,
                    external_id=r.external_id,
                    title=r.titulo,
                    price=r.preco,
                    url=r.url,
                )
                return resolve_affiliate_link(self._link_builder(), oferta) or r.url
        except (
            Exception
        ) as exc:  # noqa: BLE001 - sem link de afiliado, vai o do produto
            log.warning("Link de afiliado falhou para %s: %s", r.product_id, exc)
        return r.url


def _precos(texto: str) -> set[str]:
    """Os valores em real do texto, com o espaco depois do R$ padronizado."""
    return {re.sub(r"R\$\s?", "R$ ", p) for p in PRECO.findall(texto)}


def fiel(texto: str, itens: list[dict]) -> bool:
    """O texto so cita preco e link que estao nos itens, e cita todos os links."""
    if not texto or len(texto) > MAX_CARACTERES:
        return False
    permitidos: set[str] = set()
    for item in itens:
        permitidos |= _precos(f"{item['preco']} {item['veredito']}")
    if not _precos(texto) <= permitidos:
        return False
    links = {item["link"] for item in itens}
    usados = {u.rstrip(").,;*_") for u in LINK.findall(texto)}
    return usados == links


def abertura_fixa(consulta: str) -> str:
    return f"Achei estas opções para *{consulta}*:"


def legenda_fixa(n: int, item: dict) -> str:
    """A legenda sem Gemini: o bloco do item `n`. Nunca erra numero."""
    return "\n".join(
        [
            f"{n}. *{nome_curto(item['nome'])}*",
            f"{item['preco']} no {item['loja']}, {item['visto']}",
            *([item["grupo"]] if item.get("grupo") else []),
            item["veredito"],
            item["link"],
        ]
    )


def fechamento(parecidos: list[dict]) -> str:
    """Os parecidos (se houver) e o aviso de afiliado: o texto depois dos cartoes."""
    if not parecidos:
        return DIVULGACAO
    linhas = ["Parecidos:"]
    for item in parecidos:
        linhas += [
            f"- {nome_curto(item['nome'])}: {item['preco']} no {item['loja']}",
            item["link"],
        ]
    return "\n".join(linhas) + f"\n\n{DIVULGACAO}"


def montar_cartoes(
    abertura: str, legendas: list[str], imagens: list[str | None]
) -> tuple[Cartao, ...]:
    """Um cartao por produto; a abertura vai no comeco da primeira legenda."""
    textos = list(legendas)
    # Abertura que empurraria a legenda alem do limite corta o fim, onde esta o
    # link: nesse caso a abertura cai.
    if abertura and textos and len(abertura) + 2 + len(textos[0]) <= CAPTION_LIMIT:
        textos[0] = f"{abertura}\n\n{textos[0]}"
    return tuple(Cartao(t, i) for t, i in zip(textos, imagens, strict=True))


def escrita_aprovada(escrita: Escrita, achados: list[dict]) -> bool:
    """So passa a escrita que cita os nossos precos e o link de cada produto."""
    if len(escrita.legendas) != len(achados):
        return False
    if _precos(escrita.abertura) or LINK.search(escrita.abertura):
        return False
    for legenda, achado in zip(escrita.legendas, achados):
        if len(legenda) > CAPTION_LIMIT or not fiel(legenda, [achado]):
            return False
    primeira = escrita.legendas[0] if escrita.legendas else ""
    if escrita.abertura:
        primeira = f"{escrita.abertura}\n\n{primeira}"
    return len(primeira) <= CAPTION_LIMIT


def boas_vindas() -> str:
    texto = (
        "Oi! Eu procuro preço nas ofertas que a Comunidade do Desconto acompanha. "
        "Me diga o produto, por exemplo: *air fryer até 400* ou *ps5*."
    )
    convite = group_invite_url()
    return f"{texto}\n\nOfertas todo dia no grupo: {convite}" if convite else texto


def nao_achei(pedido: Pedido) -> str:
    faixa = f" até R$ {brl(pedido.preco_max)}" if pedido.preco_max else ""
    return (
        f"Não achei *{pedido.consulta}*{faixa} entre os produtos que vimos nos "
        "últimos 30 dias. Tente outro nome, ou um modelo mais geral."
    )


class Assistente:
    def __init__(
        self,
        busca: Busca,
        client=None,
        modelo: str | None = None,
        links: Links | None = None,
    ) -> None:
        self._busca = busca
        self._client = client
        self._modelo = modelo or assistente_gemini_modelo()
        self._links = links or Links()

    def _cliente(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=gemini_api_key())
        return self._client

    def _gerar(self, conteudo: str, sistema: str, **extra):
        from google.genai import types

        # Sem `thinking_config`: o modelo padrao (flash-lite) recusa o campo com
        # 400, como o copywriter do grupo ja descobriu.
        config = types.GenerateContentConfig(system_instruction=sistema, **extra)
        return self._cliente().models.generate_content(
            model=self._modelo, contents=conteudo, config=config
        )

    def entender(self, texto: str, contexto_texto: str | None = None) -> Pedido:
        conteudo = texto
        if contexto_texto:
            conteudo = f"Conversa anterior:\n{contexto_texto}\n\nMensagem: {texto}"
        try:
            resposta = self._gerar(
                conteudo,
                ENTENDER,
                response_mime_type="application/json",
                response_schema=Pedido,
                temperature=0.0,
            )
            parsed = getattr(resposta, "parsed", None)
            if isinstance(parsed, Pedido):
                pedido = parsed
            else:
                pedido = Pedido.model_validate(json.loads(resposta.text))
            # O Gemini as vezes devolve entidades HTML; a busca quer texto puro.
            return pedido.model_copy(
                update={"consulta": html.unescape(pedido.consulta)}
            )
        except Exception as exc:  # noqa: BLE001 - sem Gemini, a leitura simples cobre
            log.warning("Gemini nao leu o pedido; vai a leitura simples: %s", exc)
            return pedido_simples(texto, tem_contexto=bool(contexto_texto))

    def _resumo_da_conversa(self, contexto: Contexto) -> str:
        """A busca anterior e os produtos mostrados, para o Gemini entender 'o 2'.

        A numeracao e a da lista original (`item` indexa `contexto.produtos`);
        produto que saiu do ar fica marcado em vez de fechar a fila. O preco vai
        junto: "o de 4k" so se resolve olhando o valor de cada item.
        """
        achados = detalhes(list(contexto.produtos))
        linhas = [f"Busca anterior: {contexto.consulta or '(sem busca)'}"]
        for n, pid in enumerate(contexto.produtos, 1):
            r = achados.get(pid)
            if r is None:
                linhas.append(f"{n}. (não está mais disponível)")
            else:
                nome = nome_curto(nome_de_vitrine(r.titulo))
                linhas.append(f"{n}. {nome} - R$ {brl(r.preco)}")
        return "\n".join(linhas)

    def _historico(
        self, pedido: Pedido, contexto: Contexto | None, agora: datetime | None
    ) -> Resposta:
        produtos = contexto.produtos if contexto else ()
        consulta = contexto.consulta if contexto else None
        item = pedido.item or 1
        if not 1 <= item <= len(produtos):
            return Resposta(PEDE_O_PRODUTO, consulta, 0, produtos=produtos)
        h = historico(produtos[item - 1])
        if h is None:
            return Resposta(PRODUTO_SUMIU, consulta, 0, produtos=produtos)
        texto = texto_do_historico(
            h, self._links.de(h.resultado), pedido.preco_citado, agora
        )
        return Resposta(texto, consulta, 1, produtos=produtos)

    def _item(self, r: Resultado, agora: datetime | None) -> dict:
        item = {
            "nome": nome_de_vitrine(r.titulo),
            "loja": LOJAS.get(r.fonte, r.fonte),
            "preco": f"R$ {brl(r.preco)}",
            "visto": quando(r.visto_em, agora),
            "veredito": veredito(r),
            "link": self._links.de(r),
        }
        grupo = no_grupo(r, agora)
        if grupo:
            item["grupo"] = grupo
        return item

    def _escrever(self, pergunta: str, achados: list[dict]) -> Escrita | None:
        dados = json.dumps(
            {"pergunta": pergunta, "achados": achados}, ensure_ascii=False
        )
        try:
            resposta = self._gerar(
                dados,
                ESCREVER,
                response_mime_type="application/json",
                response_schema=Escrita,
                temperature=0.4,
            )
            parsed = getattr(resposta, "parsed", None)
            if isinstance(parsed, Escrita):
                escrita = parsed
            else:
                escrita = Escrita.model_validate_json(resposta.text)
        except Exception as exc:  # noqa: BLE001 - sem Gemini, vao as legendas fixas
            log.warning("Gemini nao escreveu as legendas; vao as fixas: %s", exc)
            return None
        # Entidades HTML ("op&ccedil;&otilde;es", "&amp;" no link) passariam na
        # conferencia e sairiam como lixo: confere o texto que de fato vai.
        escrita = Escrita(
            abertura=html.unescape(escrita.abertura).strip(),
            legendas=[html.unescape(t).strip() for t in escrita.legendas],
        )
        if not escrita_aprovada(escrita, achados):
            log.info("Legendas do Gemini reprovaram na conferencia; vao as fixas.")
            return None
        return escrita

    def responder(
        self,
        texto: str,
        agora: datetime | None = None,
        contexto: Contexto | None = None,
    ) -> Resposta:
        resumo = self._resumo_da_conversa(contexto) if contexto else None
        pedido = self.entender(texto, resumo)
        if pedido.tipo == "historico":
            return self._historico(pedido, contexto, agora)
        if pedido.tipo != "produto" or not termos(pedido.consulta):
            return Resposta(boas_vindas(), None, 0)

        resultados = self._busca.buscar(
            pedido.consulta, pedido.preco_min, pedido.preco_max
        )
        if not resultados:
            return Resposta(nao_achei(pedido), pedido.consulta, 0)

        parecidos = self._busca.parecidos(
            resultados[0], excluir={r.product_id for r in resultados}
        )
        achados = [self._item(r, agora) for r in resultados]
        extras = [self._item(r, agora) for r in parecidos]
        escrita = self._escrever(texto, achados)
        if escrita is None:
            escrita = Escrita(
                abertura=abertura_fixa(pedido.consulta),
                legendas=[legenda_fixa(n, a) for n, a in enumerate(achados, 1)],
            )
        cartoes = montar_cartoes(
            escrita.abertura, escrita.legendas, [r.imagem for r in resultados]
        )
        return Resposta(
            fechamento(extras),
            pedido.consulta,
            len(resultados),
            cartoes,
            tuple(r.product_id for r in resultados),
        )

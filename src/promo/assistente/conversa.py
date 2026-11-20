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
    copy_model,
    gemini_api_key,
    group_invite_url,
    ml_affiliate,
)
from ..copywriter import brl
from ..db import now
from ..delivery.evolution import CAPTION_LIMIT
from ..models import Offer
from .busca import DIAS_MINIMOS, Busca, Resultado, termos

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

PRECO = re.compile(r"R\$\s?\d{1,3}(?:\.\d{3})*,\d{2}")
LINK = re.compile(r"https?://\S+")


class Pedido(BaseModel):
    tipo: Literal["produto", "conversa"] = "produto"
    consulta: str = ""
    preco_min: float | None = None
    preco_max: float | None = None


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


ENTENDER = """Voce le mensagens de WhatsApp enviadas ao assistente de um grupo de \
ofertas brasileiro e diz o que a pessoa procura.

Responda SO o JSON pedido:
- tipo: "produto" quando a pessoa procura um produto ou um preco; "conversa" \
para cumprimento, agradecimento ou qualquer outra coisa.
- consulta: o produto, com as palavras que estariam no titulo do anuncio \
(marca, modelo, tipo). Sem "barato", "promocao", "tem", "quero", "mande". \
Corrija erro de digitacao, mas mantenha siglas como a pessoa escreveu ("ps5", \
"gta 6"): a busca ja conhece os sinonimos. Nao acrescente palavra que a pessoa \
nao disse ("ps5" nao vira "console ps5").
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


def pedido_simples(texto: str) -> Pedido:
    """A leitura sem Gemini: as palavras do texto e um "ate N" se houver."""
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
    consulta = " ".join(termos(texto))
    if preco_max is not None:
        consulta = " ".join(
            t for t in consulta.split() if not t.replace(".", "").isdigit()
        )
    cumprimento = set(termos(texto)) <= CUMPRIMENTOS
    return Pedido(
        tipo="conversa" if cumprimento or not consulta else "produto",
        consulta=consulta,
        preco_max=preco_max,
    )


def _dia_e_hora(iso: str, agora: datetime | None = None) -> str:
    """'hoje às 14:20', 'ontem às 09:05' ou 'em 12/11'.

    Hora de Brasilia de proposito, e nao a do servidor: e a que a pessoa le.
    """
    try:
        from zoneinfo import ZoneInfo

        fuso = ZoneInfo("America/Sao_Paulo")
    except Exception:  # noqa: BLE001 - sem tzdata, fica a hora local da maquina
        fuso = None
    momento = datetime.fromisoformat(iso).astimezone(fuso)
    hoje = (agora or now()).astimezone(fuso).date()
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
        return "Ainda sem histórico suficiente para comparar o preço."
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
        self._modelo = modelo or copy_model()
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

    def entender(self, texto: str) -> Pedido:
        try:
            resposta = self._gerar(
                texto,
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
            return pedido_simples(texto)

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

    def responder(self, texto: str, agora: datetime | None = None) -> Resposta:
        pedido = self.entender(texto)
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
        return Resposta(fechamento(extras), pedido.consulta, len(resultados), cartoes)

"""Coleta -> registra historico -> filtra -> escreve -> entrega."""

from __future__ import annotations

import functools
import inspect
import json
import logging
import random
import re
import time
from datetime import date, datetime
from dataclasses import dataclass, replace
from pathlib import Path

from .config import (
    amazon_partner_tag,
    AmazonConfig,
    MercadoLivreConfig,
    MissingConfig,
    ROOT,
    Rules,
    discovery_interval_hours,
    drip_interval_seconds,
    full_refetch_interval_hours,
    grupos_fonte_intervalo_horas,
    hot_interval_minutes,
    hot_margin_pct,
    hot_track_limit,
    category_cooldown_minutes,
    family_cooldown_minutes,
    max_comida_e_bebida_por_run,
    max_do_geral_por_hora,
    max_pending_queue,
    max_pistas_por_run,
    max_por_grupo_tematico,
    ofertas_pages,
    post_cooldown_minutes,
    post_max_age_minutes,
    price_focus_max,
    prefer_official_store,
    discovery_categories_per_round,
    discovery_priority_share,
    discovery_terms_per_round,
    price_focus_reserve,
    priority_ignores_price_focus,
    priority_min_discount_pct,
    priority_reserve,
    quiet_drip_multiplier,
    quiet_max_offers_per_run,
    quiet_window,
    reserva_de_prioridade_do_grupo,
    run_interval_seconds,
    track_limit,
)
from .copywriter import Copywriter, com_convite, fallback_copy
from .db import (
    CATEGORIA,
    categories_in_cooldown,
    connect,
    create_post,
    expire_stale_posts,
    enviados_na_janela,
    familia_do_titulo,
    get_meta,
    mark_post_failed,
    mark_post_sent,
    hot_products,
    hours_since,
    now,
    pending_posts,
    mesmo_produto,
    products_in_cooldown,
    produtos_ja_postados,
    titulos_recentes,
    recent_headlines,
    record_offer,
    sem_acento,
    set_meta,
    tem_tema,
    tracked_products,
)
from .delivery import NotConnected, WindowClosed, build_delivery
from .models import Offer, ScoredOffer
from .scoring import score, score_campaign, score_pista
from .sources.amazon import Amazon
from .sources.amazon_grupo import AmazonDoGrupo
from .sources.mercadolivre import MercadoLivre
from .sources.ml_ofertas import MLOfertas

log = logging.getLogger("promo")


@dataclass
class Watch:
    term: str
    max_price: float | None = None


@dataclass
class Category:
    """Categoria acompanhada pelos mais vendidos do ML."""

    id: str
    name: str = ""
    max_price: float | None = None


def load_watchlist(path: Path | None = None) -> list[Watch]:
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Watch(term=entry["term"], max_price=entry.get("max_price"))
        for entry in data["keywords"]
    ]


@dataclass(frozen=True)
class GrupoDestino:
    """Um grupo de WhatsApp que recebe posts, e o recorte dele.

    `temas` vazio quer dizer "recebe tudo" -- e o grupo geral. Com temas, o
    grupo so recebe oferta cujo TITULO cite um deles, que e a mesma regra de
    `priority`: a oferta chega por busca, por categoria ou pela vitrine, e
    nenhuma dessas carrega o motivo de ter vindo. O titulo carrega.

    `exclui` derruba a oferta mesmo que um tema case, e vem depois por isso.
    Existe porque tema e marca colidem: "lupo" trouxe "Kit 6 Cuecas Lupo Boxer"
    para o grupo de Mulheres, duas vezes, em 09/09/2026. A marca faz calcinha E
    cueca, e nenhum ajuste no tema separa as duas -- so a palavra do produto.

    A lista de exclusao geral (`load_exclude`) nao serve aqui: ela e o que nao
    entra em grupo NENHUM, e cueca no Geral e oferta legitima.
    """

    jid: str
    nome: str
    temas: tuple[str, ...] = ()
    exclui: tuple[str, ...] = ()
    # Dentro da busca propria do grupo, o que entra primeiro. E o mesmo papel
    # que o `priority` global faz no Geral: desconto e preco sozinhos decidem
    # por numero, e o numero maior raramente esta no que define o grupo.
    prioridade: tuple[str, ...] = ()
    # Marca obrigatoria: o titulo tem que citar uma destas ALEM de citar um
    # tema. Faz o que o `exclui` nao faz -- `e_barrada` devolve o item quando
    # ele casa um tema, e aqui o problema e justamente o tema.
    #
    # Medido em 17/09/2026 contra os 22.695 titulos do catalogo: o grupo de
    # Perfumes aceitava 759 produtos e 233 deles nao eram perfume. "natura"
    # esta dentro de "sabor Natural" (85 casos), "arbo" dentro de
    # "percarbonato" (57) e "essencial" e palavra de anuncio de suplemento
    # (27). Nenhum ajuste de tema separa isso: o tema e o nome da marca, e a
    # marca faz shampoo tambem.
    exige: tuple[str, ...] = ()
    # Quantas ofertas este grupo leva por rodada na busca propria dele. 0 =
    # usa o `MAX_POR_GRUPO_TEMATICO`, que vale para todo mundo.
    #
    # Existe porque o dono autorizou volume maior em dois grupos e nao nos
    # outros: "nao tem problema em subir a quantidade de envios nos grupos ...
    # preciso que esses produtos sejam enviados, pois sao os melhores produtos
    # para serem vendidos" (18/09/2026, sobre Mulheres e Perfumes).
    max_por_rodada: int = 0

    @property
    def e_geral(self) -> bool:
        return not self.temas

    def aceita(self, offer: Offer) -> bool:
        if self.exclui and tem_tema(offer.title, list(self.exclui)):
            return False
        if self.exige and not tem_tema(offer.title, list(self.exige)):
            return False
        return self.e_geral or tem_tema(offer.title, list(self.temas))


def load_grupos_fonte(path: Path | None = None) -> list[dict]:
    """Grupos de WhatsApp lidos como fonte de DESCOBERTA.

    O que se aproveita e a pista -- qual produto esta em oferta --, e nao o
    texto nem o link. Ver `sources/grupo_wa` para o porque.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        g for g in (data.get("grupos_fonte") or []) if (g.get("jid") or "").strip()
    ]


def load_grupos(path: Path | None = None) -> list[GrupoDestino]:
    """Grupos de destino do watchlist.json.

    Lista vazia -- que e o estado de quem nao configurou -- devolve um destino
    unico com JID vazio, e `deliver` traduz vazio para o EVOLUTION_GROUP_JID
    do .env. Assim quem nao usa varios grupos nao muda de comportamento, e os
    3.000 posts que ja existem (todos com `grupo_jid` vazio) continuam
    contando no cooldown do grupo principal.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    crus = data.get("grupos") or []

    grupos = []
    for cru in crus:
        # JID vazio e legitimo, e o do grupo principal: significa "o destino
        # padrao do .env". Manter assim, em vez de escrever o JID real, e o
        # que preserva o cooldown -- os 3.063 posts ja enviados tem `grupo_jid`
        # vazio, e trocar o valor faria todos eles sumirem da conta e produtos
        # recem-postados voltarem ao grupo.
        jid = (cru.get("jid") or "").strip()
        if not cru.get("nome"):
            log.warning("Grupo sem nome, com jid %r; fora.", jid)
            continue
        grupos.append(
            GrupoDestino(
                jid=jid,
                nome=(cru.get("nome") or jid).strip(),
                temas=tuple(sem_acento(x) for x in cru.get("temas") or ()),
                exclui=tuple(sem_acento(x) for x in cru.get("exclui") or ()),
                prioridade=tuple(
                    sem_acento(x) for x in cru.get("prioridade") or ()
                ),
                exige=tuple(sem_acento(x) for x in cru.get("exige") or ()),
                max_por_rodada=int(cru.get("max_por_rodada") or 0),
            )
        )

    if not grupos:
        return [GrupoDestino(jid="", nome="principal")]
    return grupos


def load_coupons(path: Path | None = None) -> list[dict]:
    """Cupons de campanha do watchlist.json que ainda estao no prazo.

    Cupom do ML e de campanha -- vale no site por alguns dias --, nao por
    produto. Mas nao vale para TUDO: cada um traz regra propria de categoria,
    valor minimo e teto. Medido em 08/09/2026, o `PAGUEMENOS` publicado dava
    18% so em roupas e acessorios, com minimo de R$ 29.

    Por isso cada cupom carrega o proprio `ate`, os proprios `temas` e o
    proprio `minimo`. Cupom vencido, ou citado num produto onde nao vale, e
    pior do que post sem cupom: a pessoa clica, tenta, falha e passa a
    desconfiar do grupo inteiro.

    Aceita os dois formatos. O antigo era um cupom so:

        "coupon": {"code": "VALEMAIS", "ate": "2026-09-15"}

    O novo e uma lista, porque cupom do ML tem limite de uso e quando um
    estoura o outro ainda pega:

        "coupon": {"codes": [
          {"code": "PAGUEMENOS", "ate": "2026-09-30",
           "temas": ["camiseta", "cueca"], "minimo": 29},
          {"code": "VALEMAIS", "ate": "2026-09-15"}
        ]}
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    bloco = data.get("coupon") or {}

    crus = bloco.get("codes")
    if crus is None:
        # Formato antigo: um cupom solto no proprio bloco.
        crus = [bloco] if bloco.get("code") else []

    validos = []
    hoje = date.today()
    for cupom in crus:
        codigo = (cupom.get("code") or "").strip()
        if not codigo:
            continue

        # Falha FECHADO nos dois eixos, e isto nao e zelo abstrato. Medido em
        # 08/09/2026 sobre os 37 cupons do ML publicados no Promobit: 12 (32%)
        # vinham com o campo de instrucoes literalmente "-", sem regra, sem
        # data, sem minimo -- e um deles era o OFERTASEMPRE, justamente um dos
        # que o grupo concorrente postou.
        #
        # A primeira versao disto tratava ausencia como permissao: sem `ate`
        # nunca vencia, sem `temas` valia para tudo. Com um terco da fonte sem
        # regra nenhuma, esses dois defaults produziam exatamente o post que o
        # cupom deveria evitar -- a pessoa clica, o codigo nao aplica, e a
        # desconfianca sobra para o grupo.
        ate = cupom.get("ate")
        if not ate:
            log.warning(
                "Cupom %s sem `ate`; nao entra. Cupom do ML vence, e alguns em "
                "horas -- sem data nao da para saber se ainda vale.",
                codigo,
            )
            continue
        if date.fromisoformat(ate) < hoje:
            log.info("Cupom %s venceu em %s; nao entra nos posts.", codigo, ate)
            continue

        temas = [sem_acento(t) for t in cupom.get("temas") or []]
        if not temas and not cupom.get("geral"):
            log.warning(
                "Cupom %s sem `temas` e sem `geral: true`; nao entra. Cupom do "
                "ML costuma valer so em algumas categorias, e a fonte publica "
                "quase nunca diz quais.",
                codigo,
            )
            continue

        validos.append(
            {
                "code": codigo,
                "temas": temas,
                "minimo": float(cupom.get("minimo") or 0),
                # Sem estes tres, `_quanto_economiza` devolvia zero e o cupom
                # do watchlist perdia toda disputa de ordem para o do Pelando,
                # que traz os campos. Cupom cadastrado a mao costuma ser o
                # MELHOR -- alguem conferiu --, e ficava por ultimo.
                "desconto": float(cupom.get("desconto") or 0),
                "tipo": (cupom.get("tipo") or "PERCENT").upper(),
                "teto": float(cupom.get("teto") or 0),
            }
        )
    return validos


def cupom_para(offer: Offer, cupons: list[dict]) -> str | None:
    """Os cupons que valem PARA ESTA oferta, prontos para o texto.

    Devolve "COD1 ou COD2" quando mais de um serve. Grupo concorrente faz isso
    e o motivo e real: cupom do ML tem limite de uso, e quando o primeiro
    estoura o segundo ainda pega. A diferenca aqui e que a alternativa so
    aparece se ela de fato se aplicar -- listar tres e torcer e o que produz o
    aviso "se o cupom der erro, tente pela aba anonima".

    So sai em oferta do MERCADO LIVRE. Estes cupons sao de campanha do ML e
    nao existem em outra loja: cita-los num post da Amazon levaria a pessoa a
    tentar um codigo que o checkout de la nunca vai aceitar. A fonte importa
    mais desde que o grupo passou a receber Amazon tambem.

    Sem `temas`, o cupom vale para qualquer produto do ML. Com, o titulo
    precisa citar um deles.
    """
    if offer.source != "mercadolivre":
        return None

    # O cupom que o outro grupo citou junto DESTE produto vem antes de tudo.
    #
    # E a evidencia mais forte que existe de que o codigo serve: alguem que
    # vive disso publicou os dois lado a lado hoje. As outras origens sao
    # inferencia -- o Pelando e comunidade, e a regra por tema acerta a
    # categoria, nao o anuncio.
    #
    # Pedido do dono em 12/09/2026: "quando os grupos mandarem cupons, coloque
    # esses cupons nas nossas publicacoes tambem, pois assim e mais facil de
    # vendermos". Medido no mesmo dia: 36 das 50 mensagens do xet e 13 das 15
    # do Economizei citam cupom.
    deles = list(_CUPONS_DO_OUTRO_GRUPO.get(offer.external_id, ()))

    servem = [c for c in cupons if _serve(offer, c) and c["code"] not in deles]
    if not deles and not servem:
        return None
    if deles:
        # Os deles na frente; os nossos so completam a vaga que sobrar.
        servem.sort(key=lambda c: _quanto_economiza(offer, c), reverse=True)
        codigos = deles + [c["code"] for c in servem]
        return " ou ".join(codigos[:MAX_CUPONS_POR_POST])

    # Ordena pelo que sobra no bolso, e corta em dois. Sem isso o post saia
    # com SEIS codigos numa linha -- medido em producao em 09/09/2026, quando
    # havia seis cupons gerais no ar e todos serviam para todo produto:
    #
    #   TORCIDA ou GLORIA ou PREDATA0809 ou SITETOD0809 ou TUDODEBOM ou VALEMAIS
    #
    # E o defeito que eu apontei no grupo concorrente, que lista tres e torce.
    # Ninguem digita seis codigos: a pessoa tenta o primeiro, falha, desiste.
    servem.sort(key=lambda c: _quanto_economiza(offer, c), reverse=True)
    return " ou ".join(c["code"] for c in servem[:MAX_CUPONS_POR_POST])


# Dois, e nao um: cupom do ML tem limite de uso, e quando o primeiro estoura o
# segundo ainda pega. Mais que isso vira lista que ninguem tenta.
MAX_CUPONS_POR_POST = 2


# O preco anunciado leva o desconto do cupom?
#
# NAO, desde 09/09/2026, e o motivo esta medido. O dono testou no carrinho
# todos os codigos que o bot publicou, e todos acabaram morrendo:
#
#     TORCIDA      funcionou de manha, "esgotou" a noite
#     GLORIA       funcionou de manha, "esgotou" a noite
#     VALEMAIS     "O cupom esgotou"
#     TUDODEBOM    "nao esta mais disponivel"
#     SITETOD0809  "nao esta mais disponivel"
#
# Cupom do ML morre por CONSUMO, nao por validade -- o texto do proprio ML diz
# "esgotou". O limite de usos nao aparece em fonte nenhuma: nem no Pelando,
# nem no painel, nem na API. Entao nao ha como saber, na hora de escrever o
# post, se o codigo ainda tem estoque.
#
# A consequencia: um preco que depende do cupom esta errado na maior parte do
# tempo. "De R$ 159,90 por R$ 79,15" com o cupom morto vira um numero que nao
# existe -- e preco falso e o unico erro que gasta a confianca do grupo de vez.
# O cupom que nao aplica, sozinho, custa uma tentativa frustrada; o preco
# falso custa a credibilidade de todos os outros posts.
#
# Por isso o preco anunciado voltou a ser o do anuncio, que esta sempre certo,
# e o cupom virou o que ele de fato e: uma chance a mais, dita como chance.
PRECO_LEVA_O_CUPOM = False


def preco_com_cupom(offer: Offer, cupons: list[dict]) -> float | None:
    """O preco que a pessoa PAGA, quando da para afirmar isso.

    Devolve None so quando nenhum cupom serve para esta oferta.

    O numero fica ATRELADO ao cupom no texto -- "por R$ 105,61 com o cupom"
    --, e essa amarra e o que o torna verdadeiro. Cupom que vale "em itens
    selecionados" as vezes nao aplica: medido em 09/09/2026, o TORCIDA passou
    num pre-treino e numa progressiva e falhou num whey. Escrito como preco do
    anuncio, o numero seria falso nesses casos; escrito como preco COM o
    cupom, ele descreve a condicao que o proprio post manda cumprir.

    Por isso a instrucao ao copywriter e ao `fallback_copy` exige a expressao
    na mesma linha, e nunca o numero solto.
    """
    if not PRECO_LEVA_O_CUPOM:
        return None

    servem = [c for c in cupons if offer.source == "mercadolivre" and _serve(offer, c)]
    if not servem:
        return None

    # Do que rende mais para o que rende menos, parando no primeiro que
    # produz um preco possivel. Escolher so o melhor e desistir se ele nao
    # servir jogaria fora o segundo, que costuma servir.
    for melhor in sorted(servem, key=lambda c: _quanto_economiza(offer, c), reverse=True):
        economia = _quanto_economiza(offer, melhor)
        if economia <= 0:
            continue

        final = round(offer.price - economia, 2)
        if final > 0:
            return final
        # Preco negativo foi para o grupo em 09/09/2026: o TUDODEBOM, de
        # R$ 250 fixos, caiu num protetor solar de R$ 69,89 e o post anunciou
        # "por R$ -180,11 com o cupom".
        #
        # A causa e a fonte: o slug daquele cupom nao trazia o minimo, entao
        # ele entrou com `minimo: 0` e passou a servir para qualquer preco.
        # Cupom de R$ 250 sem compra minima nao existe -- o numero que faltava
        # era justamente o que impedia isso.
        #
        # Aqui a guarda e sobre o RESULTADO, e nao sobre a causa, porque a
        # causa muda: qualquer cupom que desconte mais do que o produto custa
        # esta descrito errado, venha o erro de onde vier.
        log.warning(
            "Cupom %s desconta R$ %.2f de um produto de R$ %.2f; ignorado. "
            "A regra da fonte esta incompleta.",
            melhor.get("code"),
            economia,
            offer.price,
        )
    return None


def _quanto_economiza(offer: Offer, cupom: dict) -> float:
    """Quantos reais este cupom tira do preco, respeitando o teto.

    E o criterio para escolher entre varios que servem. Ordenar por
    porcentagem seria errado: 25% com teto de R$ 500 vale mais que 30% com
    teto de R$ 20 em quase tudo que o grupo posta.
    """
    desconto = float(cupom.get("desconto") or 0)
    if not desconto:
        return 0.0

    if (cupom.get("tipo") or "PERCENT").upper() == "PERCENT":
        bruto = offer.price * desconto / 100
    else:
        bruto = desconto

    teto = float(cupom.get("teto") or 0)
    return min(bruto, teto) if teto else bruto


def chave_da_fila(
    scored: ScoredOffer, cupons: list[dict], temas: list[str]
) -> tuple:
    """A ordem da fila, em quatro criterios. Maior ganha.

    1. PRIORITARIO COM CUPOM. E a unica oferta que junta as duas coisas que o
       grupo responde -- um tema que ele pediu, e um desconto a mais que
       ninguem ve no anuncio.
    2. QUALQUER oferta com cupom. Cupom e desconto que nao aparece no anuncio:
       quem acha o produto sozinho no ML paga o preco cheio, e so quem esta no
       grupo sabe do codigo. E a unica coisa que este bot publica que a pessoa
       nao acharia por conta propria.
    3. Desconto, como sempre foi.
    4. Frete gratis, so como desempate. O custo do frete e o que some do preco
       anunciado na hora do checkout, mas ele nao pode passar na frente de um
       desconto maior.

    Funcao nomeada, e nao um `lambda` dentro de `run`, porque o teste precisa
    ordenar pela MESMA chave. Quando isto era uma copia no teste, ela ficou
    defasada em uma versao e passou a testar a ordem antiga.
    """
    tem_cupom = bool(cupom_para(scored.offer, cupons))
    premiada = tem_cupom and e_prioritaria(scored.offer, temas)
    return (
        _veio_de_outro_grupo(scored.offer),
        premiada,
        tem_cupom,
        scored.discount_pct,
        scored.offer.free_shipping,
    )


# Produtos vistos em grupo concorrente, nesta rodada. Preenchido por `run` e
# lido pela ordenacao -- vive em modulo, e nao no `Offer`, porque a pista e
# sobre a DESCOBERTA e nao sobre o produto: o mesmo anuncio pode aparecer
# amanha pela vitrine, e ai nao tem nada de especial.
_VISTOS_EM_OUTRO_GRUPO: set[str] = set()

# Cupons que o outro grupo citou na mesma mensagem de cada produto:
# `MLB123... -> ("EXCLUSIVONOMELI",)`. Preenchido por
# `collect_de_outros_grupos` e lido por `cupom_para`.
#
# Fica por produto, e nao numa lista geral, porque o codigo desses grupos e
# quase sempre de marca ou de loja: colar num produto qualquer daria codigo
# que o checkout recusa. O que diz a quem o cupom serve e o anuncio que veio
# junto dele.
_CUPONS_DO_OUTRO_GRUPO: dict[str, tuple[str, ...]] = {}


def _veio_de_outro_grupo(offer: Offer) -> bool:
    """Alguem que vive disso escolheu este produto hoje.

    Vale mais que qualquer sinal nosso e por isso vem primeiro na ordem: um
    grupo de 990 membros que posta ha meses acertou a curadoria antes de nos,
    e o produto ja provou que atrai clique em publico parecido.

    O que se herda e so isso: a pista. O texto e o link continuam sendo
    nossos.

    Desde 09/09/2026 a pista tambem dispensa os pisos de desconto e os
    cooldowns -- ver `_pistas_do_dia`. O dono pediu que tudo que der para
    medir do grupo deles saia no nosso, e a curadoria deles passa a ser o
    filtro no lugar dos nossos numeros.
    """
    return offer.external_id in _VISTOS_EM_OUTRO_GRUPO


def _regra_incompleta(cupom: dict) -> bool:
    """Cupom sem compra minima declarada nao entra.

    Campanha do ML tem minimo. Quando ele nao aparece, o que houve foi o slug
    da fonte omitir o numero -- e o resto da regra provavelmente veio
    incompleto junto.

    Isso comecou como guarda so para o valor FIXO, depois do post com
    R$ -180,11. O dono entao testou os codigos no carrinho, um a um, e o
    minimo previu os cinco casos:

        TORCIDA      funciona   10%       minimo R$ 79
        GLORIA       funciona   10%       minimo R$ 79
        VALEMAIS     esgotado   10%       minimo R$ 0
        TUDODEBOM    morto      R$ 250    minimo R$ 0
        SITETOD0809  morto      R$ 60     minimo R$ 0

    Nao e o tipo que separa os vivos dos mortos, e o minimo. O percentual sem
    minimo tambem falha -- o VALEMAIS respondeu "O cupom esgotou" no checkout
    --, e ele so nao produziu preco absurdo porque 10% de qualquer preco e
    proporcional ao produto.

    Custa alcance: dos oito cupons lidos em 09/09/2026, quatro ficam de fora.
    Vale o custo, porque cupom que nao aplica gasta a confianca do grupo, e a
    confianca e o unico ativo que este projeto tem.
    """
    return float(cupom.get("minimo") or 0) <= 0


def _serve(offer: Offer, cupom: dict) -> bool:
    """Este cupom vale para esta oferta?

    Tres regras, na ordem em que o ML as aplica.

    `categorias` sao IDs do proprio ML (MLB1430 e Calcados, Roupas e Bolsas). E
    a regra oficial da campanha, entao quando o produto TEM categoria ela
    decide sozinha, e nem se consulta o titulo.

    Quando o produto chega SEM categoria, cai para os temas. Isso nao e um
    atalho: e o unico caminho para 89% do que o grupo recebe. A vitrine nao
    devolve categoria em campo nenhum, e as duas vias de conserto estao
    fechadas -- /items/{id} responde 403 (8 de 8) e a pagina do produto vem
    bloqueada. Sem esta segunda via, o cupom alcancaria 34 posts em 2.964.

    Os temas do cupom do painel nao sao chute: saem do NOME das categorias que
    o proprio ML declarou. As vinte do OFERTASEMPRE sao todas roupa -- Camisas,
    Saias, Calcas, Leggings, Ternos, Bermudas e Shorts --, e viram "camisa",
    "saia", "calca", "legging", "terno", "bermuda", "short". Ver
    `enriquecer_com_temas`.
    """
    if _regra_incompleta(cupom):
        return False
    if offer.price < cupom.get("minimo", 0):
        return False

    categorias = cupom.get("categorias") or []
    if categorias and offer.category:
        return offer.category in categorias

    temas = list(cupom.get("temas") or [])
    if categorias and not temas:
        # Cupom de categoria, produto sem categoria e sem tema derivado: nao ha
        # como afirmar que se aplica. Falha fechado.
        return False

    # `restrito` e o "Em itens Selecionados" da pagina do cupom: ele vale num
    # recorte que o ML nao publica. Sem tema e sem categoria, o codigo e a
    # unica pista -- e ele separa dois tipos bem diferentes de cupom.
    #
    # CUPOM DE MARCA. `AVENE15`, `MANTECORP14`, `MAYBELLINENOMELI`. O recorte
    # e a marca, e ela esta no proprio codigo. Medido em 09/09/2026, sem esta
    # regra o grupo recebeu "Aparador de Pelos Kemei, use o cupom AVENE15 ou
    # MANTECORP14" -- dermocosmetico num aparador de pelos. E a queixa que o
    # dono repetiu quatro vezes: o cupom nao pega.
    #
    # CUPOM DE CAMPANHA. `EXCLUSIVONOMELI`, `ITENSDECASA`, `SAINDOBARRATO`,
    # `MELIOFF`. Sao campanhas amplas do ML, e pegam em muita coisa: o grupo
    # vizinho publicou `EXCLUSIVONOMELI` numa camiseta Lupo e funcionou. Estes
    # continuam valendo para qualquer produto.
    #
    # A primeira versao desta regra tratava os dois iguais e recusava os dois
    # quando nao achava marca. Isso zerava o cupom: dos dezesseis codigos no ar
    # em 10/09/2026, NENHUM e de marca -- todos sao campanha. Recusar o que nao
    # se reconhece parece conservador, mas aqui joga fora justamente o cupom que
    # funciona.
    if not temas and cupom.get("restrito"):
        marca = _marca_do_codigo(cupom.get("code") or "")
        if marca:
            temas = [marca]

    return not temas or tem_tema(offer.title, temas)


# Sufixos que o ML gruda no codigo e que nao fazem parte da marca. Ordenados do
# maior para o menor: "NOMELI" precisa sair antes de "MELI", senao
# "MAYBELLINENOMELI" viraria "maybellineno".
_SUFIXOS_DO_CODIGO = ("NOMELI", "NOML", "MELI", "OFF")


def _marca_do_codigo(code: str) -> str:
    """A MARCA escondida no codigo do cupom, ou "" quando o codigo e campanha.

    `AVENE15` -> "avene". `MAYBELLINENOMELI` -> "maybelline".
    `EXCLUSIVONOMELI` -> "" (campanha). `ITENSDECASA` -> "" (campanha).

    A distincao decide o alcance do cupom: com marca ele vale so nos produtos
    dela; sem marca ele e campanha ampla do ML e vale em qualquer um.

    Lista BRANCA, e nao negra, porque os codigos mudam toda semana e a
    esmagadora maioria e campanha. Em 09/09/2026 estavam no ar `TUDOMELI`,
    `OFERTAHOJE` e `SOHOJE`; em 10/09 nenhum desses sobrevivia, e os dezesseis
    novos -- `SAINDOBARRATO`, `OPAECONOMIZEI`, `MELIACHAPROMO` -- eram todos
    inventados no dia. Enumerar o generico seria perseguir um alvo movel;
    enumerar marca funciona porque marca nao se inventa.

    Marca desconhecida vira "" e o cupom passa a valer para tudo. E a escolha
    certa para o erro: campanha tratada como marca some do post, e campanha e
    justamente o cupom que pega.
    """
    limpo = re.sub(r"\d+", "", (code or "").upper())
    for sufixo in _SUFIXOS_DO_CODIGO:
        if limpo.endswith(sufixo) and len(limpo) > len(sufixo):
            limpo = limpo[: -len(sufixo)]
            break

    limpo = limpo.lower()
    return limpo if limpo in _MARCAS_EM_CUPOM else ""


# Marcas que o ML ja usou em codigo de cupom, mais as que a watchlist persegue.
# Uma palavra so, sem numero e sem o sufixo do ML.
#
# Crescer esta lista e barato e seguro: marca a mais so restringe o cupom
# daquela marca. Esquecer uma custa o oposto -- o cupom da marca sai em produto
# que nao e dela, que foi o "AVENE15 num aparador de pelos" de 09/09/2026.
_MARCAS_EM_CUPOM = frozenset(
    {
        # dermocosmetico e farmacia
        "avene", "mantecorp", "cerave", "laroche", "vichy", "neutrogena",
        "eucerin", "bioderma", "sallve", "principia", "adcos", "dermage",
        "nivea", "dove", "episol", "isdin",
        # maquiagem
        "maybelline", "rubyrose", "vult", "payot", "dailus", "eudora",
        "avon", "natura", "boticario", "quemdisseberenice", "oceane",
        "marimaria", "brunatavares", "franciny", "bocarosa",
        # cabelo
        "wella", "kerastase", "loreal", "redken", "cadiveu", "brae",
        "truss", "lolacosmetics", "widicare", "salonline", "haskell",
        "inoar", "amend", "pantene", "elseve", "seda", "tresemme",
        "bioextratus", "nioxin", "joico", "schwarzkopf",
        # esporte e vestuario
        "nike", "adidas", "puma", "olympikus", "mizuno", "asics",
        "newbalance", "fila", "lupo", "hering", "reserva", "colcci",
        # suplemento
        "growth", "maxtitanium", "integralmedica", "probiotica", "dux",
        "atlhetica", "blackskull", "darkness", "yopro", "whey",
        # eletro e casa
        "philips", "mondial", "electrolux", "britania", "arno", "oster",
        "tramontina", "brinox", "cadence", "wap", "philco", "multilaser",
        "samsung", "xiaomi", "motorola", "positivo", "lenovo", "acer",
        "jbl", "sony", "lg", "intelbras", "tplink",
    }
)


# Duas chaves, e nao uma. `hours_since` le a chave que recebe e faz
# `datetime.fromisoformat` no valor -- guardar o JSON dos cupons na mesma
# chave da idade fazia toda leitura estourar com "Invalid isoformat string",
# e a excecao zerava a lista. O sintoma era o pior possivel: nenhum post com
# cupom, e um WARNING que so aparecia no log.
CUPONS_META = "cupons_ml"
CUPONS_LIDOS_EM = "cupons_ml_lidos_em"
CUPONS_HORAS = 3.0

# Quantos dias um cupom do Pelando vale, contados da postagem. O site nao
# publica validade -- a data do slug e quando o usuario postou. Tres dias e a
# defesa: cupom de la sai de circulacao sozinho em vez de ficar sendo
# publicado para sempre com base num palpite.
PELANDO_VALIDADE_DIAS = 3

# Quantas paginas do Pelando abrir por leitura. Sao 121 URLs do ML no sitemap,
# e abrir todas seria 121 requisicoes a um site de terceiro a cada tres horas.
# Os mais recentes sao os que valem: o sitemap vem em ordem de postagem.
PELANDO_LIMITE = 20


def _cupons_do_pelando() -> list[dict]:
    """Cupons do ML publicados no Pelando, ja no formato do watchlist.

    Existe porque o painel do proprio ML nao serve de fonte: ele so mostra o
    cupom DEPOIS que o dono clica em ativar, entao o bot enxergaria apenas o
    que ja foi ativado a mao. Medido em 08/09/2026, o painel dava 1 cupom
    publicavel e o Pelando dava 8.

    Erro aqui e nao-fatal, como em toda fonte de cupom.
    """
    from .sources.pelando import buscar as buscar_pelando

    achados = buscar_pelando(limite=PELANDO_LIMITE)
    return [c.como_dict(PELANDO_VALIDADE_DIAS) for c in achados]


def cupons_vigentes(path: Path | None = None) -> list[dict]:
    """Cupons de tres origens: Pelando, painel do ML, e o watchlist.

    Relidos a cada `CUPONS_HORAS` e guardados no banco. Tres horas porque
    cupom de campanha do ML sai rapido -- alguns valem so no dia --, e uma
    janela maior publicaria codigo morto por horas.

    A ordem das origens e a da confianca. O Pelando e a fonte de volume, mas e
    conteudo de comunidade: o codigo vem de um usuario e a validade e estimada.
    O painel do ML e pouco mas e oficial. O watchlist e o que o dono conferiu
    com as proprias maos.

    Falha nao-fatal em toda ponta: se as duas fontes de rede cairem, valem os
    do watchlist; sem nenhum dos tres, o post sai sem cupom -- que foi o estado
    de todos os 2.953 posts entre 29/08 e 08/09.
    """
    do_painel: list[dict] = []
    try:
        with connect() as conn:
            idade = hours_since(conn, CUPONS_LIDOS_EM)
            guardados = get_meta(conn, CUPONS_META)

            if idade is None or idade >= CUPONS_HORAS:
                from .sources.ml_cupons import (
                    SessionExpired,
                    buscar,
                    enriquecer_com_temas,
                )

                lidos: list[dict] = []
                try:
                    lidos = _cupons_do_pelando()
                except Exception as exc:  # noqa: BLE001 - site de terceiro
                    log.warning("Pelando falhou (%s); sigo com as outras fontes.", exc)

                try:
                    do_ml = enriquecer_com_temas(
                        buscar(MercadoLivreConfig.load().affiliate_cookie)
                    )
                    # O painel manda no empate: mesmo codigo, regra oficial.
                    codigos_oficiais = {c["code"] for c in do_ml}
                    lidos = do_ml + [
                        c for c in lidos if c["code"] not in codigos_oficiais
                    ]
                except SessionExpired as exc:
                    # O cookie cai sozinho de tempos em tempos.
                    log.warning("Painel de cupons do ML: %s", exc)
                except Exception as exc:  # noqa: BLE001 - painel nao e contrato
                    log.warning("Painel de cupons do ML falhou: %s", exc)

                if lidos:
                    do_painel = lidos
                    set_meta(conn, CUPONS_META, json.dumps(do_painel))
                    set_meta(conn, CUPONS_LIDOS_EM, now().isoformat())
                else:
                    do_painel = json.loads(guardados) if guardados else []
            elif guardados:
                do_painel = json.loads(guardados)
    except Exception as exc:  # noqa: BLE001 - cupom nunca derruba a rodada
        log.warning("Nao consegui ler os cupons guardados: %s", exc)

    # O `ate` e reconferido aqui, e nao so na leitura: o cache tem tres horas
    # de vida e um cupom pode vencer dentro dessa janela.
    hoje = date.today()
    frescos = [
        c for c in do_painel if c.get("ate") and date.fromisoformat(c["ate"]) >= hoje
    ]
    if len(frescos) < len(do_painel):
        log.info("%d cupom(ns) venceram desde a ultima leitura.",
                 len(do_painel) - len(frescos))

    return frescos + load_coupons(path)


def load_coupon(path: Path | None = None) -> str | None:
    """O primeiro cupom valido, sem olhar o produto.

    Continua aqui para quem so quer saber se ha campanha no ar. Quem monta
    post deve usar `load_coupons` mais `cupom_para`, que respeitam a regra de
    categoria e de valor minimo de cada cupom.
    """
    cupons = load_coupons(path)
    return cupons[0]["code"] if cupons else None


def load_priority(path: Path | None = None) -> list[str]:
    """Temas com vaga garantida na rodada, normalizados. Campo opcional.

    Sao pedacos de titulo, nao termos de busca: o mesmo shampoo Wella entra na
    carteira pela busca por termo, pelos mais vendidos de `MLB1263` e pela
    vitrine, e a oferta nao carrega qual das tres a trouxe. O titulo carrega.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [sem_acento(tema) for tema in data.get("priority", []) if tema.strip()]


def load_exclude(path: Path | None = None) -> list[str]:
    """Temas BARRADOS na selecao, normalizados. Campo opcional.

    O oposto de `priority`, e nao um teto de preco: e para o produto que sai
    bem no ranking e ainda assim nao serve ao grupo. Mochila e o caso que criou
    isto -- barata, desconto alto, ganha vaga toda rodada, e o grupo recebeu
    dez em oitenta posts.

    A barra nao e absoluta: titulo que tambem casa num tema do `priority`
    passa. E o que separa "Mochila de Viagem 50L" de "Mochila adidas" sem
    precisar de duas listas concorrentes.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [sem_acento(tema) for tema in data.get("exclude", []) if tema.strip()]


def load_vitrine_categories(path: Path | None = None) -> list[tuple[str, int]]:
    """Categorias a pedir da vitrine /ofertas, como (id, paginas).

    Diferente de `load_categories`, que consulta os mais vendidos pela API e
    custa duas chamadas POR PRODUTO. Aqui e uma requisicao por pagina, ~45
    produtos cada, com o "de" e o "por" prontos -- e a fonte mais barata que
    existe no projeto.

    Existe porque a vitrine crua e so o topo da campanha, e marca de roupa nao
    chega la. Ver `MLOfertas.fetch`.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        (entry["id"], int(entry.get("pages", 1)))
        for entry in data.get("vitrine_categories", [])
        if entry.get("id")
    ]


def load_landing_pages(path: Path | None = None) -> list[dict]:
    """Landing pages do ML a ler por rodada.

    Cada entrada: `path` (o caminho no www), `rota` ("A" com preco no card, "B"
    so com link de catalogo) e `prioridade` (o conteudo inteiro da pagina conta
    como prioritario).

    Existe porque o dono mandou dez URLs em 18/09/2026 e pediu que a coleta
    lesse elas. Oito nao dao: todo `lista.mercadolivre.com.br` redireciona para
    `/gz/account-verification` quando quem pede e servidor, e passar disso seria
    burlar deteccao de robo. As seis que responderam estao aqui, e juntas
    trazem ~280 produtos por SEIS requisicoes.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "path": entry["path"],
            "rota": (entry.get("rota") or "A").upper(),
            "prioridade": bool(entry.get("prioridade")),
            # Em quais grupos tematicos o conteudo desta pagina passa na
            # frente. Vazio = em nenhum; a prioridade dela vale so no Geral.
            "grupos": list(entry.get("grupos") or []),
        }
        for entry in data.get("landing_pages", [])
        if entry.get("path")
    ]


def load_vitrine_prioritarias(path: Path | None = None) -> set[str]:
    """As paginas da vitrine cujo conteudo inteiro conta como prioritario.

    Marcadas com `"prioridade": true` no watchlist.json. Existe porque o dono
    mandou as paginas de Perfumes, Maquiagem, Pele e Cabelo em 18/09/2026 e
    pediu prioridade para o que sai delas -- e a prioridade so olhava titulo.

    E uma lista curta de proposito: prioridade que cobre tudo nao escolhe nada.
    Vale a mesma regra do `priority`, medida no mesmo dia -- a fatia do catalogo
    que conta como prioritaria nao pode passar de metade.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        entry["id"]
        for entry in data.get("vitrine_categories", [])
        if entry.get("id") and entry.get("prioridade")
    }


def load_categories(path: Path | None = None) -> list[Category]:
    """Categorias do watchlist.json. Ausente e o normal -- o campo e opcional."""
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        Category(
            id=entry["id"],
            name=entry.get("name", ""),
            max_price=entry.get("max_price"),
        )
        for entry in data.get("categories", [])
    ]


def _rodizio(pool: list, cursor: int, quantos: int) -> tuple[list, int]:
    """`quantos` itens a partir de `cursor`, dando a volta no fim da lista."""
    if not pool or quantos <= 0:
        return [], cursor
    inicio = cursor % len(pool)
    fatia = [pool[(inicio + i) % len(pool)] for i in range(min(quantos, len(pool)))]
    return fatia, (inicio + len(fatia)) % len(pool)


def fatiar_watchlist(
    watchlist: list[Watch],
    temas: list[str],
    por_rodada: int,
    fatia_prioritaria: float,
    cursor_prioritario: int = 0,
    cursor_geral: int = 0,
) -> tuple[list[Watch], int, int]:
    """Os termos desta rodada, e os cursores para a proxima.

    Dois pocos com rodizio proprio, e nao uma lista so. Com um cursor unico, os
    95 termos prioritarios e os 95 gerais se alternariam na mesma volta e o
    prioritario seria reconsultado na mesma frequencia do resto -- que e
    exatamente o que se quer evitar: frequencia de post comeca em frequencia
    de coleta, porque oferta que ninguem reconsultou nao pode ser escolhida.

    `por_rodada` 0 devolve a watchlist inteira, que era o comportamento antes
    de existir fatia.
    """
    if por_rodada <= 0:
        return list(watchlist), cursor_prioritario, cursor_geral

    prioritarios = [w for w in watchlist if tem_tema(w.term, temas)]
    gerais = [w for w in watchlist if not tem_tema(w.term, temas)]

    cota_prioritaria = min(round(por_rodada * fatia_prioritaria), len(prioritarios))
    escolhidos, cursor_prioritario = _rodizio(
        prioritarios, cursor_prioritario, cota_prioritaria
    )
    # A cota que o poco prioritario nao usou volta para o geral em vez de
    # virar rodada menor.
    resto, cursor_geral = _rodizio(gerais, cursor_geral, por_rodada - len(escolhidos))
    return escolhidos + resto, cursor_prioritario, cursor_geral


def build_sources() -> list:
    """Amazon e opcional: sem credencial (ou sem as 3 vendas), segue so com o ML."""
    sources: list = []
    # Os temas prioritarios vao junto porque mudam de qual ANUNCIO a oferta
    # sai (loja oficial em vez do vendedor mais barato), e isso e decidido na
    # fonte, na hora de escolher entre os dezenas de anuncios do produto.
    oficiais = load_priority() if prefer_official_store() else []
    try:
        sources.append(MercadoLivre(MercadoLivreConfig.load(), oficiais))
    except MissingConfig as exc:
        log.warning("Mercado Livre desativado: %s", exc)

    # A vitrine nao precisa de OAuth (e pagina publica), mas reaproveita o
    # Link Builder da fonte de catalogo pra gerar link de afiliado.
    if ofertas_pages() > 0:
        builder = None
        for fonte in sources:
            builder = getattr(fonte, "_link_builder", lambda: None)()
            if builder:
                break
        sources.append(MLOfertas(builder))

    try:
        sources.append(Amazon(AmazonConfig.load()))
    except MissingConfig as exc:
        log.info("Amazon desativada: %s", exc)
        # Sem a Creators API ainda da para POSTAR Amazon: o que os
        # grupos-fonte repassam ja traz produto e preco, e montar o link de
        # afiliado e so a URL canonica com a nossa tag -- nao precisa de API.
        # Sem esta fonte o pipeline levantaria KeyError ao pedir o link de uma
        # oferta `source="amazon"`.
        tag = amazon_partner_tag()
        if tag:
            sources.append(AmazonDoGrupo(tag))
        else:
            log.info("Repasse da Amazon desligado: AMAZON_PARTNER_TAG vazia.")

    if not sources:
        raise MissingConfig("Nenhuma fonte configurada. Preencha o .env.")
    return sources


def collect(
    sources: list,
    watchlist: list[Watch],
    rules: Rules | None = None,
    categories: list[Category] | None = None,
) -> list[Offer]:
    """Descobre produtos novos e reconsulta os que ja conhecemos.

    Duas vias de descoberta, complementares: os termos da watchlist acham o que
    voce sabe que quer, e os mais vendidos por categoria acham o que voce nao
    pensaria em procurar. As duas alimentam o mesmo historico de precos.
    """
    rules = rules or Rules.load()
    vitrine = load_vitrine_categories()
    descobrir = _time_to_discover()
    # Quem sabe precificar por ID de catalogo. A landing da rota B traz link de
    # produto sem preco, e sem esta fonte ela nao tem como virar oferta.
    catalogo = next(
        (s for s in sources if hasattr(s, "fetch_by_ids")), None
    )

    # Fatia desta rodada. Sem fatia (0), a lista inteira, que era o
    # comportamento antes de a rodada de descoberta virar 21 minutos de
    # silencio no grupo.
    categorias = list(categories or [])
    if descobrir:
        with connect() as conn:
            cursor_p = int(get_meta(conn, "discovery_cursor_prioritario") or 0)
            cursor_g = int(get_meta(conn, "discovery_cursor_geral") or 0)
            cursor_c = int(get_meta(conn, "discovery_cursor_categoria") or 0)

        watchlist, cursor_p, cursor_g = fatiar_watchlist(
            watchlist,
            load_priority(),
            discovery_terms_per_round(),
            discovery_priority_share(),
            cursor_p,
            cursor_g,
        )
        if discovery_categories_per_round() > 0:
            categorias, cursor_c = _rodizio(
                categorias, cursor_c, discovery_categories_per_round()
            )

        with connect() as conn:
            set_meta(conn, "discovery_cursor_prioritario", str(cursor_p))
            set_meta(conn, "discovery_cursor_geral", str(cursor_g))
            set_meta(conn, "discovery_cursor_categoria", str(cursor_c))

        if discovery_terms_per_round() > 0:
            log.info(
                "Descoberta desta rodada: %d termos e %d categorias.",
                len(watchlist),
                len(categorias),
            )
    # Descoberta que so levantou excecao nao pode contar como descoberta feita.
    # Ver o carimbo no fim desta funcao.
    #
    # Sao dois sinais, e nao um: "ninguem respondeu" e falha, mas "nao havia o
    # que chamar" (rodada so com a vitrine, que nao busca por termo nem tem
    # categoria) e o funcionamento normal. Tratar os dois como falha encheria
    # o log de aviso em toda rodada sem nada de errado.
    descoberta_tentou = False
    descoberta_respondeu = False
    offers: list[Offer] = []
    for source in sources:
        # Nem toda fonte busca por termo: a vitrine e uma foto do que o ML
        # esta promovendo hoje, sem consulta. Sem esta guarda, cada termo da
        # watchlist virava um WARNING por rodada -- 35 linhas de ruido que
        # escondiam qualquer falha de verdade no meio.
        buscar = getattr(source, "search", None)
        if descobrir and buscar and watchlist:
            descoberta_tentou = True
        for watch in watchlist if (descobrir and buscar) else []:
            try:
                found = buscar(watch.term)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - uma fonte quebrada nao derruba a rodada
                log.warning("%s falhou em '%s': %s", source.name, watch.term, exc)
                continue
            descoberta_respondeu = True
            if watch.max_price is not None:
                found = [o for o in found if o.price <= watch.max_price]
            offers.extend(found)
            log.info("%s: %d ofertas para '%s'", source.name, len(found), watch.term)

        if descobrir:
            if getattr(source, "highlights", None) and categorias:
                descoberta_tentou = True
            achados, respondeu = collect_categories(source, categorias)
            offers.extend(achados)
            descoberta_respondeu = descoberta_respondeu or respondeu
        offers.extend(collect_vitrine(source, vitrine))
        offers.extend(collect_landings(source, catalogo))
        if _due("last_full_refetch", full_refetch_interval_hours()):
            offers.extend(refetch_tracked(source, rules))
            _stamp("last_full_refetch")
        offers.extend(refetch_hot(source))
        offers.extend(collect_de_outros_grupos(source))

    # So carimba se a descoberta realmente aconteceu.
    #
    # Antes o carimbo era incondicional, e isso custou meio dia de coleta: na
    # primeira rodada do servidor o `ml-auth` ainda nao tinha sido feito, cada
    # busca por termo levantou "nao autorizado" -- tratadas uma a uma, com
    # aviso, sem derrubar a rodada -- e o carimbo foi gravado assim mesmo. O
    # bot registrou "descoberta feita" para uma descoberta que nao chamou uma
    # API sequer, e passou a pular a proxima por 12 horas.
    #
    # Falha total nao pode virar sucesso no relogio: sem resposta de ninguem, a
    # proxima rodada tenta de novo em vez de esperar o intervalo inteiro.
    if descobrir and descoberta_respondeu:
        _stamp(DISCOVERY_KEY)
    elif descobrir and descoberta_tentou:
        log.warning(
            "Nenhuma fonte respondeu a descoberta; nao carimbando. "
            "A proxima rodada tenta de novo."
        )
    return offers


DISCOVERY_KEY = "last_discovery_at"


def _iso_now() -> str:
    from .db import now

    return now().isoformat()


def collect_vitrine(source, categories: list[tuple[str, int]] | None = None) -> list[Offer]:
    """Ofertas do dia do ML. Uma requisicao traz ~45 produtos.

    Roda em toda rodada de proposito: e a fonte mais barata que temos, e a
    vitrine gira ao longo do dia. Gravar essas ofertas tambem alimenta o
    historico -- produto que hoje entra como repasse pode, em alguns dias,
    virar oferta verificada pela nossa propria medicao.
    """
    fetch = getattr(source, "fetch", None)
    if fetch is None:
        return []

    # Assinatura inspecionada em vez de `except TypeError`: aquele engoliria um
    # TypeError vindo de DENTRO do fetch e reexecutaria a chamada sem
    # categoria, escondendo o defeito atras de uma coleta pela metade.
    aceita = "categories" in inspect.signature(fetch).parameters

    try:
        found = fetch(ofertas_pages(), categories) if aceita else fetch(ofertas_pages())
    except Exception as exc:  # noqa: BLE001 - HTML muda; nao derruba a rodada
        log.warning("%s falhou na vitrine: %s", source.name, exc)
        return []

    log.info("%s: %d ofertas da vitrine", source.name, len(found))
    return found


def collect_landings(source, catalogo=None) -> list[Offer]:
    """As landing pages do ML que respondem a requisicao de servidor.

    Seis paginas por ~280 produtos, uma requisicao cada. Compare com a busca
    por termo, que gasta uma chamada de descoberta MAIS uma por produto: e a
    mesma economia que faz a vitrine valer a pena.

    Duas rotas, porque o ML monta as paginas diferente. A `A` traz o preco no
    proprio card. A `B` traz so o link do produto de catalogo, e quem precifica
    e `catalogo` -- a mesma fonte que a rodada ja usa para reconsultar produto
    conhecido. Sem ela a rota B e pulada em vez de devolver oferta sem preco.

    Falha de uma pagina nao derruba as outras nem a rodada: landing e HTML, e
    HTML muda.
    """
    paginas = load_landing_pages()
    if not paginas or not hasattr(source, "landing_com_preco"):
        return []

    offers: list[Offer] = []
    for pagina in paginas:
        caminho = pagina["path"]
        try:
            if pagina["rota"] == "B":
                if catalogo is None or not hasattr(catalogo, "fetch_by_ids"):
                    continue
                encontrados = catalogo.fetch_by_ids(
                    [
                        (mlb, titulo, None)
                        for mlb, titulo in source.landing_catalogo(caminho)
                    ]
                )
                offers.extend(
                    replace(o, vitrine_categoria=caminho) for o in encontrados
                )
            else:
                offers.extend(source.landing_com_preco(caminho))
        except Exception as exc:  # noqa: BLE001 - HTML muda; nao derruba a rodada
            log.warning("landing %s falhou: %s", caminho, exc)

    if offers:
        log.info("landings: %d oferta(s) de %d pagina(s)", len(offers), len(paginas))
    return offers


def refetch_hot(source) -> list[Offer]:
    """Nivel rapido: so os produtos colados na propria minima historica.

    A carteira inteira nao cabe num ciclo de minutos -- 400 produtos a cada 15
    min sao 38 mil chamadas por dia. Mas quase nenhum deles pode virar post na
    proxima hora: quem esta 40% acima da propria minima nao vira oferta com
    mais uma queda pequena. Reconsultar so a fatia quente pega a oferta
    relampago pagando por dezenas de produtos, nao por centenas.
    """
    intervalo = hot_interval_minutes()
    fetch = getattr(source, "fetch_by_ids", None)
    if intervalo <= 0 or fetch is None:
        return []
    if not _due("last_hot_refetch", intervalo / 60):
        return []

    with connect() as conn:
        quentes = hot_products(conn, source.name, hot_margin_pct(), hot_track_limit())

    _stamp("last_hot_refetch")
    if not quentes:
        return []

    try:
        found = fetch(quentes)
    except Exception as exc:  # noqa: BLE001 - idem: nao derruba a rodada
        log.warning("%s falhou no ciclo rapido: %s", source.name, exc)
        return []

    log.info("%s: %d produtos quentes reconsultados", source.name, len(found))
    return found


def _offer_da_pista(pista) -> Offer | None:
    """A oferta montada com o que a propria pagina do produto mostrou.

    Existe porque a API de catalogo cobria pouco da fonte: ela so responde por
    produto de catalogo, e a maior parte do que o outro grupo posta e anuncio
    de vendedor. Medido em 09/09/2026, 13 dos 43 links resolvidos tinham preco
    pela API -- os outros 30 morriam sem nunca chegar ao grupo.

    A pagina que o `grupo_wa` ja baixa tem tudo: preco, riscado, foto, titulo e
    o endereco canonico do anuncio. Medido em 12/09/2026, 24 dos 25 links
    viraram oferta completa por aqui, e onde a API tambem respondia os dois
    precos bateram em 5 de 5 -- os outros dois casos divergiram porque a API
    devolve o menor anuncio do catalogo e a pagina devolve o anuncio que eles
    linkaram, que e justamente o preco que o membro deles viu.

    Sem preco nao ha oferta: o resto do funil so sabe comparar numero.
    """
    if not pista.preco or pista.preco <= 0 or not pista.url:
        return None
    return Offer(
        source="mercadolivre",
        external_id=pista.external_id,
        title=pista.titulo,
        price=pista.preco,
        url=pista.url,
        original_price=pista.preco_antes or None,
        image_url=pista.imagem or None,
        condicao=pista.condicao,
    )


def collect_de_outros_grupos(source) -> list[Offer]:
    """Produtos que grupos concorrentes acabaram de postar.

    O produto e o preco vem deles; o texto, o link de afiliado e todos os
    filtros continuam sendo nossos -- ver `sources/grupo_wa`.

    Vale a pena porque a curadoria ja foi feita por quem vive disso: um grupo
    de 990 membros que posta ha meses escolheu aquele produto hoje. Essa e a
    razao de esses produtos irem para o topo da fila em `chave_da_fila`.
    """
    from .config import EvolutionConfig
    from .sources.grupo_wa import pistas

    fontes = load_grupos_fonte()
    # `fetch_by_ids` cobre so o que a pagina nao trouxe, entao a fonte sem ele
    # ainda rende. Antes ele era obrigatorio, porque era o unico jeito de ter
    # preco.
    fetch = getattr(source, "fetch_by_ids", None) or (lambda _alvos: [])
    if not fontes:
        return []
    if not _due("last_grupos_fonte", grupos_fonte_intervalo_horas()):
        return []

    try:
        config = EvolutionConfig.load()
    except MissingConfig:
        # Sem Evolution nao ha o que ler. Nao e erro: quem usa o backend
        # oficial da Meta simplesmente nao tem esta fonte.
        return []

    _stamp("last_grupos_fonte")

    da_amazon: list[Offer] = []
    achadas = []
    for fonte in fontes:
        if fonte.get("tipo") == "pelando":
            # O link deste grupo e do Pelando, nao da loja: ver
            # `sources/pelando_grupo`. Nao ha o que pedir a `pistas` nem a
            # `_amazon_do_grupo`, que so reconhecem link do ML e da Amazon.
            pistas_do_grupo, amazon_do_grupo = _pelando_do_grupo(config, fonte)
            achadas += pistas_do_grupo
            da_amazon += amazon_do_grupo
            continue
        da_amazon += _amazon_do_grupo(config, fonte)
        achadas += pistas(
            base_url=config.base_url,
            instancia=config.instance,
            chave=config.api_key,
            jid=fonte["jid"],
            nome=fonte.get("nome", ""),
            limite_links=int(fonte.get("limite", 15)),
        )
    if not achadas and not da_amazon:
        return []

    # A pista vale para a ORDENACAO desta rodada. Nao vira atributo do
    # produto: o mesmo anuncio pode reaparecer amanha pela vitrine, e ai nao
    # tem nada de especial.
    _VISTOS_EM_OUTRO_GRUPO.update(p.external_id for p in achadas)
    for pista in achadas:
        if pista.cupons:
            _CUPONS_DO_OUTRO_GRUPO[pista.external_id] = pista.cupons

    # A pagina do produto vem primeiro, e a API so cobre o que sobrou.
    #
    # A ordem importa e ja foi a inversa. Enquanto a API vinha primeiro, dois
    # tercos da fonte se perdiam na rota `/products/{id}`, que responde 403
    # para anuncio de vendedor. E onde as duas respondem, a da pagina e a mais
    # fiel ao pedido: e o anuncio que eles linkaram, com o preco que o membro
    # deles viu, e nao o menor anuncio do catalogo.
    da_pagina = [o for o in (_offer_da_pista(p) for p in achadas) if o is not None]
    resolvidos = {o.external_id for o in da_pagina}

    # `fetch_by_ids` quer (external_id, titulo, imagem) -- a mesma tupla que a
    # reconsulta tira do banco. Titulo e imagem vem da pagina do produto, que
    # `pistas` ja baixou; a fonte repassa os dois sem uma segunda chamada.
    #
    # A imagem ia vazia ate 09/09/2026, e o post da pista saia so com texto --
    # 57 dos 90 primeiros. `fetch_by_ids` nao busca foto: ele confia em quem
    # chama, porque na reconsulta normal ela vem do banco.
    #
    # Ja errei essa tupla antes: mandei o ID com o prefixo da fonte no primeiro
    # campo, e cada GET virou `/products/mercadolivre:MLB.../items`. Todos 404,
    # nenhum erro, 15 pistas viraram zero produtos por duas rodadas.
    alvos = [
        (p.external_id, p.titulo, p.imagem or None)
        for p in achadas
        if p.external_id not in resolvidos
    ]
    da_api: list[Offer] = []
    if alvos:
        try:
            da_api = fetch(alvos)
        except Exception as exc:  # noqa: BLE001 - fonte extra nao derruba a rodada
            log.warning("Nao consegui medir os produtos dos outros grupos: %s", exc)

    # O endereco que a pista trouxe vence o que a API monta pelo ID. Para
    # produto de usuario (`/up/MLBU...`) o da pista e o que alguem abriu num
    # navegador, com o nome do produto no caminho; o da API e so o ID.
    urls_da_pista = {
        p.external_id: p.url for p in achadas if p.url and p.external_id not in resolvidos
    }
    da_api = [replace(o, url=urls_da_pista.get(o.external_id, o.url)) for o in da_api]

    found = da_pagina + da_api + da_amazon
    _VISTOS_EM_OUTRO_GRUPO.update(o.external_id for o in da_amazon)
    log.info(
        "Outros grupos: %d pista(s), %d produto(s) no topo da fila "
        "(%d pela pagina, %d pela API, %d da Amazon).",
        len(achadas),
        len(found),
        len(da_pagina),
        len(da_api),
        len(da_amazon),
    )
    return found


def _amazon_do_grupo(config, fonte: dict) -> list[Offer]:
    """As ofertas da Amazon que este grupo-fonte postou, ja como Offer.

    O link que sai daqui e o CANONICO, sem tag: `affiliate_url` poe a nossa
    depois, no mesmo ponto em que poe a do ML. Assim a tag deles nunca chega
    perto do post, nem por engano de copiar e colar.

    O preco vem do texto deles e nao de medicao nossa, entao `verified` fica
    False e `original_price` so aparece quando eles escreveram o "de". A
    `condicao` -- "em 2x", "Programe e Poupe" -- viaja junto porque sem ela o
    numero e falso.
    """
    from .sources.amazon_grupo import ofertas_do_grupo
    from .sources.grupo_wa import ler_mensagens

    if not amazon_partner_tag():
        return []
    try:
        registros = ler_mensagens(
            config.base_url,
            config.instance,
            config.api_key,
            fonte["jid"],
            50,
        )
        ofertas = ofertas_do_grupo(registros, origem=fonte.get("nome", ""))
    except Exception as exc:  # noqa: BLE001 - fonte extra nunca derruba a rodada
        log.warning("Nao consegui ler a Amazon do grupo %s: %s", fonte.get("nome"), exc)
        return []

    return [_offer_da_amazon(o) for o in ofertas]


def _offer_da_amazon(oferta) -> Offer:
    """A `OfertaAmazon` de um grupo-fonte como Offer, sem tag nenhuma na URL."""
    from .sources.amazon_grupo import PRODUTO_CANONICO
    from .sources.amazon_link import imagem_do_asin

    return Offer(
        source="amazon",
        external_id=oferta.asin,
        title=oferta.titulo,
        price=oferta.preco,
        url=PRODUTO_CANONICO.format(asin=oferta.asin),
        original_price=oferta.preco_antes or None,
        image_url=imagem_do_asin(oferta.asin),
        condicao=oferta.condicao,
    )


def _pelando_do_grupo(config, fonte: dict) -> tuple[list, list[Offer]]:
    """(pistas do ML, ofertas da Amazon) de um grupo cujo link e do Pelando.

    A Amazon so entra com a tag nossa configurada, igual ao `_amazon_do_grupo`:
    post sem tag e trabalho de graca para a Amazon. O ML nao depende disso.
    """
    from .sources.grupo_wa import ler_mensagens
    from .sources.pelando_grupo import ofertas_do_grupo

    try:
        registros = ler_mensagens(
            config.base_url,
            config.instance,
            config.api_key,
            fonte["jid"],
            50,
        )
        pistas_ml, ofertas = ofertas_do_grupo(
            registros,
            origem=fonte.get("nome", ""),
            limite_links=int(fonte.get("limite", 15)),
        )
    except Exception as exc:  # noqa: BLE001 - fonte extra nunca derruba a rodada
        log.warning("Nao consegui ler o grupo %s: %s", fonte.get("nome"), exc)
        return [], []

    if not amazon_partner_tag():
        return pistas_ml, []
    return pistas_ml, [_offer_da_amazon(o) for o in ofertas]


def _due(chave: str, intervalo_horas: float) -> bool:
    """Passou tempo suficiente desde a ultima vez que `chave` foi carimbada?"""
    with connect() as conn:
        passadas = hours_since(conn, chave)
    return passadas is None or passadas >= intervalo_horas


def _stamp(chave: str) -> None:
    with connect() as conn:
        set_meta(conn, chave, _iso_now())


def _time_to_discover() -> bool:
    """Descobrir agora, ou so manter o historico do que ja conhecemos?

    A reconsulta roda sempre: e ela que faz a baseline existir. A descoberta e
    que e intermitente, porque e cara e o resultado dela muda devagar.
    """
    intervalo = discovery_interval_hours()
    if intervalo <= 0:
        return True

    with connect() as conn:
        passadas = hours_since(conn, DISCOVERY_KEY)

    if passadas is None:
        return True  # primeira rodada: sem carteira, nao ha o que reconsultar
    if passadas >= intervalo:
        return True

    log.info(
        "Descoberta pulada (rodou ha %.1fh, intervalo %dh). So reconsulta.",
        passadas,
        intervalo,
    )
    return False


# Quantos textos ficam prontos antes de a rodada entregar o primeiro lote.
#
# Tres e o meio termo medido: o primeiro post sai depois de ~3 chamadas do
# Gemini em vez de depois de 25, e ainda ha lote suficiente para o gotejamento
# ter o que drenar entre uma escrita e outra.
_LOTE_DE_ENTREGA = 3


def _quantas_pistas_cabem(na_fila: int, ja_escolhidas: int) -> int:
    """Quantas ofertas dos grupos-fonte entram na frente da fila agora.

    Dois tetos, e vale o menor. O da fila (`MAX_PENDING_QUEUE`) impede que uma
    enxurrada do outro grupo encha a fila de post que expira antes de sair. O
    `MAX_PISTAS_POR_RUN` e outra coisa: limita quanto do volume de uma rodada
    pode ser repasse, porque tirar a pista da cota sem freio multiplicaria o
    volume do grupo por sete de uma vez -- 50 pistas coletadas por rodada
    contra as 6 vagas da cota, medido em 14/09/2026.
    """
    espaco_na_fila = max_pending_queue() - na_fila - ja_escolhidas
    return max(0, min(max_pistas_por_run(), espaco_na_fila))


def _um_por_produto(escolhidas: list[ScoredOffer]) -> list[ScoredOffer]:
    """Deixa so a melhor oferta de cada produto na rodada.

    O cooldown olha o que ja foi para o grupo; isto olha o que esta sendo
    escolhido agora. Sem os dois, os quatro anuncios do mesmo short entravam
    juntos na mesma rodada e o cooldown so pegava a partir da segunda.

    A lista chega ordenada por desconto, entao ficar com a primeira de cada
    produto e ficar com a melhor.

    Compara titulo com titulo, como o cooldown -- ver `db.mesmo_produto`. A
    assinatura de N primeiras palavras que estava aqui antes juntava marcas
    diferentes pelo comeco do titulo, e era ela que impedia dois wheys de
    fabricantes distintos de sairem na mesma rodada.
    """
    saida: list[ScoredOffer] = []
    for scored in escolhidas:
        if any(mesmo_produto(s.offer.title, scored.offer.title) for s in saida):
            continue
        saida.append(scored)
    return saida


def e_prioritaria(offer: Offer, temas: list[str]) -> bool:
    """A oferta e prioritaria: pelo titulo, ou pela pagina de onde veio.

    O titulo sempre valeu e continua valendo. A origem entrou em 18/09/2026,
    quando o dono mandou as paginas de Perfumes, Maquiagem, Cuidados com a Pele
    e Cuidados com o Cabelo e pediu prioridade para o que sai delas.

    Sem a segunda via o pedido nao se cumpre. A prioridade so olhava titulo, e
    titulo de perfume arabe generico -- "Perfume Sedutor Arabe Sabah 100ml", o
    primeiro item da pagina de Perfumes -- nao cita marca nenhuma que esteja na
    lista. O produto vinha da pagina certa e disputava a cota como qualquer
    outro.
    """
    if tem_tema(offer.title, temas):
        return True
    origem = getattr(offer, "vitrine_categoria", "")
    if not origem:
        return False
    if origem in load_vitrine_prioritarias():
        return True
    return any(
        p["path"] == origem and p["prioridade"] for p in load_landing_pages()
    )


def load_comida_e_bebida(path: Path | None = None) -> tuple[list[str], list[str]]:
    """(termos de comida e bebida, termos que desmentem), normalizados.

    Campo opcional. Os termos sao palavras inteiras do titulo, e os que
    desmentem sao o que impede "Maquina de Cafe" e "Taca de Vinho" de contar:
    o produto cita a bebida e nao e a bebida.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    termos = [sem_acento(t) for t in data.get("comida_e_bebida", []) if t.strip()]
    nao_e = [sem_acento(t) for t in data.get("nao_e_comida", []) if t.strip()]
    return termos, nao_e


@functools.lru_cache(maxsize=8)
def _palavras(termos: tuple[str, ...]) -> re.Pattern | None:
    """Os termos como palavras inteiras, com plural.

    Palavra inteira, e nao pedaco de titulo como o `priority`: "gin" e "mel"
    estao em "ginastica" e "melhor". O plural entra porque o titulo diz
    "Cervejas" e "Biscoitos" tanto quanto diz o singular.
    """
    if not termos:
        return None
    alternativas = "|".join(re.escape(t) for t in sorted(termos, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternativas})(?:s|es)?(?![a-z0-9])")


def e_comida_ou_bebida(
    offer: Offer, termos: list[str] | None = None, nao_e: list[str] | None = None
) -> bool:
    """O titulo e de comida ou bebida.

    Sem argumentos le o watchlist. Passar as listas existe para quem chama em
    laco -- ler o arquivo por oferta seria ler um JSON de 100 KB cinquenta
    vezes por rodada -- e para o teste.
    """
    if termos is None or nao_e is None:
        termos, nao_e = load_comida_e_bebida()
    titulo = sem_acento(offer.title)
    quer = _palavras(tuple(termos))
    if quer is None or not quer.search(titulo):
        return False
    desmente = _palavras(tuple(nao_e))
    return not (desmente and desmente.search(titulo))


def _prioridade_do_post(offer: Offer) -> int:
    """Qual faixa da fila de entrega o post ocupa. Maior sai antes.

    2 -- comida e bebida de outro grupo. Pedido do dono em 02/10/2026: tem que
         aparecer. Sai na frente de tudo, inclusive do resto do repasse.
    1 -- o resto do repasse: a pista sai na frente do que nos medimos.
    0 -- o que nos escolhemos.

    Sem a faixa 2, a vodka entrava em disputa com cinquenta ofertas de
    prioridade 1 por seis vagas do Geral por hora, e metade delas morria na
    fila: medido em 72h, 71 ofertas da Amazon repassadas expiraram contra 71
    enviadas.
    """
    if not _veio_de_outro_grupo(offer):
        return 0
    return 2 if e_comida_ou_bebida(offer) else 1


def _admitir_pistas(
    novas: list[ScoredOffer], na_fila: int, ja_escolhidas: int
) -> list[ScoredOffer]:
    """Quais ofertas dos grupos-fonte entram nesta rodada, na ordem de entrada.

    Comida e bebida vao primeiro e NAO dependem da folga da fila: o teto
    `MAX_PENDING_QUEUE` e contrapressao, e ela barrava a vodka. Elas ocupam a
    folga antes do resto, e o freio delas e `MAX_COMIDA_E_BEBIDA_POR_RODADA`.
    O resto continua cabendo so no que sobrar.
    """
    termos, nao_e = load_comida_e_bebida()
    comida = [s for s in novas if e_comida_ou_bebida(s.offer, termos, nao_e)]
    comida = comida[: max_comida_e_bebida_por_run()]
    ids = {s.offer.product_id for s in comida}
    resto = [s for s in novas if s.offer.product_id not in ids]
    cabem = _quantas_pistas_cabem(na_fila, ja_escolhidas + len(comida))
    return comida + resto[:cabem]


def e_barrada(offer: Offer, barrados: list[str], temas: list[str]) -> bool:
    """O titulo cai na lista de exclusao -- e nao e salvo por um tema.

    A ordem importa: barrar primeiro e perguntar pela marca depois faria
    "Mochila adidas" sumir junto com "Mochila de Viagem". A marca tem
    precedencia porque a exclusao e sobre o TIPO de produto, nao sobre a peca.
    """
    if not barrados or not tem_tema(offer.title, barrados):
        return False
    return not tem_tema(offer.title, temas)


def _priorizar_temas(
    escolhidas: list[ScoredOffer], limite: int, temas: list[str] | None = None
) -> list[ScoredOffer]:
    """Vagas guardadas para os temas do `priority`, antes do resto da fila.

    Desconto e preco sozinhos decidem por numero, e o numero maior costuma vir
    de outra categoria: na primeira rodada com a watchlist de 132 termos os 252
    produtos novos de cabelo e pele nao pegaram nenhuma das 5 vagas -- elas
    ficaram com -72%, -67% e -54%. Nao havia nada errado com essas ofertas; o
    que faltava era publico, e publico nao aparece na ordenacao.

    A reserva e teto, nao piso: sem candidato prioritario na rodada, nenhuma
    vaga fica vazia -- o resto da fila preenche na ordem de sempre. E dentro da
    reserva vale a mesma regra de preco e de rodizio por fonte do resto, entao
    isto muda quem concorre por uma vaga, nao o criterio que decide.
    """
    reserva = min(priority_reserve(), limite)
    if reserva <= 0:
        return _priorizar_baratos(escolhidas, limite)

    temas = load_priority() if temas is None else temas
    if not temas:
        return _priorizar_baratos(escolhidas, limite)

    prioritarias = [s for s in escolhidas if e_prioritaria(s.offer, temas)]
    resto = [s for s in escolhidas if not e_prioritaria(s.offer, temas)]

    # Dentro da reserva o foco em preco pode ser desligado: quando o tema
    # inteiro foi escolhido por converter, o item caro dele nao deve perder a
    # vaga para o barato do mesmo tema. O rodizio por fonte continua valendo.
    if priority_ignores_price_focus():
        saida = _intercalar_por_fonte(prioritarias, reserva)
    else:
        saida = _priorizar_baratos(prioritarias, reserva)
    if saida:
        log.info("%d vaga(s) desta rodada foram para tema prioritario.", len(saida))
    saida += _priorizar_baratos(resto, limite - len(saida))
    # Reserva sobrando nao pode virar vaga perdida nem no outro sentido: se o
    # resto nao encheu, o prioritario que ficou de fora volta a concorrer.
    if len(saida) < limite:
        ja = {id(s) for s in saida}
        sobra = [s for s in prioritarias if id(s) not in ja]
        saida += _priorizar_baratos(sobra, limite - len(saida))
    return saida


def regras_do_tema(offer: Offer, rules: Rules, temas: list[str]) -> Rules:
    """As regras que valem para esta oferta: o piso do tema, ou o geral.

    So o piso de desconto muda. O cooldown de repeticao continua o mesmo de
    proposito -- "sempre que aparecer" nao pode virar o mesmo shampoo a cada
    rodada, que e o que faz sair do grupo justamente quem o tema queria trazer.
    """
    piso = priority_min_discount_pct()
    if piso is None or not e_prioritaria(offer, temas):
        return rules
    return replace(rules, min_discount_pct=piso)


def _priorizar_baratos(
    escolhidas: list[ScoredOffer], limite: int
) -> list[ScoredOffer]:
    """Vagas primeiro para o que esta na faixa de foco, sem excluir o resto.

    Produto caro nao e barrado -- e uma queda real num item de R$ 3.000
    continua sendo real. O que muda e a ordem de servico: quem le grupo de
    oferta decide um item de R$ 100 na hora e um de R$ 1.000 em dias, e nesses
    dias sai do grupo, pesquisa e compra por outro link.

    `PRICE_FOCUS_RESERVE` guarda vagas abertas a qualquer preco. Sem elas, um
    dia com muitos candidatos baratos empurraria o produto caro para fora
    todas as vezes, e o grupo perderia justamente a variedade que segura
    audiencia.

    Dentro de cada faixa o rodizio por fonte continua valendo.
    """
    teto = price_focus_max()
    if teto <= 0:
        return _intercalar_por_fonte(escolhidas, limite)

    dentro = [s for s in escolhidas if s.offer.price <= teto]
    fora = [s for s in escolhidas if s.offer.price > teto]

    reserva = min(price_focus_reserve(), limite) if fora else 0
    saida = _intercalar_por_fonte(dentro, limite - reserva)
    # A reserva nao pode virar vaga perdida: sobrou espaco, o caro completa, e
    # se nao houver caro suficiente o barato volta a preencher.
    saida += _intercalar_por_fonte(fora, limite - len(saida))
    if len(saida) < limite:
        ja = {id(s) for s in saida}
        resto = [s for s in dentro if id(s) not in ja]
        saida += _intercalar_por_fonte(resto, limite - len(saida))
    return saida


def _intercalar_por_fonte(
    escolhidas: list[ScoredOffer], limite: int
) -> list[ScoredOffer]:
    """Distribui as vagas entre as fontes, a melhor de cada uma por vez.

    Ordenar so por desconto entregava as cinco vagas a vitrine todas as vezes,
    e nao por ela ter oferta melhor: o desconto do repasse e medido contra o
    preco riscado, que quem escolhe e a loja. Quanto mais inflado o "de", mais
    alto o produto subia. A ordenacao premiava exatamente a pratica que a
    landing acusa -- e o grupo recebia cinco eletronicos por rodada enquanto
    perfume, roupa e o resto do catalogo nunca chegavam.

    O rodizio nao deixa vaga vazia: quando uma fonte acaba, as outras seguem
    preenchendo. Com uma fonte so, o resultado e identico ao corte simples.

    A ordem por desconto e preservada DENTRO de cada fonte -- continua saindo
    a melhor oferta de cada uma, so nao as cinco melhores da mesma.
    """
    if limite <= 0:
        return []

    filas: dict[str, list[ScoredOffer]] = {}
    for scored in escolhidas:
        filas.setdefault(scored.offer.source, []).append(scored)

    saida: list[ScoredOffer] = []
    while len(saida) < limite and any(filas.values()):
        for fila in filas.values():
            if len(saida) >= limite:
                break
            if fila:
                saida.append(fila.pop(0))
    return saida


def collect_categories(
    source, categories: list[Category]
) -> tuple[list[Offer], bool]:
    """Mais vendidos das categorias acompanhadas.

    Devolve as ofertas e se ALGUMA categoria respondeu. O segundo valor existe
    para o `collect()` nao carimbar a descoberta quando ninguem respondeu --
    lista vazia por falha e lista vazia por nao haver oferta boa hoje sao
    situacoes opostas, e sem esse sinal as duas ficam iguais.

    Fonte sem `highlights` (Amazon) e ignorada em silencio -- categoria e
    conceito do ML, nao um recurso que toda loja precise ter. Ela tambem nao
    conta como resposta: nunca houve chamada.
    """
    destaques = getattr(source, "highlights", None)
    if destaques is None or not categories:
        return [], False

    offers: list[Offer] = []
    respondeu = False
    for category in categories:
        rotulo = category.name or category.id
        try:
            found = destaques(category.id)
        except Exception as exc:  # noqa: BLE001 - idem: nao derruba a rodada
            log.warning("%s falhou na categoria '%s': %s", source.name, rotulo, exc)
            continue
        respondeu = True
        if category.max_price is not None:
            found = [o for o in found if o.price <= category.max_price]
        offers.extend(found)
        log.info("%s: %d mais vendidos em '%s'", source.name, len(found), rotulo)
    return offers, respondeu


def refetch_tracked(source, rules: Rules) -> list[Offer]:
    """Reconsulta por ID os produtos ja no banco.

    Sem isso o historico so avanca enquanto o produto continuar no top 50 da
    busca, e quase nenhum chega aos MIN_OBSERVATIONS dias exigidos pelo filtro.

    Sem max_price aqui de proposito: o produto ja passou pelo teto quando foi
    descoberto, e se o preco subiu acima dele isso e justamente a informacao
    que faz a baseline valer.
    """
    fetch = getattr(source, "fetch_by_ids", None)
    if fetch is None:
        return []  # fonte sem multiget (Amazon)

    limit = track_limit()
    with connect() as conn:
        tracked = tracked_products(conn, source.name, rules.baseline_window_days, limit)

    if not tracked:
        return []
    if len(tracked) == limit:
        log.warning(
            "%s: teto de %d produtos reconsultados atingido; o resto fica sem "
            "observacao nesta rodada (suba ML_TRACK_LIMIT)",
            source.name,
            limit,
        )

    try:
        found = fetch(tracked)
    except Exception as exc:  # noqa: BLE001 - idem: nao derruba a rodada
        log.warning(
            "%s falhou ao reconsultar %d produtos: %s", source.name, len(tracked), exc
        )
        return []

    log.info(
        "%s: %d de %d produtos reconsultados", source.name, len(found), len(tracked)
    )
    return found


def run(dry_run: bool = False) -> list[ScoredOffer]:
    rules = Rules.load()
    sources = build_sources()
    by_name = {source.name: source for source in sources}

    # Drenar ANTES de coletar, e nao so no fim da rodada.
    #
    # A coleta e a entrega sao seriais dentro da rodada, entao o tempo de
    # coleta e silencio no grupo quando a fila ja tem post pronto. Medido em
    # 01/09/2026, numa rodada que fez descoberta completa mais reconsulta da
    # carteira inteira: 191 termos em 14 min, 34 categorias em 2,5 min, 900
    # produtos reconsultados em 3,5 min -- 22 minutos ate a primeira mensagem,
    # com a fila esperando o tempo todo.
    #
    # O orcamento de gotejamento continua sendo um so para a rodada inteira; o
    # que a drenagem de agora gastar sai do que a do fim tem para gastar.
    orcamento = run_interval_seconds() * 0.8
    gasto = 0.0
    if not dry_run:
        try:
            gasto = flush_pending(orcamento)
        except MissingConfig:
            # Entrega mal configurada tem que parar o daemon alto, e nao virar
            # coleta que roda para sempre sem nunca postar. O worker trata.
            raise
        except Exception as exc:  # noqa: BLE001
            # Qualquer outra falha na entrega nao pode custar a coleta: o
            # historico de preco continua valendo mesmo com o WhatsApp fora, e
            # e ele que faz a oferta virar verificada depois.
            log.warning("Drenagem antes da coleta falhou: %s", exc)

    offers = collect(sources, load_watchlist(), rules, load_categories())

    picked: list[ScoredOffer] = []
    with connect() as conn:
        for offer in offers:
            record_offer(conn, offer)

        # Deduplica mantendo o menor preco por produto nesta rodada.
        unique: dict[str, Offer] = {}
        for offer in offers:
            current = unique.get(offer.product_id)
            if current is None or offer.price < current.price:
                unique[offer.product_id] = offer

        # Verificadas primeiro: sao as unicas que podem afirmar que o preco
        # caiu de verdade, e sao o motivo de alguem preferir este grupo.
        temas = load_priority()
        for offer in unique.values():
            scored = score(conn, offer, regras_do_tema(offer, rules, temas))
            if scored is not None:
                picked.append(scored)

        # Repasse aceita qualquer fonte que traga o preco riscado, e nao so a
        # vitrine.
        #
        # A trava em `ml_ofertas` foi escrita quando o catalogo praticamente
        # nao devolvia `original_price` -- 213 vazios em 218, o numero que o
        # ml_ofertas.py registra. Isso mudou: medido no servidor, 88 dos 223
        # produtos vindos da API tem preco riscado, 39,5%, e 71 deles com 15%
        # de desconto ou mais.
        #
        # Enquanto a trava existiu, TODA rodada saia com cinco itens da
        # vitrine, e perfume, roupa e suplemento -- ja no catalogo, ja com
        # desconto -- nunca chegavam ao grupo.
        #
        # O que protege contra repasse ruim nao e a fonte: e o
        # `score_campaign`, que exige preco riscado maior que o atual, o
        # desconto minimo e o cooldown. E o post continua saindo marcado
        # `verified=False`, entao nada aqui afirma medicao que nao houve.
        ja_escolhido = {s.offer.product_id for s in picked}
        repasses: list[ScoredOffer] = []
        for offer in unique.values():
            if offer.product_id in ja_escolhido:
                continue
            scored = score_campaign(conn, offer, regras_do_tema(offer, rules, temas))
            if scored is not None:
                repasses.append(scored)

        # O que veio dos grupos-fonte sai da cota, mesmo tendo preco riscado.
        #
        # A faixa prioritaria existe para o pedido do dono: o que o xet e o
        # Economizei postam tem que sair no nosso grupo. So que ela nascia so do
        # `score_pista`, a porta de quem NAO tem preco riscado -- e depois a
        # pista passou a ler o riscado da propria pagina, que o `score_campaign`
        # aceita. O efeito foi silencioso: a pista voltou a disputar as vagas da
        # cota e a faixa esvaziou. Medido em 14/09/2026, 50 pistas coletadas e
        # UMA na frente da fila.
        #
        # Separar por ORIGEM, e nao pela porta que pontuou, e o que faz a faixa
        # voltar a descrever o pedido. O desconto medido pelo `score_campaign`
        # nao se perde: ele viaja no ScoredOffer e ordena quem entra primeiro
        # quando o teto aperta.
        com_riscado_deles = [s for s in repasses if _veio_de_outro_grupo(s.offer)]
        com_riscado_deles.sort(key=lambda s: s.discount_pct, reverse=True)
        repasses = [s for s in repasses if not _veio_de_outro_grupo(s.offer)]

        # A terceira porta, so para o que o grupo vizinho postou agora.
        #
        # Sem ela a fonte quase nao rendia: a pista e produto novo no nosso
        # radar, entao `score` nao tem historico, e `score_campaign` so aceita
        # quem tem preco riscado -- medido em 09/09/2026, 9 de 13. As outras
        # viravam so coleta e talvez saissem dias depois.
        #
        # Fica por ultimo de proposito: quem passou nas duas primeiras ja saiu
        # com desconto medido ou riscado, que e post melhor.
        ja_escolhido |= {s.offer.product_id for s in repasses + com_riscado_deles}
        pistas_agora: list[ScoredOffer] = []
        for offer in unique.values():
            if offer.product_id in ja_escolhido:
                continue
            if not _veio_de_outro_grupo(offer):
                continue
            scored = score_pista(conn, offer, regras_do_tema(offer, rules, temas))
            if scored is not None:
                pistas_agora.append(scored)

        # Quem tem riscado na frente: o post mostra "De X por Y" em vez de so o
        # preco, e quando o teto corta a fila esse e o corte certo a se fazer.
        pistas_agora = com_riscado_deles + pistas_agora

        # O que sai porque ELES postaram sai uma vez so, para sempre.
        #
        # O cooldown por si nao resolve este caso. A fonte rele as mesmas 50
        # mensagens a cada rodada, entao a mesma oferta volta a ser "nova"
        # assim que o cooldown vence -- e um post que fica dias no grupo deles
        # renderia um post nosso por dia, indefinidamente. Foi a queixa do
        # dono em 12/09/2026, junto com a camisa da Reserva e a bicicleta
        # ergometrica.
        #
        # Vale so para o que NAO e medicao nossa: `score` afirma que o preco
        # caiu contra o nosso historico, e essa e razao propria para postar de
        # novo, venha o produto de onde vier. `score_campaign` e `score_pista`
        # nao tem razao propria nenhuma -- a razao e que eles postaram.
        de_fora = [
            s
            for s in repasses + pistas_agora
            if _veio_de_outro_grupo(s.offer)
        ]
        if de_fora:
            ja_saiu = produtos_ja_postados(
                conn, [s.offer.product_id for s in de_fora]
            )
            if ja_saiu:
                repasses = [
                    s
                    for s in repasses
                    if not (
                        _veio_de_outro_grupo(s.offer)
                        and s.offer.product_id in ja_saiu
                    )
                ]
                pistas_agora = [
                    s for s in pistas_agora if s.offer.product_id not in ja_saiu
                ]
                log.info(
                    "%d oferta(s) do outro grupo fora: o grupo ja recebeu esse "
                    "produto antes.",
                    len(ja_saiu),
                )

    # A ordem tem tres criterios, do que mais manda para o que so desempata.
    #
    # 1. PRIORITARIO COM CUPOM vai na frente de tudo. E a unica oferta que
    #    junta as duas coisas que o grupo responde -- um tema que ele pediu, e
    #    um desconto a mais que ninguem ve no anuncio. Sao raros por
    #    construcao: o cupom cobre 20 categorias, exige minimo, e so 24,8% da
    #    carteira tem `category` (a vitrine, que e 89% do que sai, nao devolve
    #    esse campo). Medido em 08/09/2026, 34 de 2.964 posts enviados teriam
    #    recebido cupom. Quando um aparece, ele nao pode perder a vaga para
    #    mais um desconto grande de um produto qualquer.
    #
    # 2. QUALQUER oferta com cupom vem depois dela, antes do resto. Cupom e
    #    desconto que nao aparece no anuncio: quem ve o produto no ML paga o
    #    preco cheio, e so quem esta no grupo sabe do codigo. E a unica coisa
    #    que este bot publica que a pessoa nao acharia sozinha.
    #
    # 3. Desconto, como sempre foi.
    #
    # 4. Frete gratis DESEMPATA, e nao ordena. O custo do frete e o que some do
    #    preco anunciado na hora do checkout -- oferta boa com frete de R$ 25 e
    #    pior que oferta media com frete zero, e o grupo so descobre depois de
    #    clicar. Medido em 06/09/2026, 74,3% da vitrine tem frete gratis, entao
    #    o desempate tem material de sobra sem virar filtro.
    cupons = cupons_vigentes()

    def ordem(s: ScoredOffer) -> tuple:
        return chave_da_fila(s, cupons, temas)

    picked.sort(key=ordem, reverse=True)
    repasses.sort(key=ordem, reverse=True)

    premiadas = sum(
        1
        for s in picked + repasses
        if cupom_para(s.offer, cupons) and e_prioritaria(s.offer, temas)
    )
    if premiadas:
        log.info(
            "%d oferta(s) prioritaria(s) COM cupom foram para o topo da fila.",
            premiadas,
        )

    # Contrapressao: a coleta produz mais rapido do que a entrega gotejada
    # drena. Sem teto, a fila vira um deposito e o grupo passa a receber oferta
    # de horas atras -- que pode nem existir mais no preco anunciado.
    with connect() as conn:
        # Expira antes de contar: post velho vai ser descartado no flush desta
        # mesma rodada, e deixa-lo no numero faria a fila parecer cheia e
        # recusar oferta nova que tinha lugar.
        expire_stale_posts(conn)
        na_fila = len(pending_posts(conn))
        bloqueados = products_in_cooldown(conn)
        categorias_bloqueadas = categories_in_cooldown(conn)
        # Titulos inteiros, e nao assinaturas: a comparacao acontece em
        # `mesmo_produto`, que ve a marca onde quer que ela esteja no titulo.
        recentes = titulos_recentes(conn, family_cooldown_minutes())

    # Fora antes de escolher, e nao depois: assim o produto repetido nao ocupa
    # uma vaga da cota que outra oferta poderia usar, e nao gasta chamada do
    # Gemini nem link de afiliado para virar post que nao pode sair.
    #
    # A vitrine devolve o mesmo item rodada apos rodada enquanto ele seguir em
    # promocao, e o filtro aprova de novo -- o desconto continua real. Quem le
    # o grupo, porem, ve a mesma oferta duas vezes em quinze minutos.
    #
    # A familia entra junto porque o mesmo produto fisico aparece no ML como
    # dezenas de anuncios de vendedores diferentes: `product_id` distinto,
    # produto igual. O grupo recebeu quatro "Short Saia Esportivo Ausare" em
    # quinze minutos, cada um de um anuncio, cada um com um preco.
    # A exclusao vale para TUDO, pista inclusive: ela nao e um filtro de
    # qualidade que a curadoria do outro grupo possa substituir, e a lista de
    # coisas que o dono nao quer no grupo -- celular, por exemplo.
    barrados = load_exclude()
    if barrados:
        antes = len(picked) + len(repasses) + len(pistas_agora)
        picked = [s for s in picked if not e_barrada(s.offer, barrados, temas)]
        repasses = [s for s in repasses if not e_barrada(s.offer, barrados, temas)]
        pistas_agora = [
            s for s in pistas_agora if not e_barrada(s.offer, barrados, temas)
        ]
        fora = antes - len(picked) - len(repasses) - len(pistas_agora)
        if fora:
            log.info("%d oferta(s) fora pela lista de exclusao.", fora)

    if bloqueados or recentes or categorias_bloqueadas:
        antes = len(picked) + len(repasses)

        def passa(s: ScoredOffer) -> bool:
            if s.offer.product_id in bloqueados:
                return False
            # Mesmo nome e mesma marca so voltam no dia seguinte. Marca
            # diferente passa: quatro wheys de quatro fabricantes sao quatro
            # ofertas, e so a assinatura curta os via como uma.
            if any(mesmo_produto(t, s.offer.title) for t in recentes):
                return False
            return (
                familia_do_titulo(s.offer.title, CATEGORIA)
                not in categorias_bloqueadas
            )

        picked = [s for s in picked if passa(s)]
        repasses = [s for s in repasses if passa(s)]

        # A pista fura o cooldown de CATEGORIA -- ele barra produtos diferentes
        # so por se parecerem, e a escolha de mandar dois perfumes seguidos
        # passa a ser do grupo vizinho.
        #
        # A trava do MESMO produto continua, e ela nao e negociavel aqui: a
        # fonte rele as mesmas 50 mensagens a cada rodada, entao a mesma pista
        # reaparece de 15 em 15 minutos. Sem esta linha o grupo receberia o
        # mesmo item a manha inteira -- que nao e "toda vez que eles mandarem",
        # e sim toda vez que NOS lermos.
        #
        # A trava de nome e marca tambem vale para ela, e pelo mesmo motivo do
        # resto: eles postam o mesmo whey em dois anuncios no mesmo dia.
        pistas_agora = [
            s
            for s in pistas_agora
            if s.offer.product_id not in bloqueados
            and not any(mesmo_produto(t, s.offer.title) for t in recentes)
        ]

        repetidos = antes - len(picked) - len(repasses)
        if repetidos:
            log.info(
                "%d oferta(s) fora por repeticao: mesmo produto nos ultimos "
                "%d min, mesmo nome e marca nos ultimos %d min, ou ainda na "
                "fila.",
                repetidos,
                post_cooldown_minutes(),
                family_cooldown_minutes(),
            )

    # Na janela de silencio a entrega anda devagar; produzir no ritmo normal so
    # encheria a fila de post que morre como `expired` antes de amanhecer.
    teto = rules.max_offers_per_run
    if em_horario_silencioso():
        teto = min(teto, quiet_max_offers_per_run())
        log.info("Horario silencioso: aceitando no maximo %d oferta(s).", teto)

    espaco = max(0, min(teto, max_pending_queue() - na_fila))
    if espaco < teto:
        log.info(
            "Fila com %d post(s); aceitando so mais %d nesta rodada.", na_fila, espaco
        )

    # Repasse preenche o que sobrou da cota, nunca desloca uma verificada. A
    # ordem entre as duas listas continua sendo essa; o que muda dentro de cada
    # uma e de qual fonte vem cada vaga.
    # Um por produto antes de cortar a cota: assim o anuncio repetido nao
    # ocupa vaga que outro produto poderia usar.
    picked = _priorizar_temas(_um_por_produto(picked), espaco, temas)
    if len(picked) < espaco:
        restantes = [
            s
            for s in _um_por_produto(repasses)
            if not any(
                mesmo_produto(j.offer.title, s.offer.title) for j in picked
            )
        ]
        picked += _priorizar_temas(restantes, espaco - len(picked), temas)

    # A pista entra FORA da cota e na FRENTE, nao dentro dela.
    #
    # Dentro, ela competiria com as nossas medidas por seis vagas e perderia
    # quase sempre -- `chave_da_fila` ordena por desconto, e a pista nao tem
    # desconto nenhum para mostrar. Foi o pedido do dono em 09/09/2026:
    # prioridade total para o que o grupo vizinho postar.
    #
    # Quanto cabe aqui e `_quantas_pistas_cabem`, que cruza o espaco da fila
    # com o teto de repasse por rodada.
    if pistas_agora:
        ja = {s.offer.product_id for s in picked}
        novas = [s for s in pistas_agora if s.offer.product_id not in ja]

        # Um por produto, pela mesma comparacao de titulo do resto.
        #
        # E o pedido do dono: quatro perfumes de uma vez pode, desde que sejam
        # perfumes diferentes -- e whey varias vezes tambem, desde que sejam
        # marcas diferentes. O que esta linha barra e o MESMO produto vindo de
        # anuncios de vendedores diferentes, que e o caso que o ML produz aos
        # montes e que o leitor le como repeticao.
        novas = _um_por_produto(novas)

        admitidas = _admitir_pistas(novas, na_fila, len(picked))
        if len(novas) > len(admitidas):
            log.info(
                "%d pista(s) do outro grupo ficaram de fora: entram %d nesta "
                "rodada (fila com %d, teto %d).",
                len(novas) - len(admitidas),
                len(admitidas),
                na_fila,
                max_pending_queue(),
            )
        picked = admitidas + picked
        if admitidas:
            termos, nao_e = load_comida_e_bebida()
            na_frente = sum(1 for s in admitidas if e_comida_ou_bebida(s.offer, termos, nao_e))
            log.info(
                "%d oferta(s) do outro grupo na frente da fila, fora da cota "
                "(%d de comida ou bebida).",
                len(admitidas),
                na_frente,
            )

    verificadas = sum(1 for s in picked if s.verified)
    log.info(
        "%d ofertas selecionadas (%d verificadas, %d repasse da vitrine)",
        len(picked),
        verificadas,
        len(picked) - verificadas,
    )

    if not picked:
        # Nada novo nao quer dizer nada a enviar: a fila e persistente, e o que
        # sobrou da rodada anterior continua valendo. Sair aqui deixava post
        # pronto parado ate alguma rodada futura selecionar alguma coisa -- e
        # rodada sem selecao e comum, acontece toda vez que a vitrine esgota no
        # cooldown de repeticao.
        if not dry_run:
            deliver([], max(0.0, orcamento - gasto))
        return []

    copywriter = Copywriter()
    # `cupons` ja veio da ordenacao la em cima -- a mesma lista, porque
    # reconsultar aqui poderia mudar o que a fila prometeu enquanto ordenava.
    with connect() as conn:
        recentes = recent_headlines(conn)
    drafts: list[tuple[ScoredOffer, str]] = []
    sem_link: list[ScoredOffer] = []
    # Escritos mas ainda nao entregues. Ver `_LOTE_DE_ENTREGA`.
    lote: list[tuple[ScoredOffer, str]] = []

    for scored in picked:
        link = by_name[scored.offer.source].affiliate_url(scored.offer)
        if not link:
            # Sem link de afiliado o post nao rende nada, e nao da pra montar
            # um: o ML so emite pelo Link Builder. Segura a oferta em vez de
            # queimar ela num post sem comissao.
            sem_link.append(scored)
            continue
        cupom = cupom_para(scored.offer, cupons)
        final = preco_com_cupom(scored.offer, cupons)
        try:
            text = copywriter.write(scored, link, cupom, recentes, final)
        except Exception as exc:  # noqa: BLE001 - sem IA ainda da pra postar
            log.warning("Gemini falhou, usando texto padrao: %s", exc)
            text = fallback_copy(scored, link, cupom, final)
        # Depois dos dois caminhos, nao dentro de cada um: o convite nao depende
        # do Gemini ter funcionado, e post de fallback e justamente o que mais
        # circula em rodada ruim.
        text = com_convite(text)
        # Alimenta a proxima chamada desta mesma rodada: sem isso as 5 ofertas
        # do lote saem com a mesma formula, que e o caso mais visivel de todos.
        recentes.append(text.strip().splitlines()[0].strip())
        drafts.append((scored, text))
        lote.append((scored, text))

        # Entrega o lote em vez de esperar os 25 textos ficarem prontos.
        #
        # O `deliver` ficava depois do laco inteiro, o que era irrelevante
        # enquanto a rodada selecionava 6. Com o teto do repasse removido em
        # 16/09/2026 ela passou a selecionar 25, e o Gemini leva de 10 a 40s
        # por texto: o grupo ficava mudo por VARIOS MINUTOS depois de a
        # selecao terminar, esperando o ultimo texto de uma fila que ele nem
        # ia drenar na mesma rodada.
        #
        # Entregar de tres em tres corta essa espera para o tempo de tres
        # textos. Nao muda quantos posts saem -- isso continua sendo o
        # orcamento de gotejamento, que e dividido entre os lotes.
        if not dry_run and len(lote) >= _LOTE_DE_ENTREGA:
            gasto += deliver(lote, max(0.0, orcamento - gasto))
            lote = []

    if sem_link:
        log.warning(
            "%d oferta(s) seguraram por falta de link de afiliado. "
            "Gere no Link Builder e rode `promo link`:",
            len(sem_link),
        )
        for scored in sem_link:
            log.warning("  %s  %s", scored.offer.external_id, scored.offer.url)

    exclusivos = _rodada_dos_grupos(unique, rules, temas, copywriter, by_name)

    if dry_run:
        for _, text in drafts:
            print("\n" + "-" * 40 + "\n" + text)
        for grupo, scored, text in exclusivos:
            print("\n" + "-" * 40 + f" [{grupo.nome}]\n" + text)
        return picked

    if exclusivos:
        with connect() as conn:
            for grupo, scored, text in exclusivos:
                _gravar_post(conn, scored, text, grupo.jid)

    # O que sobrou do ultimo lote, mais a drenagem final. Chamado mesmo com
    # `lote` vazio: e o que drena os posts dos grupos tematicos gravados acima
    # e o que tiver ficado de rodadas anteriores.
    deliver(lote, max(0.0, orcamento - gasto))
    return picked


def _rodada_dos_grupos(
    unique: dict,
    rules: Rules,
    temas: list[str],
    copywriter,
    by_name: dict,
) -> list[tuple[GrupoDestino, ScoredOffer, str]]:
    """A busca propria de cada grupo tematico, que NAO passa pelo Geral.

    O grupo tematico recebe por duas vias. A primeira ja existia: toda oferta
    escolhida para o Geral tambem cai nos grupos cujo tema ela cita -- Wella
    sai no Geral e no Mulheres. A segunda e esta, e existe porque a primeira
    entrega pouco: a cota do Geral e disputada pelo catalogo inteiro, e o que
    ganha e o maior desconto, que raramente e perfume ou air fryer.

    O que sai daqui fica SO no grupo dele, por decisao do dono em 16/09/2026.
    Mandar tambem para o Geral somaria os quatro grupos no principal, que e o
    oposto de "nao poluir o grupo principal" -- e o contrario do corte de
    volume pedido no mesmo dia.

    Nao ha coleta nova: os candidatos saem do `unique` que a rodada ja montou.
    Os temas do grupo so escolhem diferente dentro do mesmo catalogo, entao
    isto nao custa chamada nenhuma de API -- so o Gemini de cada post.

    Cooldown, repeticao e cota sao contados POR GRUPO: `grupo_jid` separa as
    contas desde que o grupo de Mulheres existe, entao o que saiu no Geral nao
    impede o mesmo produto de sair no Esportes, e vice-versa.
    """
    limite = max_por_grupo_tematico()
    if limite <= 0:
        return []

    todos = load_grupos()
    grupos = [g for g in todos if not g.e_geral and g.jid]
    if not grupos:
        return []

    geral = next((g for g in todos if g.e_geral), None)
    saida: list[tuple[GrupoDestino, ScoredOffer, str]] = []
    para_o_geral = 0

    for grupo in grupos:
        # Cota do proprio grupo quando ele tem uma; senao a de todo mundo.
        cota = grupo.max_por_rodada or limite
        escolhidas = _escolhe_para_o_grupo(unique, grupo, rules, temas, cota)
        if not escolhidas:
            continue
        for scored in escolhidas:
            fonte = by_name.get(scored.offer.source)
            link = fonte.affiliate_url(scored.offer) if fonte else ""
            if not link:
                continue
            try:
                text = copywriter.write(scored, link, None, None, None)
            except Exception as exc:  # noqa: BLE001 - sem IA ainda da pra postar
                log.warning("Gemini falhou no grupo %s: %s", grupo.nome, exc)
                text = fallback_copy(scored, link)
            text = com_convite(text)
            saida.append((grupo, scored, text))

            # O achado do grupo tematico tambem sai no Geral quando ele casa a
            # prioridade DO GERAL.
            #
            # Isto reverte a regra de 16/09/2026 ("fica so no grupo dele"), a
            # pedido do dono no mesmo dia: o Geral e onde esta o publico, e o
            # revezamento da fila derrubou a frequencia dele. Reverter inteiro
            # somaria os quatro grupos no principal; o filtro pela prioridade
            # do Geral e o que separa "mais volume" de "tudo".
            #
            # Mesmo texto, sem segunda chamada do Gemini: e o mesmo produto
            # pelo mesmo preco, e reescrever pagaria duas vezes para dizer a
            # mesma coisa.
            if geral is not None and _cabe_no_geral(scored, temas):
                saida.append((geral, scored, text))
                para_o_geral += 1

        log.info(
            "%s: %d oferta(s) pela busca propria.", grupo.nome, len(escolhidas)
        )

    if para_o_geral:
        log.info(
            "%d dessas tambem saem no Geral: casam a prioridade dele.",
            para_o_geral,
        )
    return saida


def _cabe_no_geral(scored: ScoredOffer, temas: list[str]) -> bool:
    """O achado de um grupo tematico tambem serve ao Geral?

    Duas condicoes, e as duas importam. A prioridade do Geral e o filtro que o
    dono pediu -- sem ele o Geral viraria a soma dos quatro grupos. O cooldown
    do Geral e o de sempre: o produto pode nunca ter saido no Esportes e ja ter
    saido no Geral hoje, porque as contas sao separadas por `grupo_jid`.
    """
    if not e_prioritaria(scored.offer, temas):
        return False

    with connect() as conn:
        if scored.offer.product_id in products_in_cooldown(conn, grupo_jid=""):
            return False
        recentes = titulos_recentes(conn, family_cooldown_minutes(), "")

    return not any(mesmo_produto(t, scored.offer.title) for t in recentes)


def origens_por_grupo(path: Path | None = None) -> dict[str, list[str]]:
    """De cada origem, em quais grupos tematicos ela passa na frente.

    Origem e a pagina de onde o produto veio: um ID de categoria da vitrine
    ("MLB1248") ou um caminho de landing ("/e/beleza-premium"). As duas fontes
    declaram a mesma coisa do mesmo jeito, entao ficam no mesmo mapa.

    Nasceu em 18/09/2026 de duas medidas. A primeira: das quatro categorias de
    beleza, so a de Maquiagem traz maquiagem -- 35 de 48 itens, 43 deles com
    preco riscado --, enquanto Perfumes, Pele e Cabelo trazem ZERO. A segunda:
    essa prioridade valia so no Geral, e dentro de Mulheres a lista casa por
    TITULO, entao a maquiagem da categoria certa disputava a vaga como
    qualquer outra coisa.
    """
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    mapa: dict[str, list[str]] = {}
    for entry in data.get("vitrine_categories", []):
        if entry.get("id") and entry.get("grupos"):
            mapa[entry["id"]] = list(entry["grupos"])
    for entry in data.get("landing_pages", []):
        if entry.get("path") and entry.get("grupos"):
            mapa[entry["path"]] = list(entry["grupos"])
    return mapa


def prioritaria_no_grupo(offer: Offer, grupo: GrupoDestino) -> bool:
    """A oferta e prioritaria PARA ESTE GRUPO: pelo titulo, ou pela pagina.

    O titulo sempre valeu. A pagina entrou em 18/09/2026, quando o dono pediu
    que o Beleza Premium fosse prioridade em Mulheres e Perfumes: "sao os
    melhores produtos para serem vendidos".

    Sem a segunda via o pedido nao se cumpre, e pela mesma razao de sempre --
    "Gel Kerastase Curl Manifesto Gelee Curl Contour" nao cita nenhum alvo da
    lista do grupo, e a lista casa por titulo. O produto vinha da pagina que o
    dono escolheu a dedo e disputava a vaga como qualquer outro.

    Quais grupos cada pagina prioriza esta no `grupos` do `landing_pages`: a
    pagina de beleza prioriza Mulheres e Perfumes, nao o Casa.
    """
    if grupo.prioridade and tem_tema(offer.title, list(grupo.prioridade)):
        return True

    origem = getattr(offer, "vitrine_categoria", "")
    if not origem:
        return False
    return any(
        grupo.nome.endswith(nome)
        for nome in origens_por_grupo().get(origem, ())
    )


def _escolhe_para_o_grupo(
    unique: dict,
    grupo: GrupoDestino,
    rules: Rules,
    temas: list[str],
    limite: int,
) -> list[ScoredOffer]:
    """Ate `limite` ofertas do catalogo da rodada que servem a este grupo.

    Passa pelas mesmas tres portas do Geral, na mesma ordem: medicao nossa
    primeiro, preco riscado depois, pista do outro grupo por ultimo. O que muda
    e so quem concorre -- so entra o que o `grupo.aceita` deixa entrar.

    Parte das vagas e disputada so pela prioridade do grupo, e e isso que faz a
    lista de prioridade valer alguma coisa. Ordenar sem reservar nao bastava: as
    portas correm em sequencia, entao um item qualquer que passa pela primeira
    leva a vaga de um prioritario que so passaria pela segunda. Em Perfumes isso
    e a regra -- perfume importado quase nunca tem baseline medida por nos.
    """
    candidatas = [o for o in unique.values() if grupo.aceita(o)]
    if not candidatas:
        return []

    # A prioridade do grupo na frente, a ordem de chegada preservada dentro de
    # cada metade. Sem isto o grupo de Casa encheria de organizador de gaveta
    # com 70% off e a air fryer -- o motivo de alguem entrar no grupo -- ficaria
    # de fora por ter "so" 30%. Mesmo raciocinio do `priority` no Geral.
    tem_prioridade = bool(grupo.prioridade) or any(
        grupo.nome.endswith(nome)
        for nomes in origens_por_grupo().values()
        for nome in nomes
    )
    if tem_prioridade:
        candidatas.sort(key=lambda o: not prioritaria_no_grupo(o, grupo))

    reserva = (
        min(reserva_de_prioridade_do_grupo(), limite) if tem_prioridade else 0
    )

    with connect() as conn:
        bloqueados = products_in_cooldown(conn, grupo_jid=grupo.jid)
        recentes = titulos_recentes(conn, family_cooldown_minutes(), grupo.jid)

        escolhidas: list[ScoredOffer] = []

        def preenche(elegiveis: list[Offer], teto: int) -> None:
            for porta in (score, score_campaign, score_pista):
                for offer in elegiveis:
                    if len(escolhidas) >= teto:
                        break
                    if offer.product_id in bloqueados:
                        continue
                    if any(s.offer.product_id == offer.product_id for s in escolhidas):
                        continue
                    titulo = offer.title
                    if any(mesmo_produto(t, titulo) for t in recentes):
                        continue
                    if any(mesmo_produto(s.offer.title, titulo) for s in escolhidas):
                        continue
                    scored = porta(conn, offer, regras_do_tema(offer, rules, temas))
                    if scored is not None:
                        escolhidas.append(scored)
                if len(escolhidas) >= teto:
                    break

        if reserva > 0:
            prioritarias = [
                o for o in candidatas if prioritaria_no_grupo(o, grupo)
            ]
            preenche(prioritarias, reserva)
            if escolhidas:
                log.info(
                    "%s: %d vaga(s) da reserva foram para a prioridade do grupo.",
                    grupo.nome,
                    len(escolhidas),
                )

        # Reserva sobrando nao vira vaga perdida: o resto da fila preenche o que
        # faltar, e o prioritario que ficou de fora continua na frente dela.
        preenche(candidatas, limite)

    return escolhidas[:limite]


def em_horario_silencioso(agora: datetime | None = None) -> bool:
    """A hora atual cai na janela de silencio?

    A janela normal cruza a meia-noite (23:30 as 07:30), entao a comparacao
    inverte quando o inicio e maior que o fim: dentro dela e "depois do inicio
    OU antes do fim", nao "entre os dois".

    Compara hora local de proposito. O que importa e a hora de quem le no
    grupo, e o Dockerfile fixa TZ=America/Sao_Paulo no container.
    """
    janela = quiet_window()
    if janela is None:
        return False

    inicio, fim = janela
    agora_hora = (agora or datetime.now()).time()

    if inicio <= fim:
        return inicio <= agora_hora < fim
    return agora_hora >= inicio or agora_hora < fim


def _drip_gap() -> float:
    """Intervalo ate o proximo post, com variacao.

    O jitter importa: intervalo exato de 150s em 150s e assinatura de robo, e
    o numero que assina os posts e um chip pareado por cliente nao oficial.
    Os grupos reais mandam a cada 2-3 minutos, sem regularidade nenhuma.

    Dentro da janela de silencio o intervalo estica pelo multiplicador. Com o
    padrao (6) ele passa do orcamento de drenagem de uma rodada, entao sai um
    post por rodada em vez de cinco.
    """
    base = drip_interval_seconds()
    if em_horario_silencioso():
        base *= quiet_drip_multiplier()
    return base * random.uniform(1 - DRIP_JITTER, 1 + DRIP_JITTER)


# Variacao aplicada ao intervalo: 0.4 = de 60% a 140% do valor configurado.
DRIP_JITTER = 0.4


def deliver(
    drafts: list[tuple[ScoredOffer, str]],
    budget_seconds: float | None = None,
    grupos: list[GrupoDestino] | None = None,
) -> float:
    """Enfileira os posts novos e drena a fila (incluindo o que sobrou de antes).

    Chamada mesmo com `drafts` vazio, de proposito: sem isso uma rodada que nao
    selecionou nada deixava a fila anterior parada ate alguma rodada selecionar
    -- o grupo mudo com post pronto esperando.

    Cada rascunho vira UM post por grupo que o aceita. Uma oferta de Wella vai
    para o Geral e para o de Mulheres; um monitor gamer, so para o Geral. O
    texto e o mesmo -- foi escrito uma vez, e reescrever por grupo dobraria a
    conta do Gemini para dizer a mesma coisa.

    O cooldown ja foi conferido por grupo la em cima, entao aqui e so gravar.
    """
    grupos = grupos or load_grupos()
    with connect() as conn:
        for scored, text in drafts:
            destinos = [g for g in grupos if g.aceita(scored.offer)]
            if not destinos:
                # Nao deveria acontecer -- a selecao ja filtrou --, mas se
                # acontecer o post some em silencio, e um log barato evita
                # caçar isso depois.
                log.warning(
                    "Nenhum grupo aceita %r; post descartado.", scored.offer.title[:50]
                )
                continue
            for grupo in destinos:
                _gravar_post(conn, scored, text, grupo.jid)
    return flush_pending(budget_seconds)


def _gravar_post(conn, scored: ScoredOffer, text: str, grupo_jid: str) -> None:
    """Um post pendente, para um grupo."""
    create_post(
        conn,
        scored.offer.product_id,
        scored.offer.price,
        scored.baseline,
        scored.discount_pct,
        text,
        scored.offer.image_url,
        # Sem isso a distincao morria aqui: o site lia repasse da vitrine como
        # desconto medido por nos.
        verified=scored.verified,
        grupo_jid=grupo_jid,
        # A pista passa na frente do que ja esta na fila. Sem isso ela herdaria
        # a posicao pelo `created_at` e esperaria a fila inteira drenar a 120 s
        # por post -- ate uma hora com a fila cheia, para uma oferta que vale
        # porque acabou de ser postada.
        prioridade=_prioridade_do_post(scored.offer),
    )


def _reveza_por_grupo(
    queue: list[tuple], vagas_do_geral: int | None = None
) -> list[tuple]:
    """A fila reordenada para os grupos se revezarem, um post de cada por vez.

    `pending_posts` ordena por `prioridade DESC, created_at`, o que era a ordem
    certa enquanto existia um grupo so. Com cinco, ela vira fome: o repasse do
    outro grupo entra com prioridade 1 e ocupa a frente inteira, e o que sobra
    sai por ordem de chegada -- e os posts dos grupos tematicos sao criados por
    ultimo na rodada, depois dos do Geral.

    O efeito estava medido em 16/09/2026, antes desta funcao existir: o grupo
    de Mulheres teve 49 posts enviados e 49 EXPIRADOS em 24 horas. Metade do
    que era escolhido para ele morria na fila sem nunca sair.

    O revezamento nao muda quantos posts cabem no orcamento -- isso e o
    `DRIP_INTERVAL_SECONDS`. Muda de quem sao: em vez de o Geral levar os sete
    de uma rodada, cada grupo leva a vez dele.

    Dentro de cada grupo a ordem original e preservada, entao prioridade e
    chegada continuam valendo onde ainda fazem sentido.

    `vagas_do_geral` corta a fila do Geral antes do revezamento: o que passar do
    teto da hora fica pendente e concorre de novo na proxima drenagem. Cortar
    aqui, e nao na selecao, e o unico jeito de o teto valer -- o Geral tem sobra
    de post pendente, entao tirar candidato so troca qual dos dele sai.
    """
    if not queue:
        return []

    por_grupo: dict[str, list[tuple]] = {}
    for item in queue:
        por_grupo.setdefault(item[3] or "", []).append(item)

    if vagas_do_geral is not None and "" in por_grupo:
        cortados = len(por_grupo[""]) - max(0, vagas_do_geral)
        por_grupo[""] = por_grupo[""][: max(0, vagas_do_geral)]
        if not por_grupo[""]:
            del por_grupo[""]
        if cortados > 0:
            log.info(
                "%d post(s) do Geral ficam para depois: teto de %d por hora.",
                cortados,
                max_do_geral_por_hora(),
            )

    if not por_grupo:
        return []

    if len(por_grupo) == 1:
        return next(iter(por_grupo.values()))

    saida: list[tuple] = []
    filas = list(por_grupo.values())
    while filas:
        for fila in list(filas):
            saida.append(fila.pop(0))
            if not fila:
                filas.remove(fila)
    return saida


def _vagas_do_geral(conn) -> int | None:
    """Quantas vagas ainda cabem no Geral nesta hora, ou None quando nao ha teto.

    Lido do banco a cada drenagem de proposito. Com a entrega em lotes a rodada
    chama `flush_pending` varias vezes, e cada chamada tem que enxergar o que as
    anteriores ja mandaram -- senao o teto valeria por lote e o total seria o
    teto multiplicado pelo numero de lotes.
    """
    teto = max_do_geral_por_hora()
    if teto <= 0:
        return None
    return max(0, teto - enviados_na_janela(conn, "", 60))


def flush_pending(
    budget_seconds: float | None = None,
    sleep=time.sleep,
    monotonic=time.monotonic,
) -> float:
    """Drena a fila aos poucos, um post de cada vez.

    Nao e enfeite. Dez mensagens seguidas no mesmo segundo tem dois problemas:
    o grupo le como flood e silencia a conversa, e o padrao -- rajada perfeita,
    intervalo zero -- e exatamente o que o antifraude da Meta procura num
    numero pareado por Baileys. Os grupos que funcionam postam de 2 em 3
    minutos, com intervalo irregular.

    `budget_seconds` limita quanto tempo a drenagem segura a rodada. O que nao
    couber fica na fila e sai na proxima -- por isso o daemon acorda a cada 15
    minutos em vez de a cada 2 horas.

    sleep/monotonic sao injetaveis pra o teste nao dormir de verdade.

    Devolve os segundos gastos gotejando. Quem chama duas vezes na mesma
    rodada precisa disso para dividir um orcamento so entre as duas: sem a
    conta, drenar antes E depois da coleta dobraria o tempo de gotejamento e a
    rodada invadiria a seguinte.
    """
    inicio = monotonic()
    delivery = build_delivery()

    with connect() as conn:
        # Antes de drenar, joga fora o que envelheceu. O texto na fila carrega
        # um preco com hora; passado o prazo ele deixa de ser medicao e passa a
        # ser chute -- e chute e o que o grupo existe para nao receber.
        velhos = expire_stale_posts(conn)
        queue = _reveza_por_grupo(
            [
                (row["id"], row["copy"], row["image_url"], row["grupo_jid"])
                for row in pending_posts(conn)
            ],
            _vagas_do_geral(conn),
        )

    if velhos:
        log.warning(
            "%d post(s) descartados por preco velho (mais de %d min na fila). "
            "Fila produzindo mais rapido do que o gotejamento drena.",
            len(velhos),
            post_max_age_minutes(),
        )

    if not queue:
        log.info("Nada pendente na fila.")
        return monotonic() - inicio

    if budget_seconds is None:
        # Sobra de proposito: a drenagem nao pode invadir a proxima rodada.
        budget_seconds = run_interval_seconds() * 0.8

    enviados = 0

    for index, (post_id, text, image_url, grupo_jid) in enumerate(queue):
        if enviados:
            # Decidir ANTES de dormir. Checar so o tempo ja gasto deixava a
            # drenagem estourar o orcamento por um intervalo inteiro -- com
            # gap de 150s isso invadia a rodada seguinte do daemon.
            espera = _drip_gap()
            if monotonic() - inicio + espera >= budget_seconds:
                log.info(
                    "Orcamento de %.0fs esgotado; %d post(s) ficam pra proxima rodada.",
                    budget_seconds,
                    len(queue) - index,
                )
                return monotonic() - inicio
            log.info("Aguardando %.0fs antes do proximo post.", espera)
            sleep(espera)

        try:
            # `or None` porque post antigo tem JID vazio, e vazio quer dizer
            # "o destino padrao do .env".
            delivery.send_post(text, image_url, grupo_jid or None)
        except WindowClosed:
            # So o backend 'cloud' chega aqui. Fora da janela de 24h so passa
            # template: pinga voce pedindo uma resposta qualquer, o que reabre a
            # janela pra proxima rodada (ou `promo flush`) drenar o resto.
            remaining = len(queue) - index
            log.warning(
                "Janela de 24h fechada, %d na fila. Enviando template.", remaining
            )
            delivery.send_ping_template(remaining)
            return monotonic() - inicio
        except NotConnected as exc:
            # So o backend 'evolution'. Reparar exige o QR na mao, entao insistir
            # nos outros posts da fila so gastaria tentativa a toa -- eles ficam
            # 'pending' e saem quando o numero voltar.
            log.error(
                "Instancia da Evolution caiu, %d post(s) ficam na fila: %s",
                len(queue) - index,
                exc,
            )
            return monotonic() - inicio
        except Exception as exc:  # noqa: BLE001
            log.error("Falha ao enviar post %d: %s", post_id, exc)
            with connect() as conn:
                mark_post_failed(conn, post_id, str(exc))
            continue

        with connect() as conn:
            mark_post_sent(conn, post_id)
        enviados += 1

    return monotonic() - inicio

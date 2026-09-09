"""Coleta -> registra historico -> filtra -> escreve -> entrega."""

from __future__ import annotations

import inspect
import json
import logging
import random
import time
from datetime import date, datetime
from dataclasses import dataclass, replace
from pathlib import Path

from .config import (
    AmazonConfig,
    MercadoLivreConfig,
    MissingConfig,
    ROOT,
    Rules,
    discovery_interval_hours,
    drip_interval_seconds,
    full_refetch_interval_hours,
    hot_interval_minutes,
    hot_margin_pct,
    hot_track_limit,
    category_cooldown_minutes,
    max_pending_queue,
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
    run_interval_seconds,
    track_limit,
)
from .copywriter import Copywriter, fallback_copy
from .db import (
    CATEGORIA,
    categories_in_cooldown,
    connect,
    create_post,
    expire_stale_posts,
    familia_do_titulo,
    families_in_cooldown,
    get_meta,
    mark_post_failed,
    mark_post_sent,
    hot_products,
    hours_since,
    now,
    pending_posts,
    products_in_cooldown,
    recent_headlines,
    record_offer,
    sem_acento,
    set_meta,
    tem_tema,
    tracked_products,
)
from .delivery import NotConnected, WindowClosed, build_delivery
from .models import Offer, ScoredOffer
from .scoring import score, score_campaign
from .sources.amazon import Amazon
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
    """

    jid: str
    nome: str
    temas: tuple[str, ...] = ()

    @property
    def e_geral(self) -> bool:
        return not self.temas

    def aceita(self, offer: Offer) -> bool:
        return self.e_geral or tem_tema(offer.title, list(self.temas))


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
    servem = [c for c in cupons if _serve(offer, c)]
    if not servem:
        return None

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
    return (premiada, tem_cupom, scored.discount_pct, scored.offer.free_shipping)


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

    temas = cupom.get("temas") or []
    if categorias and not temas:
        # Cupom de categoria, produto sem categoria e sem tema derivado: nao ha
        # como afirmar que se aplica. Falha fechado.
        return False
    return not temas or tem_tema(offer.title, temas)


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
        if _due("last_full_refetch", full_refetch_interval_hours()):
            offers.extend(refetch_tracked(source, rules))
            _stamp("last_full_refetch")
        offers.extend(refetch_hot(source))

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


def _uma_por_familia(
    escolhidas: list[ScoredOffer], palavras: int = 3
) -> list[ScoredOffer]:
    """Deixa so a melhor oferta de cada familia de produto na rodada.

    O cooldown por familia olha o que ja foi para o grupo; isto olha o que esta
    sendo escolhido agora. Sem os dois, os quatro anuncios do mesmo short
    entravam juntos na mesma rodada e o cooldown so pegava a partir da segunda.

    A lista chega ordenada por desconto, entao ficar com a primeira de cada
    familia e ficar com a melhor.

    Com `palavras=CATEGORIA` a assinatura fica grossa e a regra vira "um item
    de cada TIPO por rodada" -- o que impede a rajada de tres tenis adidas
    saindo juntos, que a assinatura de tres palavras nao ve.
    """
    vistas: set[str] = set()
    saida: list[ScoredOffer] = []
    for scored in escolhidas:
        familia = familia_do_titulo(scored.offer.title, palavras)
        if familia and familia in vistas:
            continue
        if familia:
            vistas.add(familia)
        saida.append(scored)
    return saida


def e_prioritaria(offer: Offer, temas: list[str]) -> bool:
    """O titulo cita algum dos temas que o grupo pediu."""
    return tem_tema(offer.title, temas)


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
        familias_bloqueadas = families_in_cooldown(conn)
        categorias_bloqueadas = categories_in_cooldown(conn)

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
    barrados = load_exclude()
    if barrados:
        antes = len(picked) + len(repasses)
        picked = [s for s in picked if not e_barrada(s.offer, barrados, temas)]
        repasses = [s for s in repasses if not e_barrada(s.offer, barrados, temas)]
        fora = antes - len(picked) - len(repasses)
        if fora:
            log.info("%d oferta(s) fora pela lista de exclusao.", fora)

    if bloqueados or familias_bloqueadas or categorias_bloqueadas:
        antes = len(picked) + len(repasses)

        def passa(s: ScoredOffer) -> bool:
            if s.offer.product_id in bloqueados:
                return False
            if familia_do_titulo(s.offer.title) in familias_bloqueadas:
                return False
            return (
                familia_do_titulo(s.offer.title, CATEGORIA)
                not in categorias_bloqueadas
            )

        picked = [s for s in picked if passa(s)]
        repasses = [s for s in repasses if passa(s)]
        repetidos = antes - len(picked) - len(repasses)
        if repetidos:
            log.info(
                "%d oferta(s) fora por repeticao: mesmo produto nos ultimos "
                "%d min, mesmo tipo nos ultimos %d min, ou ainda na fila.",
                repetidos,
                post_cooldown_minutes(),
                category_cooldown_minutes(),
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
    # Uma por familia antes de cortar a cota: assim o anuncio repetido nao
    # ocupa vaga que outro produto poderia usar.
    #
    # Quando o cooldown de categoria esta ligado, a assinatura usada aqui e a
    # grossa: a de tres palavras separa "Tenis adidas Boost Run" de "Tenis
    # adidas Ih4039", e os dois saiam na mesma rodada. Como a assinatura curta
    # e prefixo da longa, uma so passagem cobre os dois niveis.
    assinatura = CATEGORIA if category_cooldown_minutes() > 0 else 3
    picked = _priorizar_temas(_uma_por_familia(picked, assinatura), espaco, temas)
    if len(picked) < espaco:
        familias_usadas = {
            familia_do_titulo(s.offer.title, assinatura) for s in picked
        }
        restantes = [
            s
            for s in _uma_por_familia(repasses, assinatura)
            if familia_do_titulo(s.offer.title, assinatura) not in familias_usadas
        ]
        picked += _priorizar_temas(restantes, espaco - len(picked), temas)
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
        # Alimenta a proxima chamada desta mesma rodada: sem isso as 5 ofertas
        # do lote saem com a mesma formula, que e o caso mais visivel de todos.
        recentes.append(text.strip().splitlines()[0].strip())
        drafts.append((scored, text))

    if sem_link:
        log.warning(
            "%d oferta(s) seguraram por falta de link de afiliado. "
            "Gere no Link Builder e rode `promo link`:",
            len(sem_link),
        )
        for scored in sem_link:
            log.warning("  %s  %s", scored.offer.external_id, scored.offer.url)

    if dry_run:
        for _, text in drafts:
            print("\n" + "-" * 40 + "\n" + text)
        return picked

    deliver(drafts, max(0.0, orcamento - gasto))
    return picked


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
    )


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
        queue = [
            (row["id"], row["copy"], row["image_url"], row["grupo_jid"])
            for row in pending_posts(conn)
        ]

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

"""Espelha a publicacao de um grupo-fonte em um grupo nosso.

Pedido do dono em 26/11/2026, sobre o "Radar do Bagg" e o grupo de Esportes:
"a mesma publicacao, mesma foto, mesmo texto, nao precisa nem mandar nada pro
Gemini, quero tudo igual, so o meu link correto".

E o oposto de `sources/grupo_wa`, que so aproveita a pista. Aqui nao ha
pontuacao, cooldown nem copywriter: o texto e a foto sao os deles, e a unica
mudanca e cada link de loja virar o nosso.

Mensagem com qualquer link que nao vira link nosso (outra loja, convite de
grupo, anuncio recusado pelo programa) NAO sai: com o link deles a comissao
seria deles. Mensagem sem link nenhum tambem nao sai. As duas ficam no log.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .config import (
    ROOT,
    EvolutionConfig,
    MercadoLivreConfig,
    MissingConfig,
    amazon_partner_tag,
    post_max_age_minutes,
)
from .db import (
    PRIORIDADE_ESPELHO,
    affiliate_blocked,
    connect,
    create_post,
    get_meta,
    set_meta,
)
from .models import Offer
from .pipeline import em_horario_silencioso
from .sources.affiliate import resolve_affiliate_link
from .sources.amazon_grupo import _precos_da_mensagem, asin_do_link
from .sources.amazon_link import PRODUTO, link_de_afiliado
from .sources.grupo_wa import (
    _texto_da_mensagem,
    baixar_foto,
    ler_mensagens,
    produto_do_link,
    tem_foto,
)
from .sources.ml_linkbuilder import LinkBuilder

log = logging.getLogger("promo")

# Todo endereco do texto, com ou sem `https://`: o que escapasse daqui sairia no
# nosso grupo com o codigo de afiliado deles.
LINK = re.compile(
    r"(?:https?://|www\.|(?<![\w.@/-])(?:[\w-]+\.)+(?:com|br|la|to|co|me|ly|in|"
    r"amazon|net|app|shop|store|link)(?=/))[^\s<>]*",
    re.I,
)
# Pontuacao e marcacao do WhatsApp que grudam no fim do link.
SOBRA = ".,;:!?)]}\"'*_~`"

DOMINIOS_ML = ("meli.la", "mercadolivre.com.br", "mercadolivre.com")
DOMINIOS_AMAZON = ("amazon.com.br", "amzn.to", "a.co", "link.amazon", "amzlinks.in")


class _Depois(Exception):
    """Falha passageira: a mensagem tenta de novo na proxima leitura."""


class _Nunca(Exception):
    """Esta mensagem nao tem como sair com link nosso."""


def load_espelhos(path: Path | None = None) -> list[dict]:
    """Os pares grupo-fonte -> grupo nosso, de `grupos_espelho` no watchlist."""
    path = path or ROOT / "watchlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        g
        for g in (data.get("grupos_espelho") or [])
        # Destino vazio significaria o Geral; aqui seria engano de configuracao.
        if (g.get("jid") or "").strip() and (g.get("destino") or "").strip()
    ]


def _config_da_evolution() -> EvolutionConfig:
    return EvolutionConfig.load()


def _link_builder() -> LinkBuilder | None:
    try:
        config = MercadoLivreConfig.load()
    except MissingConfig:
        return None
    if not (config.affiliate_cookie and config.affiliate_tag):
        return None
    return LinkBuilder(config.affiliate_cookie, config.affiliate_tag)


def _e_de(host: str, dominios: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in dominios)


def _registrar_produto(offer: Offer) -> None:
    """So a linha do produto: link de afiliado e post apontam para ela.

    Sem `record_offer` de proposito: o preco daqui e o que outro grupo
    escreveu, e nao pode entrar no historico que mede desconto.
    """
    visto = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO products (id, source, external_id, title, url,
                image_url, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                offer.product_id,
                offer.source,
                offer.external_id,
                offer.title,
                offer.url,
                offer.image_url,
                visto,
                visto,
            ),
        )


def _nosso_link(url: str, builder, titulo: str) -> tuple[Offer, str]:
    """(produto por tras do link, o nosso link para ele)."""
    completa = url if url.lower().startswith("http") else f"https://{url}"
    host = (urlsplit(completa).hostname or "").lower()

    if _e_de(host, DOMINIOS_AMAZON):
        tag = amazon_partner_tag()
        if not tag:
            raise _Nunca(f"sem tag da Amazon para {url}")
        asin = asin_do_link(completa)
        if not asin:
            raise _Depois(f"link da Amazon sem produto: {url}")
        offer = Offer("amazon", asin, titulo, 0.0, PRODUTO.format(asin=asin))
        _registrar_produto(offer)
        return offer, link_de_afiliado(asin, tag)

    if _e_de(host, DOMINIOS_ML):
        pista = produto_do_link(completa)
        if pista is None or not pista.url:
            raise _Depois(f"nao achei o produto de {url}")
        offer = Offer(
            source="mercadolivre",
            external_id=pista.external_id,
            title=pista.titulo or titulo,
            price=pista.preco,
            url=pista.url,
            image_url=pista.imagem or None,
        )
        _registrar_produto(offer)
        link = resolve_affiliate_link(builder, offer)
        if link:
            return offer, link
        with connect() as conn:
            motivo = affiliate_blocked(conn, offer.product_id)
        if motivo:
            raise _Nunca(f"{url} fora do programa de afiliados: {motivo}")
        raise _Depois(f"sem link de afiliado para {url}")

    raise _Nunca(f"loja sem afiliado nosso: {url}")


def _trocar_links(texto: str, builder) -> tuple[str, Offer]:
    """O texto com os nossos links, e o produto do primeiro deles."""
    produtos: list[Offer] = []
    # A primeira linha e o nome do produto nos posts deles.
    titulo = texto.strip().split("\n")[0].strip("*_~ ")

    def troca(achado: re.Match) -> str:
        url = achado.group(0).rstrip(SOBRA)
        offer, nosso = _nosso_link(url, builder, titulo)
        produtos.append(offer)
        return nosso + achado.group(0)[len(url) :]

    novo = LINK.sub(troca, texto)
    if not produtos:
        raise _Nunca("mensagem sem link de loja")
    return novo, produtos[0]


def _enfileirar(conn, offer: Offer, texto: str, foto: str, destino: str) -> None:
    preco, antes = _precos_da_mensagem(texto)
    preco = preco or offer.price
    desconto = (1 - preco / antes) * 100 if antes > preco > 0 else 0.0
    # O que o bot ia mandar deste produto para o mesmo grupo sairia repetido.
    conn.execute(
        "UPDATE posts SET status = 'expired', error = 'substituido pelo espelho' "
        "WHERE status = 'pending' AND product_id = ? AND grupo_jid = ? "
        "AND prioridade < ?",
        (offer.product_id, destino, PRIORIDADE_ESPELHO),
    )
    create_post(
        conn,
        offer.product_id,
        preco,
        antes or preco,
        desconto,
        texto,
        foto or None,
        verified=False,
        grupo_jid=destino,
        prioridade=PRIORIDADE_ESPELHO,
    )


# Mensagens cuja falha ja foi para o log: a releitura de cada minuto nao repete.
_AVISADAS: set[str] = set()


def espelhar() -> int:
    """Le os grupos espelhados e enfileira o que e novo. Devolve quantos.

    So enfileira: quem envia e a entrega, no ritmo e nos tetos de sempre.
    Mensagem mais velha que o prazo da fila fica de fora -- o preco ja
    envelheceu, e sem o corte a primeira leitura despejaria o dia anterior.
    """
    if em_horario_silencioso():
        return 0
    espelhos = load_espelhos()
    if not espelhos:
        return 0
    config = _config_da_evolution()
    builder = _link_builder()
    prazo = timedelta(minutes=post_max_age_minutes() or 60)
    corte = (datetime.now(timezone.utc) - prazo).timestamp()
    total = 0

    for grupo in espelhos:
        nome = grupo.get("nome") or grupo["jid"]
        try:
            registros = ler_mensagens(
                config.base_url, config.instance, config.api_key, grupo["jid"], 50
            )
        except Exception as exc:  # noqa: BLE001 - um grupo nao derruba os outros
            log.warning("Nao consegui ler o grupo %s: %s", nome, exc)
            continue

        for registro in sorted(
            registros, key=lambda r: int(r.get("messageTimestamp") or 0)
        ):
            id_msg = (registro.get("key") or {}).get("id")
            texto = _texto_da_mensagem(registro)
            if not id_msg or int(registro.get("messageTimestamp") or 0) < corte:
                continue
            if not texto and not tem_foto(registro):
                continue  # reacao, aviso de sistema
            chave = f"espelho:{grupo['jid']}:{id_msg}"
            with connect() as conn:
                if get_meta(conn, chave):
                    continue

            try:
                novo, offer = _trocar_links(texto, builder)
                foto = ""
                if tem_foto(registro):
                    foto = baixar_foto(
                        config.base_url, config.instance, config.api_key, registro
                    )
                    if not foto:
                        raise _Depois("foto nao baixou")
            except _Nunca as exc:
                log.warning("%s: mensagem %s nao espelhada: %s", nome, id_msg, exc)
                with connect() as conn:
                    set_meta(conn, chave, "recusada")
                continue
            except Exception as exc:  # noqa: BLE001 - inclui `_Depois`
                if chave not in _AVISADAS:
                    _AVISADAS.add(chave)
                    log.warning(
                        "%s: mensagem %s ainda nao espelhada, tento de novo: %s",
                        nome,
                        id_msg,
                        exc,
                    )
                continue

            with connect() as conn:
                _enfileirar(conn, offer, novo, foto, grupo["destino"])
                set_meta(conn, chave, "enfileirada")
            total += 1

    if total:
        log.info("Espelho: %d publicacao(oes) na fila.", total)
    return total

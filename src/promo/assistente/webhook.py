"""O evento `messages.upsert` da Evolution, reduzido ao que o assistente usa.

Fica de fora, sem resposta: mensagem nossa (`fromMe`), de grupo, de status e de
canal, sem texto (audio, figurinha) e as antigas. A Evolution reenvia o
historico quando reconecta, e responder a pergunta de ontem seria pior do que
nao responder.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..db import now
from ..sources.grupo_wa import _texto_da_mensagem

log = logging.getLogger("promo")

IDADE_MAXIMA = timedelta(minutes=10)
MAX_CARACTERES = 1000


@dataclass(frozen=True)
class Recebida:
    id: str
    jid: str  # para onde vai a resposta: sempre o JID do telefone
    telefone: str  # so digitos
    texto: str


def _fora(jid: str) -> bool:
    return jid.endswith("@g.us") or jid.endswith("@newsletter") or "broadcast" in jid


def _ler(registro: dict, agora: datetime) -> Recebida | None:
    chave = registro.get("key") or {}
    if chave.get("fromMe") or not chave.get("id"):
        return None
    # Conta nova no WhatsApp fala por LID (`123@lid`), e o numero vem num campo
    # a parte. A lista liberada e o limite sao por telefone, entao sem ele a
    # mensagem nao entra.
    jids = [
        str(j)
        for j in (
            chave.get("remoteJid"),
            chave.get("remoteJidAlt"),
            chave.get("senderPn"),
            registro.get("remoteJidAlt"),
        )
        if j
    ]
    if not jids or _fora(jids[0]):
        return None
    do_telefone = next((j for j in jids if j.endswith("@s.whatsapp.net")), None)
    if do_telefone is None:
        # So os nomes dos campos, sem valor: e o que diz onde a Evolution pos o
        # telefone quando o formato mudar.
        log.info(
            "Mensagem %s sem telefone, so LID. Campos da chave: %s",
            chave.get("id"),
            ", ".join(sorted(chave)),
        )
        return None
    texto = _texto_da_mensagem(registro).strip()
    if not texto:
        return None
    carimbo = registro.get("messageTimestamp")
    if isinstance(carimbo, (int, float, str)) and str(carimbo).isdigit():
        enviada = datetime.fromtimestamp(int(carimbo), UTC)
        if agora - enviada > IDADE_MAXIMA:
            return None
    telefone = do_telefone.split("@")[0].split(":")[0]
    return Recebida(
        id=str(chave["id"]),
        jid=f"{telefone}@s.whatsapp.net",
        telefone=telefone,
        texto=texto[:MAX_CARACTERES],
    )


def ler_evento(corpo: dict, agora: datetime | None = None) -> list[Recebida]:
    """As mensagens do evento que pedem resposta. Lista vazia para o resto."""
    evento = str(corpo.get("event") or "").lower().replace("_", ".")
    if evento != "messages.upsert":
        return []
    dados = corpo.get("data")
    registros = dados if isinstance(dados, list) else [dados]
    agora = agora or now()
    return [r for r in (_ler(d, agora) for d in registros if isinstance(d, dict)) if r]

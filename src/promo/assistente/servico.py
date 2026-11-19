"""O servico do assistente: webhook, fila de resposta e indexador.

O webhook so enfileira -- a Evolution espera resposta rapida, e o Gemini mais o
envio levam segundos. Quem responde e uma thread que le a fila, uma mensagem por
vez, com as travas do beta:

- so numeros da lista liberada (`ASSISTENTE_NUMEROS`);
- no maximo `ASSISTENTE_MAX_POR_HORA` respostas por numero;
- PARAR desliga para aquele numero, VOLTAR religa;
- espera de 2 a 5 s antes de mandar, como uma pessoa digitando.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from hmac import compare_digest
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request

from ..config import (
    assistente_max_por_hora,
    assistente_numeros,
    assistente_webhook_token,
)
from ..db import sem_acento
from .banco import (
    apagar_antigas,
    conectar,
    finalizar,
    marcar_parada,
    parou,
    proxima_pendente,
    registrar_contato,
    registrar_mensagem,
    respondidas_na_ultima_hora,
)
from .busca import Busca
from .conversa import Assistente, Resposta
from .indice import (
    COBERTURA_MINIMA,
    Embeddings,
    Vetores,
    sincronizar,
    vetorizar_pendentes,
)
from .webhook import ler_evento

log = logging.getLogger("promo")

AVISO_PRIMEIRA_VEZ = (
    "Sou um assistente automático da Comunidade do Desconto. Guardo sua "
    "mensagem por até 30 dias, para responder e melhorar a busca. Para eu "
    "parar de responder, mande PARAR."
)
PAROU = "Pronto, não respondo mais por aqui. Se mudar de ideia, mande VOLTAR."
VOLTOU = "Voltei! Me diga o produto que você procura."

PARAR = {"parar", "pare", "sair", "stop", "cancelar"}
VOLTAR = {"voltar", "volta"}

# O indexador roda a cada meia hora: o bot reconsulta a carteira inteira a cada
# hora, entao um intervalo menor so repetiria o mesmo indice.
INTERVALO_DO_INDICE = 1800.0

# Pausa entre lotes de embedding: com 40 textos por lote, nenhuma janela de 60 s
# passa de 80, abaixo do teto de 100 do plano gratuito do Gemini (ver
# `indice.LOTE_DE_EMBEDDING`).
PAUSA_ENTRE_LOTES = 45.0

ESPERA_APOS_FALHA = 60.0


def pausa_fixa(segundos: float = PAUSA_ENTRE_LOTES) -> Callable[[], bool]:
    """O `esperar` do indexador fora do servico: dorme e nunca pede para parar."""

    def esperar() -> bool:
        time.sleep(segundos)
        return False

    return esperar


def variantes(numero: str) -> set[str]:
    """O mesmo celular brasileiro com e sem o nono digito.

    Conta antiga no WhatsApp identifica o numero sem o 9 depois do DDD
    (55 81 8888-7777), e quem preenche a lista escreve com ele
    (55 81 9 8888-7777). Sem isto, o dono ficaria fora do proprio beta.
    """
    saida = {numero}
    if numero.startswith("55") and len(numero) == 13 and numero[4] == "9":
        saida.add(numero[:4] + numero[5:])
    if numero.startswith("55") and len(numero) == 12:
        saida.add(numero[:4] + "9" + numero[4:])
    return saida


def liberado(telefone: str, lista: list[str]) -> bool:
    permitidos = {v for numero in lista for v in variantes(numero)}
    return bool(variantes(telefone) & permitidos)


def final_do_telefone(telefone: str) -> str:
    """So os 4 ultimos digitos: o log fica 30 dias no disco do servidor."""
    return f"...{telefone[-4:]}"


def _espera_humana() -> None:
    time.sleep(random.uniform(2.0, 5.0))


class Atendente:
    """Responde a fila, uma mensagem por vez."""

    def __init__(
        self,
        assistente: Assistente,
        entrega,
        liberados: list[str] | None = None,
        max_por_hora: int | None = None,
        esperar: Callable[[], None] = _espera_humana,
        caminho: Path | None = None,
    ) -> None:
        self._assistente = assistente
        self._entrega = entrega
        self._liberados = assistente_numeros() if liberados is None else liberados
        self._max_por_hora = (
            assistente_max_por_hora() if max_por_hora is None else max_por_hora
        )
        self._esperar = esperar
        self._caminho = caminho

    def atender_uma(self) -> bool:
        """Trata a mensagem pendente mais antiga. False quando a fila esta vazia."""
        with conectar(self._caminho) as conn:
            msg = proxima_pendente(conn)
            if msg is None:
                return False
            telefone = msg["telefone"]
            comando = sem_acento(msg["texto"]).strip(" .!?")
            motivo: str | None = None
            novo = False
            fixa: str | None = None
            if not liberado(telefone, self._liberados):
                motivo = "fora da lista liberada"
            else:
                novo = registrar_contato(conn, telefone)
                if comando in PARAR:
                    marcar_parada(conn, telefone, True)
                    fixa = PAROU
                elif parou(conn, telefone):
                    if comando in VOLTAR:
                        marcar_parada(conn, telefone, False)
                        fixa = VOLTOU
                    else:
                        motivo = "pediu para parar"
                elif respondidas_na_ultima_hora(conn, telefone) >= self._max_por_hora:
                    motivo = "limite por hora"
            if motivo is not None:
                finalizar(conn, msg["id"], "ignorada", motivo)
                log.info(
                    "Assistente ignorou %s de %s: %s",
                    msg["id"],
                    final_do_telefone(telefone),
                    motivo,
                )
                return True

        # Fora da conexao: o Gemini e o envio levam segundos, e o webhook
        # precisa continuar gravando enquanto isso.
        try:
            if fixa is not None:
                resposta = Resposta(fixa, None, 0)
            else:
                resposta = self._assistente.responder(msg["texto"])
            texto = resposta.texto
            if novo and fixa is None:
                texto = f"{texto}\n\n{AVISO_PRIMEIRA_VEZ}"
            self._esperar()
            self._entrega.send_text(texto, to=msg["jid"])
        except Exception as exc:  # noqa: BLE001 - uma mensagem ruim nao para a fila
            log.warning("Assistente nao respondeu %s: %s", msg["id"], exc)
            with conectar(self._caminho) as conn:
                finalizar(conn, msg["id"], "erro", str(exc)[:300])
            return True

        with conectar(self._caminho) as conn:
            finalizar(
                conn,
                msg["id"],
                "respondida",
                consulta=resposta.consulta,
                resultados=resposta.resultados,
                resposta=texto,
            )
        log.info(
            "Assistente respondeu %s para %s: consulta=%r, %d resultado(s)",
            msg["id"],
            final_do_telefone(telefone),
            resposta.consulta,
            resposta.resultados,
        )
        return True


def indexar(
    busca: Busca,
    embeddings: Embeddings | None,
    caminho: Path | None = None,
    esperar: Callable[[], bool] | None = None,
) -> dict:
    """Atualiza o indice e recarrega os vetores. Sem embeddings, fica o texto.

    O texto (FTS) vale assim que `sincronizar` termina; os vetores entram aos
    poucos, no ritmo de `esperar`, e cada recarga ja usa o que estiver pronto.
    """
    with conectar(caminho) as conn:
        ativos, removidos = sincronizar(conn)
        apagadas = apagar_antigas(conn)
    vetorizados = 0
    cobertura = 0.0
    if embeddings is not None:
        with conectar(caminho) as conn:
            vetorizados = vetorizar_pendentes(conn, embeddings, esperar=esperar)
            vetores = Vetores.carregar(conn, embeddings.assinatura)
        if vetores is not None and ativos:
            cobertura = len(vetores) / ativos
        # Vetor de parte do catalogo traz vizinho aleatorio: fica so o texto ate
        # a carga chegar perto do fim (ver `indice.COBERTURA_MINIMA`).
        busca.atualizar_vetores(vetores if cobertura >= COBERTURA_MINIMA else None)
    resumo = {
        "ativos": ativos,
        "removidos": removidos,
        "vetorizados": vetorizados,
        "cobertura_dos_vetores": round(cobertura, 3),
        "conversas_apagadas": apagadas,
    }
    log.info("Indice do assistente: %s", resumo)
    return resumo


def _laco(stop: threading.Event, tarefa: Callable[[], object], pausa: float) -> None:
    while not stop.is_set():
        try:
            trabalhou = tarefa()
        except Exception:  # noqa: BLE001 - a thread nao pode morrer
            log.exception("Falha no assistente")
            # Falha tenta de novo logo, sem esperar a pausa cheia: a do indice
            # e meia hora, e o indice ficaria vazio esse tempo todo.
            stop.wait(min(pausa, ESPERA_APOS_FALHA))
            continue
        if not trabalhou:
            stop.wait(pausa)


def criar_app(
    atendente: Atendente | None = None,
    busca: Busca | None = None,
    embeddings: Embeddings | None = None,
) -> FastAPI:
    """A API do webhook. Com `atendente` e `busca`, sobe tambem as duas threads."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = threading.Event()
        # Cria o banco antes das threads. Com o arquivo novo, as duas abriam ao
        # mesmo tempo e uma levava "database is locked" na troca para WAL --
        # erro imediato, sem passar pelo busy_timeout. Visto na 1a subida local
        # em 18/11/2026.
        with conectar():
            pass
        if atendente is not None and busca is not None:

            def indice() -> bool:
                indexar(busca, embeddings, esperar=lambda: stop.wait(PAUSA_ENTRE_LOTES))
                stop.wait(INTERVALO_DO_INDICE)
                return True

            for nome, alvo, pausa in (
                ("assistente-indice", indice, INTERVALO_DO_INDICE),
                ("assistente-fila", atendente.atender_uma, 1.0),
            ):
                threading.Thread(
                    target=_laco, args=(stop, alvo, pausa), name=nome, daemon=True
                ).start()
        yield
        stop.set()

    app = FastAPI(title="Assistente da Comunidade do Desconto", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        with conectar() as conn:
            pendentes = conn.execute(
                "SELECT COUNT(*) FROM mensagens WHERE status = 'pendente'"
            ).fetchone()[0]
        return {"status": "ok", "pendentes": pendentes}

    @app.post("/webhook/{token}")
    async def webhook(token: str, request: Request) -> dict:
        esperado = assistente_webhook_token()
        # 404, e nao 401: quem chuta o endereco nao fica sabendo que ele existe.
        if not esperado or not compare_digest(token, esperado):
            raise HTTPException(status_code=404)
        try:
            corpo = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400) from exc
        lista = assistente_numeros()
        novas = 0
        with conectar() as conn:
            for m in ler_evento(corpo if isinstance(corpo, dict) else {}):
                # Fora da lista do beta nem o texto fica guardado. O chip e o do
                # grupo, e quem manda mensagem no privado dele sem estar no
                # teste nao tem por que ficar no disco.
                if not liberado(m.telefone, lista):
                    log.info(
                        "Assistente: %s fora do beta, nao guardada",
                        final_do_telefone(m.telefone),
                    )
                    continue
                if registrar_mensagem(conn, m.id, m.jid, m.telefone, m.texto):
                    novas += 1
                    log.info(
                        "Assistente recebeu %s de %s",
                        m.id,
                        final_do_telefone(m.telefone),
                    )
        return {"novas": novas}

    return app


def montar() -> FastAPI:
    """O servico de verdade: Gemini, Evolution e o indice no disco."""
    from ..config import EvolutionConfig, assistente_instancia
    from ..delivery.evolution import Evolution

    embeddings = Embeddings()
    busca = Busca(embeddings=embeddings)
    config = EvolutionConfig.load()
    if assistente_instancia():
        # Outro chip para o assistente: banimento dele nao derruba o grupo.
        config = replace(config, instance=assistente_instancia())
    atendente = Atendente(Assistente(busca), Evolution(config))
    return criar_app(atendente, busca, embeddings)

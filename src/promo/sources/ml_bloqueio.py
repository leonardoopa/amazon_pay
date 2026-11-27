"""Para de insistir quando o Mercado Livre recusa.

Em 27/11/2026 o preco de catalogo respondia 403 (PolicyAgent) em todas as
chamadas desde a tarde anterior, de 2 a 3 mil por hora, e as paginas publicas
mandavam o servidor para a verificacao de conta. Insistir nao traz preco, gera
um aviso por tentativa e nao ajuda a recusa a acabar.

Depois de N recusas seguidas as chamadas ficam suspensas. Passada a pausa, uma
so vai de sonda: se for aceita, tudo volta; se nao, comeca outra pausa. Isto so
espera. Nao ha proxy, cookie nem troca de agente para passar pela recusa.

O estado fica em memoria e e do processo: `build_sources` cria fontes novas a
cada rodada, e o ciclo rapido roda em outra thread.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from ..config import ml_bloqueio_limite, ml_bloqueio_pausa_minutos

log = logging.getLogger("promo")


class MLSuspenso(RuntimeError):
    """A chamada nao foi feita, ou foi a recusa que manteve a suspensao.

    Quem chama nao deve avisar no log: o erro ja foi registrado uma vez.
    """


class Disjuntor:
    """Conta recusas seguidas de um tipo de chamada e suspende as proximas."""

    def __init__(self, recusa: str, relogio=time.monotonic) -> None:
        self.recusa = recusa  # o que o ML fez, para a mensagem de erro
        self.relogio = relogio
        self._lock = threading.Lock()
        self.zerar()

    def zerar(self) -> None:
        """Estado de processo recem-iniciado."""
        self._ultimo_aceito = ""
        self._liberar()

    def _liberar(self) -> None:
        self._seguidas = 0
        self._reabre_em: float | None = None  # None = chamadas liberadas
        self._sondas: set[str] = set()

    @property
    def aberto(self) -> bool:
        """Ha uma recusa em curso, com ou sem sonda disponivel agora."""
        return self._reabre_em is not None

    def suspenso(self) -> bool:
        """Nada sai agora: ha recusa em curso e a pausa ainda nao acabou."""
        reabre_em = self._reabre_em
        return reabre_em is not None and self.relogio() < reabre_em

    def pedir(self, alvo: str) -> None:
        """Chamar antes da requisicao. Levanta `MLSuspenso` se ela nao deve sair.

        Alvo que ja foi sonda e foi recusado cede a vez por uma pausa. Um ID de
        anuncio de vendedor responde 403 mesmo com a API saudavel, e se ele
        encabecasse a fila toda vez a fonte nunca voltaria. Se nao aparecer
        outro, ele pode ir de novo.
        """
        with self._lock:
            if self._reabre_em is None:
                return
            agora = self.relogio()
            pausa = ml_bloqueio_pausa_minutos() * 60
            repetido = alvo in self._sondas and agora < self._reabre_em + pausa
            if agora < self._reabre_em or repetido:
                raise MLSuspenso(f"{self.recusa}: chamadas suspensas")
            self._sondas.add(alvo)
            # Empurrado ja na saida: enquanto a sonda esta no ar, so ela.
            self._reabre_em = agora + pausa
            log.debug("Sonda no Mercado Livre: %s", alvo)

    def recusado(self, ainda_responde: Callable[[str], bool] | None = None) -> bool:
        """Registra uma recusa. True quando as chamadas estao suspensas.

        `ainda_responde` tira a duvida antes de suspender: recebe o ultimo alvo
        aceito e diz se ele continua respondendo. Se continua, a sequencia era
        de alvos recusados um a um, e nao um bloqueio.
        """
        limite = ml_bloqueio_limite()
        with self._lock:
            if self._reabre_em is not None:
                return True
            self._seguidas += 1
            seguidas, prova = self._seguidas, self._ultimo_aceito
        if limite <= 0 or seguidas < limite:
            return False

        # Fora do lock: e uma requisicao.
        if ainda_responde and prova and ainda_responde(prova):
            with self._lock:
                self._seguidas = 0
            return False

        with self._lock:
            if self._reabre_em is not None:
                return True
            minutos = ml_bloqueio_pausa_minutos()
            self._reabre_em = self.relogio() + minutos * 60
            log.error(
                "Mercado Livre bloqueado: %s %d vezes seguidas. Chamadas "
                "suspensas; uma sonda a cada %g min ate voltar a responder.",
                self.recusa,
                seguidas,
                minutos,
            )
            return True

    def aceito(self, alvo: str = "") -> None:
        """Registra uma resposta aceita: zera a contagem e libera as chamadas.

        `alvo` fica guardado para a conferencia de `recusado`.
        """
        with self._lock:
            if self._reabre_em is not None:
                log.info(
                    "Mercado Livre voltou a responder (antes: %s). Chamadas "
                    "retomadas.",
                    self.recusa,
                )
            self._liberar()
            if alvo:
                self._ultimo_aceito = alvo

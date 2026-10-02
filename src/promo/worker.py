"""Loop de fundo: roda o pipeline, dorme, repete.

Vive fora do cli.py porque agora tem dois donos -- o comando `daemon` e o
lifespan da API. Duplicar o loop nos dois lugares seria duplicar o tratamento
de erro, que e justamente a parte que importa: uma rodada que falha nao pode
matar o processo.
"""

from __future__ import annotations

import logging
import threading
from time import monotonic
from typing import Callable

from .config import MissingConfig
from .db import connect, now, set_meta

log = logging.getLogger("promo")

# Piso de espera entre rodadas. Existe so para o caso de a rodada falhar em
# menos de um segundo (config quebrada, rede fora): sem ele o laco bateria nas
# APIs sem pausa nenhuma ate alguem notar.
PAUSA_MINIMA = 30.0

# Carimbos lidos pelo /health. Ficam no banco e nao em memoria porque o
# processo reinicia, e um contador em memoria zeraria junto -- mesmo motivo
# que ja levou o resto do controle do bot pra tabela `meta`.
LAST_RUN_KEY = "last_run"
LAST_ERROR_KEY = "last_run_error"


def stamp_run(erro: str | None = None) -> None:
    """Registra o fim da rodada (e o erro, se houve).

    Engole a propria falha de proposito: isso e chamado de dentro do except do
    loop, e um banco indisponivel nao pode transformar 'rodada falhou' em
    'processo morreu'.
    """
    try:
        with connect() as conn:
            set_meta(conn, LAST_RUN_KEY, now().isoformat())
            set_meta(conn, LAST_ERROR_KEY, erro or "")
    except Exception:  # noqa: BLE001 - carimbo e diagnostico, nao pode derrubar
        log.exception("Nao consegui carimbar o fim da rodada")


def start_drenagem(stop: threading.Event) -> threading.Thread | None:
    """Sobe a thread que goteja a fila, ou None quando nao se aplica.

    Dois donos, como o `loop`: o comando `daemon` e o lifespan da API. So existe
    para o backend 'evolution' -- o 'cloud' tem janela de 24h e template, que
    pedem a drenagem de dentro da rodada. `ENTREGA_CONTINUA=false` desliga.
    """
    from .config import delivery_backend, entrega_continua

    if not entrega_continua() or delivery_backend() != "evolution":
        return None

    from .pipeline import drenar_continuamente

    thread = threading.Thread(
        target=drenar_continuamente, args=(stop,), name="promo-drip", daemon=True
    )
    thread.start()
    return thread


def loop(
    stop: threading.Event,
    interval: float,
    task: Callable[[], object],
) -> int:
    """Executa `task` a CADA `interval` segundos ate `stop` ser sinalizado.

    "A cada", e nao "com `interval` de pausa entre uma e outra" -- a diferenca
    custava metade do volume do grupo. A rodada leva de 10 a 15 minutos (a
    coleta chama o Gemini uma vez por oferta, e a entrega goteja com intervalo
    proposital), e dormir `interval` DEPOIS disso fazia o ciclo real ser
    rodada + 900s, perto de 30 minutos. O gotejamento entregava o que cabia no
    orcamento e o grupo ficava mudo o resto do tempo.

    O orcamento de drenagem em `deliver()` e `interval * 0.8`, ou seja, ele ja
    assumia que `interval` era o periodo do ciclo. As duas pontas agora
    concordam: descontar o tempo gasto faz o ciclo durar `interval` de verdade.

    Devolve 2 se faltar configuracao -- nao adianta tentar de novo, o .env nao
    se preenche sozinho. Qualquer outra excecao vira log e a proxima rodada
    acontece normalmente: queda de rede nao pode parar o bot ate alguem notar.
    """
    while not stop.is_set():
        inicio = monotonic()
        try:
            task()
            stamp_run()
        except MissingConfig as exc:
            log.error("Configuracao faltando: %s", exc)
            return 2
        except Exception as exc:  # noqa: BLE001 - uma rodada ruim nao mata o loop
            log.exception("Rodada falhou: %s", exc)
            stamp_run(str(exc))

        gasto = monotonic() - inicio
        # Piso de espera para rodada que falha rapido: sem ele, um erro
        # imediato e repetido viraria laco quente batendo nas APIs sem pausa.
        espera = max(PAUSA_MINIMA, interval - gasto)
        if gasto > interval:
            log.info(
                "Rodada levou %.0fs, acima do intervalo de %.0fs; "
                "a proxima comeca em %.0fs.",
                gasto,
                interval,
                espera,
            )
        stop.wait(espera)

    return 0

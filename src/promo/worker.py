"""Loop de fundo: roda o pipeline, dorme, repete.

Vive fora do cli.py porque agora tem dois donos -- o comando `daemon` e o
lifespan da API. Duplicar o loop nos dois lugares seria duplicar o tratamento
de erro, que e justamente a parte que importa: uma rodada que falha nao pode
matar o processo.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from .config import MissingConfig
from .db import connect, now, set_meta

log = logging.getLogger("promo")

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


def loop(
    stop: threading.Event,
    interval: float,
    task: Callable[[], object],
) -> int:
    """Executa `task` a cada `interval` segundos ate `stop` ser sinalizado.

    Devolve 2 se faltar configuracao -- nao adianta tentar de novo, o .env nao
    se preenche sozinho. Qualquer outra excecao vira log e a proxima rodada
    acontece normalmente: queda de rede nao pode parar o bot ate alguem notar.
    """
    while not stop.is_set():
        try:
            task()
            stamp_run()
        except MissingConfig as exc:
            log.error("Configuracao faltando: %s", exc)
            return 2
        except Exception as exc:  # noqa: BLE001 - uma rodada ruim nao mata o loop
            log.exception("Rodada falhou: %s", exc)
            stamp_run(str(exc))

        stop.wait(interval)

    return 0

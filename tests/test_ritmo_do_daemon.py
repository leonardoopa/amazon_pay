"""O laco do daemon roda a CADA `interval`, e nao com `interval` de pausa.

A diferenca custava metade do volume do grupo: a rodada leva de 10 a 15
minutos, e dormir `interval` depois dela fazia o ciclo real ser perto de 30.
O orcamento de drenagem em `deliver()` e `interval * 0.8`, ou seja, o outro
lado do codigo ja assumia que `interval` era o periodo do ciclo.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import worker  # noqa: E402


def rodar(duracao_da_rodada: float, interval: float, vezes: int = 1):
    """Roda o laco `vezes` e devolve as esperas pedidas ao stop.wait."""
    esperas: list[float] = []
    relogio = {"t": 0.0}
    chamadas = {"n": 0}

    def falso_monotonic() -> float:
        return relogio["t"]

    def task() -> None:
        relogio["t"] += duracao_da_rodada

    stop = mock.Mock(spec=threading.Event)
    stop.is_set.side_effect = lambda: chamadas["n"] > vezes

    def wait(segundos):
        esperas.append(segundos)
        chamadas["n"] += 1

    stop.wait.side_effect = wait

    with (
        mock.patch.object(worker, "monotonic", falso_monotonic),
        mock.patch.object(worker, "stamp_run", lambda *a, **k: None),
    ):
        worker.loop(stop, interval, task)
    return esperas


def test_desconta_o_tempo_gasto_na_rodada():
    """Rodada de 600s com intervalo de 900s deve dormir 300s, nao 900s."""
    assert rodar(duracao_da_rodada=600.0, interval=900.0)[0] == 300.0


def test_rodada_curta_dorme_quase_o_intervalo_inteiro():
    assert rodar(duracao_da_rodada=10.0, interval=900.0)[0] == 890.0


def test_rodada_que_estoura_o_intervalo_respeita_o_piso():
    """Sem piso, rodada longa (ou falha instantanea) viraria laco quente."""
    assert rodar(duracao_da_rodada=1200.0, interval=900.0)[0] == worker.PAUSA_MINIMA


def test_rodada_instantanea_tambem_respeita_o_piso():
    esperas = rodar(duracao_da_rodada=0.0, interval=10.0)
    assert esperas[0] == worker.PAUSA_MINIMA


def test_o_ciclo_se_mantem_estavel_entre_rodadas():
    esperas = rodar(duracao_da_rodada=600.0, interval=900.0, vezes=3)
    assert set(esperas) == {300.0}
    assert len(esperas) >= 3

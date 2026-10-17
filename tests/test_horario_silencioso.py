"""Janela de silencio: entre 23:00 e 07:00 o bot nao envia promocao nenhuma.

Pedido do dono. Antes a janela so desacelerava (um post por rodada), e isso
ainda acordava o grupo a noite inteira.

O caso que importa e o que quase todo codigo de horario erra: a janela cruza a
meia-noite. 23:00 as 07:00 nao e "entre inicio e fim" -- e "depois do inicio OU
antes do fim". Comparar com `inicio <= agora < fim` daria False a noite inteira,
e o bot seguiria postando as 3 da manha sem ninguem notar ate o grupo reclamar.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.pipeline import em_horario_silencioso  # noqa: E402


def as_horas(hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 8, 29, hora, minuto)


def janela_padrao(monkeypatch) -> None:
    monkeypatch.setenv("QUIET_START", "23:00")
    monkeypatch.setenv("QUIET_END", "07:00")


# ---------- janela que cruza a meia-noite ----------


def test_madrugada_esta_dentro_da_janela(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(3))


def test_logo_apos_o_inicio_esta_dentro(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(23, 1))


def test_o_minuto_do_inicio_ja_conta(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(23, 0))


def test_pouco_antes_do_inicio_esta_fora(monkeypatch):
    janela_padrao(monkeypatch)
    assert not em_horario_silencioso(as_horas(22, 59))


def test_o_minuto_do_fim_ja_esta_fora(monkeypatch):
    """07:00 e quando o bot volta, entao nao pertence a janela."""
    janela_padrao(monkeypatch)
    assert not em_horario_silencioso(as_horas(7, 0))


def test_pouco_antes_do_fim_ainda_esta_dentro(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(6, 59))


def test_meio_do_dia_esta_fora(monkeypatch):
    janela_padrao(monkeypatch)
    assert not em_horario_silencioso(as_horas(14))


# ---------- janela normal, sem cruzar a meia-noite ----------


def test_janela_no_mesmo_dia(monkeypatch):
    monkeypatch.setenv("QUIET_START", "13:00")
    monkeypatch.setenv("QUIET_END", "15:00")
    assert em_horario_silencioso(as_horas(14))
    assert not em_horario_silencioso(as_horas(12))
    assert not em_horario_silencioso(as_horas(16))


# ---------- desligar ----------


def test_janela_vazia_nunca_silencia(monkeypatch):
    monkeypatch.setenv("QUIET_START", "")
    monkeypatch.setenv("QUIET_END", "")
    assert not em_horario_silencioso(as_horas(3))


def test_horario_malformado_nao_silencia_em_silencio(monkeypatch, caplog):
    """Erro de digitacao no .env tem que aparecer, nao virar janela desligada muda."""
    import logging

    monkeypatch.setenv("QUIET_START", "23h30")
    monkeypatch.setenv("QUIET_END", "07:30")
    with caplog.at_level(logging.WARNING):
        assert not em_horario_silencioso(as_horas(3))
    assert "QUIET_START" in caplog.text


# ---------- padrao ----------


def test_sem_nada_no_env_vale_23h_as_7h(monkeypatch):
    """O padrao e o pedido do dono, para quem apagar as linhas do .env."""
    monkeypatch.delenv("QUIET_START")
    monkeypatch.delenv("QUIET_END")
    assert em_horario_silencioso(as_horas(23, 0))
    assert em_horario_silencioso(as_horas(6, 59))
    assert not em_horario_silencioso(as_horas(7, 0))
    assert not em_horario_silencioso(as_horas(22, 59))


# ---------- a rodada nao coleta ----------


@pytest.mark.parametrize("rapido", [False, True])
def test_na_janela_a_rodada_nao_coleta(monkeypatch, rapido):
    """Coletar de madrugada gastaria Gemini e link de afiliado em post que
    expira antes das 07:00. A rodada tem que sair antes de montar as fontes."""

    def nao_pode(*_a, **_k):
        raise AssertionError("coletou dentro da janela")

    monkeypatch.setattr(pipeline, "em_horario_silencioso", lambda *a, **k: True)
    monkeypatch.setattr(pipeline, "build_sources", nao_pode)
    monkeypatch.setattr(pipeline, "flush_pending", nao_pode)

    assert pipeline.run(rapido=rapido) == []


def test_relogio_de_verdade_tambem_silencia_a_rodada(monkeypatch):
    """Sem trocar `em_horario_silencioso`: as 3h com a janela padrao."""

    def nao_pode(*_a, **_k):
        raise AssertionError("coletou dentro da janela")

    janela_padrao(monkeypatch)
    monkeypatch.setattr("promo.pipeline.datetime", _RelogioFixo(as_horas(3)))
    monkeypatch.setattr(pipeline, "build_sources", nao_pode)

    assert pipeline.run() == []
    assert pipeline.drip_once() == pipeline.NADA


class _RelogioFixo:
    """Congela `datetime.now()` so dentro do pipeline."""

    def __init__(self, quando: datetime) -> None:
        self.quando = quando

    def now(self) -> datetime:
        return self.quando

"""Janela de silencio: de madrugada o grupo recebe muito menos post.

O caso que importa e o que quase todo codigo de horario erra: a janela cruza a
meia-noite. 23:30 as 07:30 nao e "entre inicio e fim" -- e "depois do inicio OU
antes do fim". Comparar com `inicio <= agora < fim` daria False a noite inteira,
e o bot seguiria postando as 3 da manha sem ninguem notar ate o grupo reclamar.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.pipeline import _drip_gap, em_horario_silencioso  # noqa: E402


def as_horas(hora: int, minuto: int = 0) -> datetime:
    return datetime(2026, 8, 29, hora, minuto)


def janela_padrao(monkeypatch) -> None:
    monkeypatch.setenv("QUIET_START", "23:30")
    monkeypatch.setenv("QUIET_END", "07:30")


# ---------- janela que cruza a meia-noite ----------


def test_madrugada_esta_dentro_da_janela(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(3))


def test_logo_apos_o_inicio_esta_dentro(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(23, 31))


def test_o_minuto_do_inicio_ja_conta(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(23, 30))


def test_pouco_antes_do_inicio_esta_fora(monkeypatch):
    janela_padrao(monkeypatch)
    assert not em_horario_silencioso(as_horas(23, 29))


def test_o_minuto_do_fim_ja_esta_fora(monkeypatch):
    """07:30 e quando volta ao normal, entao nao pertence a janela."""
    janela_padrao(monkeypatch)
    assert not em_horario_silencioso(as_horas(7, 30))


def test_pouco_antes_do_fim_ainda_esta_dentro(monkeypatch):
    janela_padrao(monkeypatch)
    assert em_horario_silencioso(as_horas(7, 29))


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


# ---------- efeito no gotejamento ----------


def test_intervalo_estica_dentro_da_janela(monkeypatch):
    """Dentro da janela o gap passa do orcamento de uma rodada, entao sai
    um post por rodada em vez de cinco."""
    monkeypatch.setenv("DRIP_INTERVAL_SECONDS", "150")
    monkeypatch.setenv("QUIET_DRIP_MULTIPLIER", "6")
    janela_padrao(monkeypatch)

    # O jitter e de 40%, entao compara faixas em vez de valor exato.
    monkeypatch.setattr("promo.pipeline.datetime", _RelogioFixo(as_horas(3)))
    dentro = [_drip_gap() for _ in range(30)]
    assert min(dentro) >= 150 * 6 * 0.6

    monkeypatch.setattr("promo.pipeline.datetime", _RelogioFixo(as_horas(14)))
    fora = [_drip_gap() for _ in range(30)]
    assert max(fora) <= 150 * 1.4
    assert min(dentro) > max(fora)


class _RelogioFixo:
    """Congela `datetime.now()` só dentro do pipeline."""

    def __init__(self, quando: datetime) -> None:
        self.quando = quando

    def now(self) -> datetime:
        return self.quando

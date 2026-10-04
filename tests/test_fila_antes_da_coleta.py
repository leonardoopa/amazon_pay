"""A fila drena antes da coleta, e drena mesmo em rodada que nao seleciona.

Dois defeitos do mesmo lugar, ambos vistos em producao em 01/09/2026.

O primeiro: coleta e entrega sao seriais dentro da rodada, entao o tempo de
coleta vira silencio no grupo mesmo com post pronto na fila. Uma rodada com
descoberta completa mais reconsulta da carteira inteira gastou 191 termos em
14 min, 34 categorias em 2,5 min e 900 produtos em 3,5 min -- 22 minutos ate a
primeira mensagem sair.

O segundo: `deliver` so era chamado quando a rodada selecionava alguma oferta.
Rodada sem selecao -- que acontece toda vez que a vitrine esgota no cooldown --
deixava a fila anterior parada esperando alguma rodada futura selecionar algo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402


class Relogio:
    """monotonic falso, avanca so quando o sleep injetado e chamado."""

    def __init__(self) -> None:
        self.agora = 0.0

    def monotonic(self) -> float:
        return self.agora

    def sleep(self, segundos: float) -> None:
        self.agora += segundos


# ---------- flush_pending devolve o tempo gasto ----------


def test_fila_vazia_gasta_zero(monkeypatch, banco_em_memoria):
    monkeypatch.setattr(pipeline, "build_delivery", lambda: object())
    relogio = Relogio()

    gasto = pipeline.flush_pending(
        100.0, sleep=relogio.sleep, monotonic=relogio.monotonic
    )

    assert gasto == 0.0


def test_devolve_o_tempo_de_gotejamento(monkeypatch, banco_em_memoria):
    """Sem esse numero, drenar antes E depois da coleta dobraria o gotejamento
    e a rodada invadiria a seguinte."""
    from promo.db import create_post

    class Entrega:
        def send_post(self, texto, imagem, para=None):
            return None

    monkeypatch.setattr(pipeline, "build_delivery", Entrega)
    monkeypatch.setattr(pipeline, "_drip_gap", lambda: 30.0)
    for i in range(3):
        create_post(banco_em_memoria, f"ml:MLB{i}", 10.0, 20.0, 50.0, f"texto {i}")

    relogio = Relogio()
    gasto = pipeline.flush_pending(
        1000.0, sleep=relogio.sleep, monotonic=relogio.monotonic
    )

    assert gasto == 60.0  # dois intervalos de 30s entre tres posts


def test_orcamento_estourado_tambem_devolve_o_gasto(monkeypatch, banco_em_memoria):
    from promo.db import create_post

    class Entrega:
        def send_post(self, texto, imagem, para=None):
            return None

    monkeypatch.setattr(pipeline, "build_delivery", Entrega)
    monkeypatch.setattr(pipeline, "_drip_gap", lambda: 40.0)
    for i in range(5):
        create_post(banco_em_memoria, f"ml:MLB{i}", 10.0, 20.0, 50.0, f"texto {i}")

    relogio = Relogio()
    gasto = pipeline.flush_pending(
        100.0, sleep=relogio.sleep, monotonic=relogio.monotonic
    )

    assert gasto == 80.0  # parou antes do terceiro intervalo, que estouraria


# ---------- deliver drena mesmo sem oferta nova ----------


def test_deliver_sem_oferta_nova_ainda_drena(monkeypatch):
    chamadas: list[float | None] = []
    monkeypatch.setattr(
        pipeline,
        "flush_pending",
        lambda orcamento=None: chamadas.append(orcamento) or 0.0,
    )

    pipeline.deliver([], 300.0)

    assert chamadas == [300.0]


def test_deliver_repassa_o_orcamento(monkeypatch, banco_em_memoria):
    chamadas: list[float | None] = []
    monkeypatch.setattr(
        pipeline,
        "flush_pending",
        lambda orcamento=None: chamadas.append(orcamento) or 7.0,
    )

    assert pipeline.deliver([], 42.0) == 7.0
    assert chamadas == [42.0]


# ---------- a ordem dentro da rodada ----------


@pytest.fixture
def rodada_instrumentada(monkeypatch, banco_em_memoria):
    """Grava a ordem em que a rodada chama drenagem e coleta."""
    ordem: list[str] = []

    monkeypatch.setattr(pipeline, "build_sources", lambda: [])
    monkeypatch.setattr(pipeline, "load_watchlist", lambda: [])
    monkeypatch.setattr(pipeline, "load_categories", lambda: [])
    monkeypatch.setattr(pipeline, "load_priority", lambda: [])
    monkeypatch.setattr(pipeline, "run_interval_seconds", lambda: 900)

    def coleta(*a, **k):
        ordem.append("coleta")
        return []

    def drena(orcamento=None):
        ordem.append(f"drena({orcamento:.0f})" if orcamento is not None else "drena()")
        return 0.0

    monkeypatch.setattr(pipeline, "collect", coleta)
    monkeypatch.setattr(pipeline, "flush_pending", drena)
    return ordem


def test_a_fila_drena_antes_da_coleta(rodada_instrumentada):
    """A ordem e o conserto: a coleta pode levar 22 minutos, e a fila nao pode
    esperar por ela."""
    pipeline.run()

    assert rodada_instrumentada[0] == "drena(720)"
    assert "coleta" in rodada_instrumentada
    assert rodada_instrumentada.index("drena(720)") < rodada_instrumentada.index(
        "coleta"
    )


def test_drena_de_novo_depois_da_coleta(rodada_instrumentada):
    """As ofertas escritas nesta rodada tem que sair nesta rodada."""
    pipeline.run()

    assert len([p for p in rodada_instrumentada if p.startswith("drena")]) == 2


def test_o_orcamento_de_gotejamento_e_um_so_para_a_rodada(
    monkeypatch, banco_em_memoria
):
    """Duas drenagens nao podem dobrar o tempo de gotejamento: o que a primeira
    gastar sai do que a segunda tem para gastar."""
    orcamentos: list[float] = []

    monkeypatch.setattr(pipeline, "build_sources", lambda: [])
    monkeypatch.setattr(pipeline, "load_watchlist", lambda: [])
    monkeypatch.setattr(pipeline, "load_categories", lambda: [])
    monkeypatch.setattr(pipeline, "load_priority", lambda: [])
    monkeypatch.setattr(pipeline, "run_interval_seconds", lambda: 900)
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [])

    def drena(orcamento=None):
        orcamentos.append(orcamento)
        return 500.0  # a primeira drenagem gasta 500s

    monkeypatch.setattr(pipeline, "flush_pending", drena)
    pipeline.run()

    assert orcamentos == [720.0, 220.0]


def test_dry_run_nao_envia_nada(monkeypatch, banco_em_memoria):
    """`--dry-run` imprime o texto; mandar pro grupo seria o oposto do pedido."""
    chamadas: list = []

    monkeypatch.setattr(pipeline, "build_sources", lambda: [])
    monkeypatch.setattr(pipeline, "load_watchlist", lambda: [])
    monkeypatch.setattr(pipeline, "load_categories", lambda: [])
    monkeypatch.setattr(pipeline, "load_priority", lambda: [])
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [])
    monkeypatch.setattr(
        pipeline,
        "flush_pending",
        lambda orcamento=None: chamadas.append(orcamento) or 0.0,
    )

    pipeline.run(dry_run=True)

    assert chamadas == []


# ---------- a entrega quebrada nao pode custar a coleta ----------


def test_falha_na_drenagem_nao_impede_a_coleta(monkeypatch, banco_em_memoria, caplog):
    """O historico de preco continua valendo com o WhatsApp fora -- e e ele que
    faz a oferta virar verificada depois. Perder a coleta por causa da entrega
    seria pagar duas vezes pela mesma queda."""
    import logging

    coletou: list[bool] = []

    monkeypatch.setattr(pipeline, "build_sources", lambda: [])
    monkeypatch.setattr(pipeline, "load_watchlist", lambda: [])
    monkeypatch.setattr(pipeline, "load_categories", lambda: [])
    monkeypatch.setattr(pipeline, "load_priority", lambda: [])
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: coletou.append(True) or [])

    chamadas = []

    def drena(orcamento=None):
        chamadas.append(orcamento)
        if len(chamadas) == 1:
            raise RuntimeError("Evolution fora do ar")
        return 0.0

    monkeypatch.setattr(pipeline, "flush_pending", drena)

    with caplog.at_level(logging.WARNING):
        pipeline.run()

    assert coletou == [True]
    assert "Evolution fora do ar" in caplog.text


def test_configuracao_faltando_ainda_para_o_daemon(monkeypatch, banco_em_memoria):
    """Entrega mal configurada nao pode virar coleta rodando para sempre sem
    nunca postar. O worker trata `MissingConfig` saindo com codigo 2."""
    from promo.config import MissingConfig

    monkeypatch.setattr(pipeline, "build_sources", lambda: [])
    monkeypatch.setattr(pipeline, "load_watchlist", lambda: [])
    monkeypatch.setattr(pipeline, "load_categories", lambda: [])
    monkeypatch.setattr(pipeline, "load_priority", lambda: [])
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [])

    def drena(orcamento=None):
        raise MissingConfig("Falta EVOLUTION_API_KEY no .env")

    monkeypatch.setattr(pipeline, "flush_pending", drena)

    with pytest.raises(MissingConfig):
        pipeline.run()

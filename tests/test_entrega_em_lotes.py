"""A rodada entrega de tres em tres, e nao so depois do ultimo texto.

O `deliver` ficava depois do laco inteiro de escrita, o que era irrelevante
enquanto a rodada selecionava 6 ofertas. Com o teto do repasse removido em
16/09/2026 ela passou a selecionar 25, e o Gemini leva de 10 a 40s por texto:
o grupo ficava mudo por varios minutos DEPOIS de a selecao terminar, esperando
o ultimo texto de uma fila que ele nem ia drenar na mesma rodada.

O que estes testes guardam e o tamanho da espera ate o primeiro post, e o fato
de o orcamento de gotejamento continuar sendo um so para a rodada inteira --
lote nao pode virar desculpa para gotejar o dobro.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402


def scored(n: int) -> ScoredOffer:
    offer = Offer(
        source="mercadolivre",
        external_id=f"MLB{n}",
        title=f"Produto Numero {n}",
        price=100.0,
        url=f"https://produto.mercadolivre.com.br/MLB-{n}",
        original_price=200.0,
    )
    return ScoredOffer(
        offer=offer,
        baseline=200.0,
        discount_pct=50.0,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


@pytest.fixture
def rodada(monkeypatch, tmp_path):
    """Uma rodada que ja tem ofertas escolhidas, sem coletar nada.

    O caminho de selecao inteiro fica de fora: o que se mede aqui e o ritmo da
    ESCRITA e da ENTREGA, que e o que mudou.
    """
    entregas: list[int] = []

    monkeypatch.setattr(pipeline, "run_interval_seconds", lambda: 900)
    monkeypatch.setattr(
        pipeline, "deliver", lambda lote, orcamento=None: entregas.append(len(lote)) or 0.0
    )
    return entregas


def test_o_lote_e_de_tres():
    """Tres chamadas do Gemini ate o primeiro post, e nao vinte e cinco."""
    assert pipeline._LOTE_DE_ENTREGA == 3


def test_o_lote_e_pequeno_o_bastante_para_valer():
    """Lote maior que a cota de uma rodada antiga anularia o conserto."""
    assert 1 <= pipeline._LOTE_DE_ENTREGA <= 5


def test_deliver_aceita_lote_parcial(monkeypatch, tmp_path):
    """A entrega parcial e uma chamada normal de `deliver`, com menos posts."""
    chamadas: list[tuple[int, float | None]] = []
    monkeypatch.setattr(
        pipeline,
        "flush_pending",
        lambda orcamento=None: chamadas.append(("flush", orcamento)) or 5.0,
    )
    monkeypatch.setattr(pipeline, "_gravar_post", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "load_grupos", lambda: [pipeline.GrupoDestino("", "g")])

    gasto = pipeline.deliver([(scored(1), "texto")], 100.0)

    assert gasto == 5.0
    assert chamadas == [("flush", 100.0)]


def test_o_orcamento_e_um_so_para_a_rodada(monkeypatch):
    """Cada lote desconta do que o proximo tem para gastar.

    Sem a subtracao, tres lotes gotejariam 3x o orcamento e a rodada invadiria
    a seguinte -- o defeito que o `flush_pending` ja evitava dentro de uma
    chamada so.
    """
    orcamentos: list[float] = []
    monkeypatch.setattr(
        pipeline,
        "flush_pending",
        lambda orcamento=None: orcamentos.append(orcamento) or 30.0,
    )
    monkeypatch.setattr(pipeline, "_gravar_post", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "load_grupos", lambda: [pipeline.GrupoDestino("", "g")])

    orcamento = 100.0
    gasto = 0.0
    for _ in range(3):
        gasto += pipeline.deliver([(scored(1), "t")], max(0.0, orcamento - gasto))

    assert orcamentos == [100.0, 70.0, 40.0]
    assert gasto <= 90.0


def test_orcamento_estourado_nao_fica_negativo(monkeypatch):
    """`max(0.0, ...)` e o que impede um orcamento negativo virar drenagem
    infinita na ultima entrega da rodada."""
    monkeypatch.setattr(pipeline, "flush_pending", lambda orcamento=None: orcamento)
    monkeypatch.setattr(pipeline, "_gravar_post", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "load_grupos", lambda: [pipeline.GrupoDestino("", "g")])

    assert pipeline.deliver([], max(0.0, 100.0 - 250.0)) == 0.0

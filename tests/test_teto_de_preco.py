"""O grupo foca em produto barato, sem deixar o caro de fora.

Nao e teto: uma queda real num notebook de R$ 3.000 continua sendo real, e
variedade de faixa e o que segura audiencia. E prioridade -- as vagas vao
primeiro para a faixa de foco, e `PRICE_FOCUS_RESERVE` garante que o produto
caro ainda apareca em vez de ser empurrado para fora todas as vezes.

Medido antes: 26 dos 59 posts passavam de R$ 500, 15 deles de R$ 1.000.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import _priorizar_baratos  # noqa: E402


def oferta(preco: float, external_id: str, fonte: str = "ml_ofertas") -> ScoredOffer:
    return ScoredOffer(
        offer=Offer(
            source=fonte,
            external_id=external_id,
            title=f"Produto {external_id}",
            price=preco,
            url=f"https://exemplo/{external_id}",
        ),
        baseline=preco * 2,
        discount_pct=50.0,
        observations=9,
        lowest_ever=False,
        verified=True,
    )


def precos(saida) -> list[float]:
    return [s.offer.price for s in saida]


def test_baratos_ficam_com_a_maior_parte_das_vagas(monkeypatch):
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "1")
    candidatos = [oferta(50.0 + i, f"B{i}") for i in range(8)]
    candidatos += [oferta(2000.0 + i, f"C{i}") for i in range(4)]

    saida = _priorizar_baratos(candidatos, 5)

    assert sum(1 for p in precos(saida) if p <= 300) == 4
    assert sum(1 for p in precos(saida) if p > 300) == 1


def test_produto_caro_nao_e_excluido(monkeypatch):
    """O requisito mudou de teto para foco: caro entra, so nao domina."""
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "1")
    candidatos = [oferta(50.0 + i, f"B{i}") for i in range(20)]
    candidatos += [oferta(2942.0, "CARO")]

    saida = _priorizar_baratos(candidatos, 5)

    assert 2942.0 in precos(saida)


def test_sem_candidato_caro_o_barato_preenche_tudo(monkeypatch):
    """A reserva nao pode virar vaga perdida."""
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "1")
    candidatos = [oferta(50.0 + i, f"B{i}") for i in range(8)]

    saida = _priorizar_baratos(candidatos, 5)

    assert len(saida) == 5
    assert all(p <= 300 for p in precos(saida))


def test_sem_candidato_barato_o_caro_preenche_tudo(monkeypatch):
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "1")
    candidatos = [oferta(2000.0 + i, f"C{i}") for i in range(8)]

    saida = _priorizar_baratos(candidatos, 5)

    assert len(saida) == 5


def test_reserva_maior_abre_mais_espaco_ao_caro(monkeypatch):
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "2")
    candidatos = [oferta(50.0 + i, f"B{i}") for i in range(8)]
    candidatos += [oferta(2000.0 + i, f"C{i}") for i in range(4)]

    saida = _priorizar_baratos(candidatos, 5)

    assert sum(1 for p in precos(saida) if p > 300) == 2


def test_foco_desligado_mantem_o_comportamento_antigo(monkeypatch):
    """PRICE_FOCUS_MAX=0 volta ao rodizio por fonte puro, sem olhar preco."""
    monkeypatch.setenv("PRICE_FOCUS_MAX", "0")
    candidatos = [oferta(2000.0, "C1"), oferta(50.0, "B1")]

    saida = _priorizar_baratos(candidatos, 2)

    assert precos(saida) == [2000.0, 50.0]


def test_rodizio_por_fonte_continua_valendo_dentro_da_faixa(monkeypatch):
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "0")
    candidatos = [oferta(50.0 + i, f"V{i}", "ml_ofertas") for i in range(4)]
    candidatos += [oferta(60.0 + i, f"C{i}", "mercadolivre") for i in range(4)]

    saida = _priorizar_baratos(candidatos, 4)
    fontes = [s.offer.source for s in saida]

    assert fontes.count("mercadolivre") == 2
    assert fontes.count("ml_ofertas") == 2

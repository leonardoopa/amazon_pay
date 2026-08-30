"""Vaga garantida para os temas que o grupo pediu.

Muitas mulheres entraram no grupo e pediram cabelo, pele e suplemento. A
watchlist cresceu de 104 para 132 termos e trouxe 252 produtos novos de
beleza para a carteira -- e a primeira rodada depois disso nao postou
nenhum deles. As cinco vagas ficaram com -72%, -67% e -54% de outras
categorias.

Nao havia nada errado com aquelas ofertas. O ranking decide por numero, e o
numero maior mora onde estiver; o que faltava era publico, e publico nao
aparece na ordenacao. Dai a reserva.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import (  # noqa: E402
    _priorizar_temas,
    e_prioritaria,
    load_priority,
)

TEMAS = ["wella", "protetor solar", "melatonina"]


@pytest.fixture(autouse=True)
def reserva_de_duas(monkeypatch):
    """A reserva vem do .env e e 0 por padrao (desligada)."""
    monkeypatch.setenv("PRIORITY_RESERVE", "2")
    monkeypatch.setenv("PRICE_FOCUS_MAX", "0")


def oferta(titulo: str, preco: float, desconto: float, fonte="mercadolivre"):
    return ScoredOffer(
        offer=Offer(
            source=fonte,
            external_id=f"MLB{abs(hash(titulo)) % 10**6}",
            title=titulo,
            price=preco,
            url="https://mercadolivre.com.br/p/MLB1",
        ),
        baseline=preco / (1 - desconto / 100),
        discount_pct=desconto,
        observations=9,
        lowest_ever=False,
        verified=False,
    )


# ---------- reconhecer o tema ----------


def test_reconhece_o_tema_no_titulo():
    assert e_prioritaria(oferta("Máscara Wella Blondorplex 150ml", 124.0, 48).offer, TEMAS)


def test_acento_e_caixa_nao_atrapalham():
    """O vendedor digita o titulo; o tema e escrito a mao no watchlist.json."""
    temas = ["mascara capilar"]
    assert e_prioritaria(oferta("MÁSCARA CAPILAR Truss", 90.0, 20).offer, temas)


def test_titulo_sem_tema_nao_e_prioritario():
    assert not e_prioritaria(oferta("Smart Tv 43 Samsung Crystal", 1500.0, 30).offer, TEMAS)


def test_sem_temas_nada_e_prioritario():
    assert not e_prioritaria(oferta("Shampoo Wella", 80.0, 24).offer, [])


# ---------- a reserva ----------


def test_prioritaria_entra_mesmo_com_desconto_menor():
    """O caso que motivou tudo: -29% de pele contra -72% de outra coisa."""
    candidatas = [
        oferta("Ventilador Portatil Mini", 58.80, 72),
        oferta("Escova Limpeza Mamadeira", 25.74, 67),
        oferta("Air Fryer WAP Barbecue", 846.0, 54),
        oferta("Protetor Solar Anthelios FPS 80", 79.90, 33),
    ]

    titulos = [s.offer.title for s in _priorizar_temas(candidatas, 3, TEMAS)]

    assert "Protetor Solar Anthelios FPS 80" in titulos


def test_a_reserva_e_teto_e_nao_toma_a_rodada():
    """Rodada so de tema prioritario nao pode virar rodada so de shampoo."""
    candidatas = [oferta(f"Shampoo Wella {i}", 80.0 + i, 40) for i in range(5)]
    candidatas += [oferta("Smart Tv Philco", 1200.0, 50)]

    saida = _priorizar_temas(candidatas, 3, TEMAS)
    prioritarias = [s for s in saida if e_prioritaria(s.offer, TEMAS)]

    assert len(saida) == 3
    assert len(prioritarias) == 2


def test_sem_candidato_prioritario_nenhuma_vaga_se_perde():
    """Reserva vazia nao pode encolher a rodada."""
    candidatas = [oferta(f"Smart Tv {i}", 1000.0 + i, 50) for i in range(4)]

    assert len(_priorizar_temas(candidatas, 3, TEMAS)) == 3


def test_reserva_sobrando_volta_para_o_prioritario():
    """Se o resto da fila nao encheu, o prioritario que ficou de fora concorre
    de novo -- senao a reserva viraria vaga perdida no outro sentido."""
    candidatas = [oferta(f"Shampoo Wella {i}", 80.0 + i, 40) for i in range(4)]

    assert len(_priorizar_temas(candidatas, 3, TEMAS)) == 3


def test_reserva_zero_desliga(monkeypatch):
    """Padrao do .env e 0: quem nao configurou nao muda de comportamento."""
    monkeypatch.setenv("PRIORITY_RESERVE", "0")
    candidatas = [
        oferta("Ventilador Portatil", 58.80, 72),
        oferta("Escova Mamadeira", 25.74, 67),
        oferta("Protetor Solar Anthelios", 79.90, 33),
    ]

    saida = _priorizar_temas(candidatas, 2, TEMAS)

    assert [s.discount_pct for s in saida] == [72, 67]


def test_lista_de_temas_vazia_nao_muda_nada():
    candidatas = [
        oferta("Ventilador Portatil", 58.80, 72),
        oferta("Protetor Solar Anthelios", 79.90, 33),
    ]

    saida = _priorizar_temas(candidatas, 1, [])

    assert saida[0].discount_pct == 72


def test_reserva_maior_que_a_cota_nao_estoura(monkeypatch):
    monkeypatch.setenv("PRIORITY_RESERVE", "9")
    candidatas = [oferta("Shampoo Wella", 80.0, 40), oferta("Smart Tv", 900.0, 60)]

    assert len(_priorizar_temas(candidatas, 1, TEMAS)) == 1


def test_lista_vazia_nao_estoura():
    assert _priorizar_temas([], 3, TEMAS) == []


def test_o_foco_em_barato_continua_valendo_dentro_da_reserva(monkeypatch):
    """A reserva muda quem concorre pela vaga, nao o criterio que decide."""
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "0")
    candidatas = [
        oferta("Kit Wella Oil Reflections Profissional", 612.0, 55),
        oferta("Máscara Wella Oil Reflections 150ml", 116.0, 47),
        oferta("Smart Tv Philco", 1200.0, 60),
    ]

    saida = _priorizar_temas(candidatas, 1, TEMAS)

    assert saida[0].offer.price == 116.0


# ---------- o arquivo de verdade ----------


def test_o_watchlist_traz_os_temas_pedidos():
    temas = load_priority()

    assert {"wella", "truss", "cerave", "protetor solar", "melatonina"} <= set(temas)


def test_os_temas_ja_vem_normalizados():
    """`e_prioritaria` normaliza so o titulo; o tema tem que chegar pronto."""
    assert all(t == t.lower() and t.isascii() for t in load_priority())


def test_tema_curto_demais_nao_entra():
    """"oleo" sozinho pegaria oleo de motor, "kit" pegaria a watchlist inteira."""
    assert all(len(t) >= 5 for t in load_priority())


def test_tema_vazio_e_ignorado(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(
        json.dumps({"keywords": [], "priority": ["wella", "", "   "]}), encoding="utf-8"
    )

    assert load_priority(caminho) == ["wella"]


def test_priority_ausente_e_o_normal(tmp_path):
    """Campo opcional: watchlist antiga nao pode quebrar."""
    caminho = tmp_path / "w.json"
    caminho.write_text(json.dumps({"keywords": [{"term": "x"}]}), encoding="utf-8")

    assert load_priority(caminho) == []

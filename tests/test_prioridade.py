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
    """"oleo" sozinho pegaria oleo de motor, "kit" pegaria a watchlist inteira.

    Quatro e o piso porque "nike" e "puma" tem quatro letras e sao marcas que
    o grupo pediu pelo nome. Nao ha palavra comum de produto em portugues que
    as contenha, entao o falso positivo que o piso de cinco evitava nao existe
    para essas duas.
    """
    assert all(len(t) >= 4 for t in load_priority())


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


# ---------- preco e desconto nao barram o tema ----------
#
# "esses produtos podemos deixar com preco alto, nao tem nenhum problema. O
# ideal e que sempre que eles aparecerem, independente de preco ou desconto,
# eles tem que ser enviados no grupo -- esses sao os que mais convertem."
#
# A conta e outra nesses temas: -9% num shampoo de salao de R$ 400 e mais
# dinheiro que -60% num item de R$ 25, e a pessoa que entrou no grupo pelo
# nome do produto ja quer o produto.


def test_o_foco_em_barato_pode_ser_desligado_dentro_da_reserva(monkeypatch):
    monkeypatch.setenv("PRIORITY_IGNORES_PRICE_FOCUS", "1")
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "0")
    candidatas = [
        oferta("Kit Wella Oil Reflections Profissional", 612.0, 55),
        oferta("Máscara Wella Oil Reflections 150ml", 116.0, 47),
    ]

    saida = _priorizar_temas(candidatas, 1, TEMAS)

    assert saida[0].offer.price == 612.0  # o caro nao perde mais a vaga


def test_desligar_o_foco_nao_afeta_o_resto_da_fila(monkeypatch):
    """Fora da reserva, produto caro continua cedendo vez ao barato."""
    monkeypatch.setenv("PRIORITY_IGNORES_PRICE_FOCUS", "1")
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "0")
    monkeypatch.setenv("PRIORITY_RESERVE", "0")
    candidatas = [
        oferta("Smart Tv Philco 50", 1200.0, 60),
        oferta("Fone Bluetooth JBL", 99.0, 20),
    ]

    saida = _priorizar_temas(candidatas, 1, TEMAS)

    assert saida[0].offer.price == 99.0


# ---------- piso de desconto proprio ----------


def regras(min_discount: float = 5.0):
    from promo.config import Rules

    return Rules(
        min_discount_pct=min_discount,
        baseline_window_days=60,
        min_observations=4,
        repost_cooldown_days=3,
        max_offers_per_run=5,
    )


def anuncio(titulo: str, preco: float, riscado: float | None = None) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB1",
        title=titulo,
        price=preco,
        url="https://mercadolivre.com.br/p/MLB1",
        original_price=riscado,
    )


def test_o_tema_usa_o_piso_proprio(monkeypatch):
    from promo.pipeline import regras_do_tema

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    wella = anuncio("Shampoo Wella Invigo Nutri-Enrich", 399.90, 441.80)

    assert regras_do_tema(wella, regras(), TEMAS).min_discount_pct == 0


def test_fora_do_tema_o_piso_geral_continua(monkeypatch):
    from promo.pipeline import regras_do_tema

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    tv = anuncio("Smart Tv Philco 50", 1200.0, 1300.0)

    assert regras_do_tema(tv, regras(), TEMAS).min_discount_pct == 5.0


def test_sem_piso_configurado_nada_muda(monkeypatch):
    """Vazio e o padrao: quem nao configurou usa o piso geral nos dois casos."""
    from promo.pipeline import regras_do_tema

    monkeypatch.delenv("PRIORITY_MIN_DISCOUNT_PCT", raising=False)
    wella = anuncio("Shampoo Wella Invigo", 399.90, 441.80)

    assert regras_do_tema(wella, regras(), TEMAS).min_discount_pct == 5.0


def test_o_piso_zero_deixa_passar_o_desconto_de_9_por_cento(monkeypatch, tmp_path):
    """O caso real: -9% num Wella de R$ 400, recusado pelo piso de 5%... nao,
    aceito -- mas -2% seria recusado, e e esse que o piso do tema libera."""
    import sqlite3

    from promo.db import SCHEMA
    from promo.pipeline import regras_do_tema
    from promo.scoring import score_campaign

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    quase_nada = anuncio("Shampoo Wella Invigo", 435.00, 441.80)  # -1,5%

    geral = score_campaign(conn, quase_nada, regras())
    tema = score_campaign(conn, quase_nada, regras_do_tema(quase_nada, regras(), TEMAS))

    assert geral is None
    assert tema is not None


def test_o_piso_do_tema_nao_inventa_desconto(monkeypatch):
    """Piso 0 nao e "sem desconto": sem preco riscado maior que o atual nao ha
    o que anunciar, e o post nunca afirma uma queda que nao existe."""
    import sqlite3

    from promo.db import SCHEMA
    from promo.pipeline import regras_do_tema
    from promo.scoring import score_campaign

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    sem_queda = anuncio("Shampoo Wella Invigo", 441.80, None)

    assert score_campaign(conn, sem_queda, regras_do_tema(sem_queda, regras(), TEMAS)) is None


def test_o_cooldown_de_repeticao_continua_valendo_no_tema(monkeypatch):
    """"Sempre que aparecer" nao pode virar o mesmo shampoo a cada rodada --
    e isso que faz sair do grupo justamente quem o tema queria trazer."""
    from promo.pipeline import regras_do_tema

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    wella = anuncio("Shampoo Wella Invigo", 399.90, 441.80)

    assert regras_do_tema(wella, regras(), TEMAS).repost_cooldown_days == 3


# ---------- watchlist: teto de preco dos termos do tema ----------


def test_os_termos_do_tema_nao_tem_teto_de_preco():
    """Teto de R$ 400 no termo cortava o kit Wella de R$ 612 antes de ele
    chegar a pontuacao -- o filtro corria na coleta, nao na selecao."""
    from promo.pipeline import load_priority, load_watchlist

    temas = load_priority()
    com_teto = [
        w.term
        for w in load_watchlist()
        if w.max_price is not None
        and any(tema in w.term for tema in temas)
    ]

    assert com_teto == []


def test_os_termos_fora_do_tema_mantem_teto():
    """Soltar o teto e para o tema, nao para a watchlist inteira: sem teto,
    'notebook' traz servidor de R$ 40 mil."""
    from promo.pipeline import load_watchlist

    tetos = {w.term: w.max_price for w in load_watchlist()}

    assert tetos["notebook"] == 5000
    assert tetos["monitor gamer"] == 3000

"""Fonte prioritaria: o que o grupo manda, sai.

Pedido do dono em 03/10/2026 sobre o Achadinhos Pelando VIP #BVA: "preciso que
os produtos desse grupo sejam enviados". Medido em 30 horas de mensagens reais,
de 62 produtos da Amazon que o grupo postou so 18 chegaram a virar post. O resto
morreu em tres lugares: a fila acima de 30 (que barrava todo repasse), a trava
de 24 h de "mesmo nome e marca" (que barrou as variantes do cafe L'OR) e a
lista de exclusao.

`prioridade: true` numa fonte tira o que vem dela dessas tres travas e poe na
faixa 2 da entrega. A trava do MESMO produto continua valendo para todos.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.sources.grupo_wa import Pista  # noqa: E402


def oferta(titulo: str, external_id: str, source: str = "amazon") -> Offer:
    return Offer(
        source=source, external_id=external_id, title=titulo, price=50.0,
        url=f"https://exemplo.com/{external_id}",
    )


def pontuada(titulo: str, external_id: str, desconto: float = 0.0) -> ScoredOffer:
    return ScoredOffer(
        offer=oferta(titulo, external_id), baseline=50.0, discount_pct=desconto,
        observations=0, lowest_ever=False, verified=False,
    )


@pytest.fixture(autouse=True)
def limpa():
    pipeline._DA_FONTE_PRIORITARIA.clear()
    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()
    yield
    pipeline._DA_FONTE_PRIORITARIA.clear()
    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()


def prioritaria(*ids: str) -> None:
    pipeline._DA_FONTE_PRIORITARIA.update(ids)
    pipeline._VISTOS_EM_OUTRO_GRUPO.update(ids)


# ---------- faixa da entrega ----------


def test_tudo_da_fonte_prioritaria_ganha_a_faixa_2_mesmo_sem_ser_comida():
    prioritaria("B0NOTEBOOK")

    assert pipeline._prioridade_do_post(oferta("Notebook Dell i5-1334U", "B0NOTEBOOK")) == 2


def test_repasse_comum_continua_na_faixa_1():
    pipeline._VISTOS_EM_OUTRO_GRUPO.add("B0JAQUETA")

    assert pipeline._prioridade_do_post(oferta("Jaqueta Masculina", "B0JAQUETA")) == 1


# ---------- as tres travas ----------


def test_a_lista_de_exclusao_nao_barra_a_fonte_prioritaria():
    """Smartphone esta na lista de exclusao do dono; num grupo mandado enviar por
    inteiro, a curadoria e a do grupo."""
    prioritaria("B0PHONE")
    barrados = ["smartphone"]

    assert not pipeline._barrada_na_selecao(
        pontuada("Smartphone Realme C73 256gb", "B0PHONE"), barrados, []
    )


def test_a_lista_de_exclusao_continua_barrando_o_resto():
    barrados = ["smartphone"]

    assert pipeline._barrada_na_selecao(
        pontuada("Smartphone Realme C73 256gb", "B0OUTRO"), barrados, []
    )


def test_a_trava_de_nome_e_marca_nao_segura_a_fonte_prioritaria():
    """O cafe L'OR Sul da Bahia (outro ASIN) nao pode ser barrado pelo
    L'OR Intense que saiu de manha."""
    prioritaria("B0BZWRBZ11")
    recentes = ["L'OR Café Solúvel Intense Pote De Vidro 130G"]

    assert not pipeline._repete_nome_e_marca(
        pontuada("[REC] L'OR Café Solúvel Sul Da Bahia L'Or Vidro 84G", "B0BZWRBZ11"),
        recentes,
    )


def test_a_trava_de_nome_e_marca_continua_para_o_resto():
    recentes = ["L'OR Café Solúvel Intense Pote De Vidro 130G"]

    assert pipeline._repete_nome_e_marca(
        pontuada("L'OR Café Solúvel Intense Pote De Vidro 130G", "B0OUTRO"), recentes
    )


# ---------- sai antes de qualquer corte ----------


def test_separa_a_fonte_prioritaria_de_todas_as_listas():
    prioritaria("P1", "P2")
    picked = [pontuada("Verificada", "V1"), pontuada("Do grupo, medida por nos", "P1")]
    repasses = [pontuada("Vitrine", "R1")]
    pistas = [pontuada("Pista comum", "C1"), pontuada("Pista do grupo", "P2")]

    prioritarias, (picked, repasses, pistas) = pipeline._separar_da_fonte_prioritaria(
        picked, repasses, pistas
    )

    assert {s.offer.external_id for s in prioritarias} == {"P1", "P2"}
    assert [s.offer.external_id for s in picked] == ["V1"]
    assert [s.offer.external_id for s in repasses] == ["R1"]
    assert [s.offer.external_id for s in pistas] == ["C1"]


def test_o_mesmo_produto_em_duas_listas_conta_uma_vez():
    prioritaria("P1")

    prioritarias, _ = pipeline._separar_da_fonte_prioritaria(
        [pontuada("Produto", "P1")], [pontuada("Produto", "P1")]
    )

    assert len(prioritarias) == 1


def test_sem_fonte_prioritaria_nada_se_separa():
    picked = [pontuada("A", "A1")]

    prioritarias, (picked2,) = pipeline._separar_da_fonte_prioritaria(picked)

    assert prioritarias == [] and picked2 == picked


# ---------- a coleta marca o que vem da fonte ----------


@pytest.fixture
def coleta(monkeypatch, banco_em_memoria):
    from promo.config import EvolutionConfig
    from promo.sources import grupo_wa

    monkeypatch.setenv("AMAZON_PARTNER_TAG", "comunidaded0a-20")
    monkeypatch.setattr(
        EvolutionConfig,
        "load",
        classmethod(
            lambda cls, *a, **k: EvolutionConfig(
                base_url="http://e", api_key="k", instance="i", group_jid="g@g.us"
            )
        ),
    )
    # A fonte comum: uma pista do ML. A prioritaria e a do Pelando, abaixo.
    monkeypatch.setattr(
        grupo_wa, "pistas",
        lambda **k: [Pista(external_id="MLB_COMUM", titulo="Comum", origem="xet")],
    )
    monkeypatch.setattr("promo.pipeline._amazon_do_grupo", lambda c, f: [])
    from promo.sources import pelando_grupo

    monkeypatch.setattr(
        pelando_grupo, "ofertas_do_grupo",
        lambda *a, **k: (
            [Pista(external_id="MLB_BVA", titulo="Do BVA", origem="bva")],
            [],
        ),
    )
    monkeypatch.setattr(grupo_wa, "ler_mensagens", lambda *a, **k: [])


class MLFalso:
    def fetch_by_ids(self, alvos):
        return [
            Offer(source="mercadolivre", external_id=e, title=t, price=10.0, url="u")
            for e, t, _ in alvos
        ]


def fontes(monkeypatch, *itens):
    monkeypatch.setattr("promo.pipeline.load_grupos_fonte", lambda *a, **k: list(itens))


def test_a_coleta_marca_so_o_que_veio_da_fonte_com_prioridade(monkeypatch, coleta):
    fontes(
        monkeypatch,
        {"jid": "xet@g.us", "nome": "xet"},
        {"jid": "bva@g.us", "nome": "BVA", "tipo": "pelando", "prioridade": True},
    )

    pipeline.collect_de_outros_grupos(MLFalso())

    assert pipeline._DA_FONTE_PRIORITARIA == {"MLB_BVA"}
    # As duas contam como "veio de outro grupo": o que muda e a prioridade.
    assert {"MLB_BVA", "MLB_COMUM"} <= pipeline._VISTOS_EM_OUTRO_GRUPO


def test_sem_a_chave_nada_e_prioritario(monkeypatch, coleta):
    fontes(monkeypatch, {"jid": "bva@g.us", "nome": "BVA", "tipo": "pelando"})

    pipeline.collect_de_outros_grupos(MLFalso())

    assert pipeline._DA_FONTE_PRIORITARIA == set()


def test_a_marca_e_trocada_a_cada_leitura(monkeypatch, coleta):
    """O que foi prioritario ontem e reaparece hoje pela vitrine nao e especial."""
    prioritaria("ANTIGO")
    fontes(
        monkeypatch,
        {"jid": "bva@g.us", "nome": "BVA", "tipo": "pelando", "prioridade": True},
    )

    pipeline.collect_de_outros_grupos(MLFalso())

    assert "ANTIGO" not in pipeline._DA_FONTE_PRIORITARIA


def test_o_watchlist_real_marca_o_bva_como_prioritario():
    from promo.pipeline import load_grupos_fonte

    bva = next(f for f in load_grupos_fonte() if f["jid"] == "120363427661444901@g.us")

    assert bva["prioridade"] is True


def test_o_freio_tem_um_valor_padrao(monkeypatch):
    from promo.config import max_fonte_prioritaria_por_run

    monkeypatch.delenv("MAX_FONTE_PRIORITARIA_POR_RODADA", raising=False)

    assert max_fonte_prioritaria_por_run() == 20

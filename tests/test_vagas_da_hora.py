"""So entra na fila o que o grupo consegue enviar antes de o post expirar.

Medido no Geral em 18/11/2026, em 24h: cerca de 1.000 posts criados, 219
enviados e 781 expirados por preco velho. Cada expirado ja tinha pago uma
chamada do Gemini. Quem decide quanto sai e a entrega (`MAX_DO_GERAL_POR_HORA`
no Geral, `TEMATICO_GAP_SEGUNDOS` nos tematicos); selecionar alem disso so
troca qual post expira.

No mesmo dia o dono inverteu a ordem da fila: o repasse (1) e a nossa medicao
(0) passam na frente da comida e da fonte prioritaria (-1). A entrada na fila
segue a mesma ordem, entao o corte por falta de vaga deixa de fora quem sairia
por ultimo.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "11")
    monkeypatch.setenv("POST_MAX_AGE_MINUTES", "60")
    monkeypatch.setenv("TEMATICO_GAP_SEGUNDOS", "900")
    monkeypatch.delenv("MAX_PISTAS_POR_RUN", raising=False)
    monkeypatch.delenv("MAX_COMIDA_E_BEBIDA_POR_RODADA", raising=False)
    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()
    pipeline._DA_FONTE_PRIORITARIA.clear()
    pipeline._DRENAGEM_CONTINUA.clear()
    yield
    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()
    pipeline._DA_FONTE_PRIORITARIA.clear()
    pipeline._DRENAGEM_CONTINUA.clear()


def oferta(titulo: str, external_id: str) -> Offer:
    return Offer(
        source="amazon",
        external_id=external_id,
        title=titulo,
        price=89.9,
        url=f"https://www.amazon.com.br/dp/{external_id}",
        original_price=150.0,
    )


def pontuada(titulo: str, external_id: str) -> ScoredOffer:
    return ScoredOffer(
        offer=oferta(titulo, external_id),
        baseline=150.0,
        discount_pct=40.0,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


def repasse(titulo: str, external_id: str) -> ScoredOffer:
    pipeline._VISTOS_EM_OUTRO_GRUPO.add(external_id)
    return pontuada(titulo, external_id)


def do_bva(titulo: str, external_id: str) -> ScoredOffer:
    pipeline._DA_FONTE_PRIORITARIA.add(external_id)
    return repasse(titulo, external_id)


def titulos(lista: list[ScoredOffer]) -> list[str]:
    return [s.offer.title for s in lista]


# ---------- quanto cabe ----------


def test_o_geral_comporta_o_que_envia_no_prazo_menos_a_folga():
    """11 por hora, prazo de 60 min: 11 saem antes de expirar, e a fila fica
    em 8 -- os 20% de sobra absorvem quem chega depois e passa na frente."""
    assert pipeline._capacidade_da_fila("") == 8


def test_prazo_maior_comporta_mais(monkeypatch):
    monkeypatch.setenv("POST_MAX_AGE_MINUTES", "120")

    assert pipeline._capacidade_da_fila("") == 17


def test_o_tematico_comporta_o_que_o_intervalo_dele_deixa_sair():
    """900 s entre posts: 4 por hora, e a fila fica em 3."""
    assert pipeline._capacidade_da_fila("120363@g.us") == 3


def test_teto_minimo_ainda_deixa_um_post_entrar(monkeypatch):
    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "1")

    assert pipeline._capacidade_da_fila("") == 1


@pytest.mark.parametrize(
    "variavel, valor",
    [
        ("MAX_DO_GERAL_POR_HORA", "0"),  # Geral sem teto por hora
        ("POST_MAX_AGE_MINUTES", "0"),  # post que nunca expira
    ],
)
def test_sem_teto_ou_sem_prazo_o_geral_volta_a_fila_inteira(
    monkeypatch, variavel, valor
):
    monkeypatch.setenv(variavel, valor)

    assert pipeline._capacidade_da_fila("") is None
    assert pipeline._folga_na_fila([], "") is None


def test_tematico_sem_intervalo_nao_tem_limite(monkeypatch):
    monkeypatch.setenv("TEMATICO_GAP_SEGUNDOS", "0")

    assert pipeline._capacidade_da_fila("120363@g.us") is None


def test_a_folga_desconta_so_a_fila_do_proprio_grupo():
    pendentes = [{"grupo_jid": ""}] * 3 + [{"grupo_jid": "mulheres@g.us"}] * 5

    assert pipeline._folga_na_fila(pendentes, "") == 5
    assert pipeline._folga_na_fila(pendentes, "mulheres@g.us") == 0


# ---------- a ordem de entrada e a de saida ----------


def test_a_selecao_entra_na_ordem_da_fila():
    """Repasse, depois a nossa medicao, depois comida e #BVA. Dentro da mesma
    faixa vale a ordem que a selecao ja tinha dado."""
    selecao = [
        do_bva("Notebook Dell i5", "B0BVA"),
        repasse("Vodka Grey Goose 750ml", "B0VODKA"),
        repasse("Jaqueta Field & Stream", "B0JAQUETA"),
        pontuada("Console PlayStation 5 Slim", "B0PS5"),
    ]

    ordem = pipeline._na_ordem_da_fila(selecao, None)

    assert titulos(ordem) == [
        "Jaqueta Field & Stream",
        "Console PlayStation 5 Slim",
        "Notebook Dell i5",
        "Vodka Grey Goose 750ml",
    ]


def test_sem_vaga_fica_de_fora_quem_sairia_por_ultimo():
    selecao = [
        repasse("Vodka Grey Goose 750ml", "B0VODKA"),
        pontuada("Console PlayStation 5 Slim", "B0PS5"),
        repasse("Jaqueta Field & Stream", "B0JAQUETA"),
    ]

    ordem = pipeline._na_ordem_da_fila(selecao, 2)

    assert titulos(ordem) == ["Jaqueta Field & Stream", "Console PlayStation 5 Slim"]


def test_com_folga_a_comida_nao_tira_vaga_do_repasse():
    """Sem teto por hora a comida entra primeiro e desconta a vaga do resto.
    Com teto, ela vai para o fim, e quem corta e a folga do Geral."""
    novas = [
        repasse("Jaqueta A", "B0A"),
        repasse("Vodka Grey Goose 750ml", "B0VODKA"),
        repasse("Jaqueta B", "B0B"),
        repasse("Jaqueta C", "B0C"),
    ]

    admitidas = pipeline._admitir_pistas(novas, na_fila=0, ja_escolhidas=0, folga=2)

    assert titulos(admitidas) == ["Jaqueta A", "Jaqueta B", "Vodka Grey Goose 750ml"]


def test_com_folga_a_fila_dos_outros_grupos_nao_barra_a_pista():
    """78 pendentes somando todos os grupos travavam o repasse inteiro. Com
    teto por hora a conta e so a do Geral."""
    assert pipeline._quantas_pistas_cabem(na_fila=78, ja_escolhidas=6, folga=4) == 4


# ---------- a rodada nao escreve o que nao cabe ----------


class Redator:
    escritos: list[str] = []

    def write(self, scored, link, cupom, recentes, final):
        Redator.escritos.append(scored.offer.external_id)
        return f"Texto de {scored.offer.title}\n{link}"


class FonteFalsa:
    def __init__(self, nome: str) -> None:
        self.name = nome

    def affiliate_url(self, offer):
        return f"https://nosso.link/{offer.external_id}"


@pytest.fixture
def rodada(monkeypatch, banco_em_memoria):
    Redator.escritos = []
    monkeypatch.setattr(pipeline, "build_sources", lambda: [FonteFalsa("amazon")])
    monkeypatch.setattr(pipeline, "Copywriter", Redator)
    monkeypatch.setattr(pipeline, "cupons_vigentes", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "load_exclude", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "flush_pending", lambda *a, **k: 0.0)
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: [])
    pipeline._DRENAGEM_CONTINUA.set()

    ofertas = [oferta(f"Achado do BVA {n}", f"B0BVA{n}") for n in range(5)]
    for o in ofertas:
        pipeline._DA_FONTE_PRIORITARIA.add(o.external_id)
        pipeline._VISTOS_EM_OUTRO_GRUPO.add(o.external_id)
    monkeypatch.setattr(pipeline, "collect_rapido", lambda sources: list(ofertas))
    return banco_em_memoria


def posts_do_geral(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM posts WHERE grupo_jid = '' AND status = 'pending'"
    ).fetchone()[0]


def test_a_rodada_so_escreve_o_que_o_geral_envia_antes_de_expirar(monkeypatch, rodada):
    """Teto de 2 por hora: a fila do Geral comporta 1. Os outros quatro achados
    nao viram texto do Gemini."""
    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "2")

    pipeline.run(rapido=True)

    assert posts_do_geral(rodada) == 1
    assert len(Redator.escritos) == 1


def test_com_a_fila_do_geral_cheia_nada_e_escrito(monkeypatch, rodada):
    from promo.db import create_post, record_offer

    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "2")
    antigo = oferta("Post que ja esperava", "B0ANTIGO")
    record_offer(rodada, antigo)
    create_post(rodada, antigo.product_id, 89.9, 150.0, 40.0, "texto")

    pipeline.run(rapido=True)

    assert posts_do_geral(rodada) == 1
    assert Redator.escritos == []

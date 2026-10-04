"""A descoberta sai em fatias, e o prioritario leva a maior.

A rodada de descoberta virou silencio no grupo. Medido em producao em
02/09/2026: 190 termos a 20 candidatos cada, mais 33 categorias, deram 4.822
chamadas a ~4 por segundo -- 21 minutos entre o inicio da rodada e a primeira
mensagem, com a fila vazia esperando o tempo todo.

Fatiar troca "tudo de tres em tres horas" por "um pedaco a cada rodada". As
duas pontas melhoram: nenhuma rodada passa de poucos minutos, e com a
descoberta rodando em toda rodada a carteira e reciclada MAIS rapido do que
antes.

A fatia nao e igual para os dois pocos, de proposito. Sao 95 termos que casam
num tema do `priority` contra 95 gerais, e produto prioritario tem que
aparecer no grupo com mais frequencia -- frequencia de post comeca em
frequencia de coleta, porque oferta que ninguem reconsultou nao pode ser
escolhida.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.pipeline import Watch, _rodizio, fatiar_watchlist  # noqa: E402

TEMAS = ["wella", "nike", "whey"]


def lista(*termos: str) -> list[Watch]:
    return [Watch(term=t) for t in termos]


PRIORITARIOS = lista("shampoo wella", "tenis nike", "whey dux", "mascara wella")
GERAIS = lista("air fryer", "smart tv", "cadeira", "notebook")
TODOS = PRIORITARIOS + GERAIS


# ---------- o rodizio ----------


def test_rodizio_da_a_volta_no_fim():
    fatia, cursor = _rodizio([1, 2, 3, 4, 5], cursor=3, quantos=4)

    assert fatia == [4, 5, 1, 2]
    assert cursor == 2


def test_rodizio_nao_repete_dentro_da_mesma_fatia():
    """Pedir mais do que o poco tem devolve o poco, e nao itens repetidos."""
    fatia, _ = _rodizio([1, 2, 3], cursor=0, quantos=10)

    assert sorted(fatia) == [1, 2, 3]


def test_rodizio_de_poco_vazio_nao_estoura():
    assert _rodizio([], cursor=5, quantos=3) == ([], 5)


def test_rodizio_de_zero_itens_nao_mexe_no_cursor():
    assert _rodizio([1, 2, 3], cursor=2, quantos=0) == ([], 2)


# ---------- a fatia ----------


def test_sem_fatia_devolve_a_watchlist_inteira():
    """0 e o padrao: quem nao configurar nao muda de comportamento."""
    termos, cp, cg = fatiar_watchlist(TODOS, TEMAS, 0, 0.5)

    assert [w.term for w in termos] == [w.term for w in TODOS]
    assert (cp, cg) == (0, 0)


def test_a_fatia_respeita_o_tamanho_pedido():
    termos, _, _ = fatiar_watchlist(TODOS, TEMAS, por_rodada=4, fatia_prioritaria=0.5)

    assert len(termos) == 4


def test_o_prioritario_leva_a_cota_dele():
    termos, _, _ = fatiar_watchlist(TODOS, TEMAS, por_rodada=4, fatia_prioritaria=0.75)
    prioritarios = [w for w in termos if any(t in w.term for t in TEMAS)]

    assert len(prioritarios) == 3


def test_dois_cursores_independentes():
    """Com um cursor so, os dois pocos se alternariam na mesma volta e o
    prioritario seria reconsultado na mesma frequencia do resto."""
    _, cp1, cg1 = fatiar_watchlist(TODOS, TEMAS, 4, 0.5)
    _, cp2, cg2 = fatiar_watchlist(TODOS, TEMAS, 4, 0.5, cp1, cg1)

    assert (cp1, cg1) == (2, 2)
    assert (cp2, cg2) == (0, 0)


def test_o_poco_prioritario_gira_mais_rapido():
    """Quatro de cada, cota de 75%: o prioritario fecha a volta em 2 rodadas,
    o geral em 4."""
    vistos_p: list[str] = []
    vistos_g: list[str] = []
    cp = cg = 0
    for _ in range(2):
        termos, cp, cg = fatiar_watchlist(TODOS, TEMAS, 4, 0.75, cp, cg)
        for w in termos:
            alvo = vistos_p if any(t in w.term for t in TEMAS) else vistos_g
            alvo.append(w.term)

    assert set(vistos_p) == {w.term for w in PRIORITARIOS}
    assert len(set(vistos_g)) < len(GERAIS)


def test_a_cota_nao_usada_volta_para_o_geral():
    """Poucos prioritarios nao pode virar rodada menor: a vaga sobrando vai
    para o outro poco em vez de sumir."""
    watchlist = lista("shampoo wella") + GERAIS
    termos, _, _ = fatiar_watchlist(
        watchlist, TEMAS, por_rodada=4, fatia_prioritaria=0.75
    )

    assert len(termos) == 4


def test_sem_termo_prioritario_a_fatia_e_toda_geral():
    termos, _, _ = fatiar_watchlist(GERAIS, TEMAS, por_rodada=2, fatia_prioritaria=0.9)

    assert len(termos) == 2


def test_sem_tema_nenhum_ninguem_e_prioritario():
    termos, cp, _ = fatiar_watchlist(TODOS, [], por_rodada=4, fatia_prioritaria=0.75)

    assert len(termos) == 4
    assert cp == 0  # o poco prioritario ficou vazio, o cursor nao andou


def test_watchlist_vazia_nao_estoura():
    assert fatiar_watchlist([], TEMAS, 10, 0.5) == ([], 0, 0)


def test_fatia_maior_que_a_watchlist_devolve_tudo_sem_repetir():
    termos, _, _ = fatiar_watchlist(TODOS, TEMAS, por_rodada=99, fatia_prioritaria=0.5)

    assert sorted(w.term for w in termos) == sorted(w.term for w in TODOS)


def test_a_volta_completa_cobre_todo_termo():
    """Fatiar nao pode significar termo que nunca e consultado."""
    vistos: set[str] = set()
    cp = cg = 0
    for _ in range(4):
        termos, cp, cg = fatiar_watchlist(TODOS, TEMAS, 4, 0.5, cp, cg)
        vistos |= {w.term for w in termos}

    assert vistos == {w.term for w in TODOS}

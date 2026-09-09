"""Republicar o que o grupo vizinho posta, sem os nossos filtros de preco.

Pedido em 09/09/2026: "toda vez que eles mandarem algo no grupo deles
(promocao do Mercado Livre) a gente reenvie no nosso grupo com o nosso link,
com 100% de prioridade".

O que ja existia era so a ORDENACAO: a pista ia para o topo da fila. Nao
bastava, e a medicao mostrou por que -- a pista quase nunca CHEGAVA a ser
ordenada. `score` exige historico proprio, que produto recem-descoberto nao
tem, e `score_campaign` exige preco riscado no anuncio, presente em 9 de 13
medidos. As outras viravam so coleta.

Daqui saiu a terceira porta, `score_pista`, e com ela uma escolha editorial: o
post nao tem desconto nenhum para anunciar, nem medido nem riscado. O que
sustenta a publicacao e a curadoria de quem achou. O texto entao fala do preco
de hoje e nao pode escrever "De X por Y" nem porcentagem -- que e o que este
arquivo garante.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.scoring import score_pista  # noqa: E402


def oferta(preco: float = 155.0, disponivel: bool = True) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB60687768",
        title="Lattafa Bade'e Al Oud for glory 100ml",
        price=preco,
        url="https://mercadolivre.com.br/p/MLB60687768",
        available=disponivel,
    )


# ---------- a terceira porta ----------


def test_pista_sem_historico_e_sem_riscado_vira_post():
    """O caso que as outras duas portas recusam, e que motivou esta."""
    scored = score_pista(oferta())

    assert scored is not None
    assert scored.offer.price == 155.0


def test_pista_nao_afirma_desconto():
    """Sem baseline e sem riscado nao ha queda para anunciar."""
    scored = score_pista(oferta())

    assert scored.discount_pct == 0.0
    assert scored.baseline == scored.offer.price
    assert scored.verified is False
    assert scored.sem_medicao is True
    assert scored.lowest_ever is False


@pytest.mark.parametrize(
    "preco, disponivel",
    [(0.0, True), (-1.0, True), (155.0, False)],
    ids=["preco zero", "preco negativo", "indisponivel"],
)
def test_pista_sem_preco_util_fica_de_fora(preco, disponivel):
    """A porta e fraca, nao inexistente: sem preco valido nao ha post."""
    assert score_pista(oferta(preco, disponivel)) is None


# ---------- o texto ----------


def pontuada_sem_medicao() -> ScoredOffer:
    return score_pista(oferta())


def test_os_fatos_nao_carregam_preco_antigo():
    """O prompt nao pode receber um "De" que nao existe.

    Antes deste caminho, `_facts` mandava `baseline` sempre -- e com baseline
    igual ao preco o modelo receberia "De R$ 155,00 / Por R$ 155,00 / Desconto
    0%" e escreveria em cima disso.
    """
    from promo.copywriter import _facts

    fatos = _facts(pontuada_sem_medicao(), "https://mercadolivre.com/sec/abc")

    assert "De (media" not in fatos
    assert "Desconto contra a media" not in fatos
    assert "R$ 155,00" in fatos
    assert "NAO escreva porcentagem" in fatos


def test_o_texto_que_inventa_desconto_e_recusado():
    """A instrucao no prompt nao basta -- e a razao de todo `_reject_*`."""
    from promo.copywriter import _reject_unfounded_claims

    for inventado in (
        "Perfume Lattafa com 30% off, corre!",
        "De R$ 299,00 por R$ 155,00, aproveita",
        "Esse perfume caiu de preco, olha so",
        "Custava R$ 300 e agora ta R$ 155",
    ):
        with pytest.raises(RuntimeError, match="afirma desconto"):
            _reject_unfounded_claims(inventado, pontuada_sem_medicao())


def test_o_texto_honesto_passa():
    """Falar do preco de hoje e exatamente o que se espera."""
    from promo.copywriter import _reject_unfounded_claims

    _reject_unfounded_claims(
        "Lattafa Bade'e Al Oud saindo por R$ 155,00. Perfume arabe que dura o "
        "dia inteiro.",
        pontuada_sem_medicao(),
    )


def test_o_cupom_continua_permitido_na_pista():
    """Cupom e desconto de verdade, e nao depende de baseline nenhuma.

    A guarda olha afirmacao sobre o preco ANTIGO. Barrar a palavra "desconto"
    tiraria da pista a unica coisa que ela poderia legitimamente anunciar.
    """
    from promo.copywriter import _reject_unfounded_claims

    _reject_unfounded_claims(
        "Lattafa por R$ 155,00. Usa o cupom TORCIDA que ainda tem desconto.",
        pontuada_sem_medicao(),
    )


def test_o_template_de_emergencia_nao_repete_o_preco():
    """Sem Gemini o post sai por template, e ele tambem nao pode mentir.

    Com `baseline` igual ao preco, o formato antigo produzia "De R$ 155,00 por
    R$ 155,00".
    """
    from promo.copywriter import fallback_copy

    texto = fallback_copy(pontuada_sem_medicao(), "https://mercadolivre.com/sec/abc")

    assert "De R$" not in texto
    assert texto.count("155,00") == 1
    assert "*R$ 155,00*" in texto


# ---------- a fila ----------


def banco():
    import sqlite3

    from promo.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def enfileirar(conn, produto: str, prioridade: int = 0) -> int:
    from promo.db import create_post, record_offer

    record_offer(
        conn,
        Offer(
            source="mercadolivre",
            external_id=produto,
            title=f"Produto {produto}",
            price=100.0,
            url=f"https://mercadolivre.com.br/{produto}",
        ),
    )
    return create_post(
        conn,
        f"mercadolivre:{produto}",
        100.0,
        150.0,
        33.0,
        "texto",
        prioridade=prioridade,
    )


def test_a_pista_passa_na_frente_do_que_ja_esperava():
    """O ponto do pedido: 100% de prioridade.

    A fila drena a 120 s por post. Com trinta esperando, herdar a posicao pelo
    `created_at` colocaria a pista uma hora depois -- e ela vale justamente
    porque acabou de ser postada.
    """
    from promo.db import pending_posts

    conn = banco()
    enfileirar(conn, "ANTIGO_1")
    enfileirar(conn, "ANTIGO_2")
    enfileirar(conn, "PISTA", prioridade=1)

    ordem = [linha["id"] for linha in pending_posts(conn)]

    assert ordem[0] == 3


def test_entre_posts_normais_a_ordem_continua_a_de_chegada():
    """A coluna nova nao pode reordenar o que ja funcionava."""
    from promo.db import pending_posts

    conn = banco()
    primeiro = enfileirar(conn, "A")
    segundo = enfileirar(conn, "B")
    terceiro = enfileirar(conn, "C")

    ordem = [linha["id"] for linha in pending_posts(conn)]

    assert ordem == [primeiro, segundo, terceiro]


def test_o_post_da_pista_nasce_com_prioridade():
    """`_gravar_post` le a marca da descoberta, e nao um campo do produto."""
    from promo.pipeline import _VISTOS_EM_OUTRO_GRUPO, _gravar_post

    conn = banco()
    from promo.db import record_offer

    scored = score_pista(oferta())
    record_offer(conn, scored.offer)

    _VISTOS_EM_OUTRO_GRUPO.clear()
    _VISTOS_EM_OUTRO_GRUPO.add(scored.offer.external_id)
    try:
        _gravar_post(conn, scored, "texto do post", "")
    finally:
        _VISTOS_EM_OUTRO_GRUPO.clear()

    assert conn.execute("SELECT prioridade FROM posts").fetchone()[0] == 1


def test_post_normal_nasce_sem_prioridade():
    from promo.db import record_offer
    from promo.pipeline import _VISTOS_EM_OUTRO_GRUPO, _gravar_post

    conn = banco()
    scored = ScoredOffer(
        offer=oferta(), baseline=200.0, discount_pct=22.5, observations=9,
        lowest_ever=False,
    )
    record_offer(conn, scored.offer)

    _VISTOS_EM_OUTRO_GRUPO.clear()
    _gravar_post(conn, scored, "texto do post", "")

    assert conn.execute("SELECT prioridade FROM posts").fetchone()[0] == 0

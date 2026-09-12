"""Dentro do cooldown, quem libera a repeticao e o PRECO -- nao o desconto.

A queixa do dono em 12/09/2026 era "mandamos A MESMA coisa em pouco tempo",
com dois exemplos: a camisa da Reserva e a bicicleta de malhar. A medicao em
producao, sobre 72 horas e 1.296 posts enviados, achou 568 repeticoes do mesmo
produto -- e 487 delas (86%) com o preco IDENTICO ao do post anterior.

A causa nao era o cooldown estar desligado. Era a excecao dele.

O mesmo anuncio e pontuado por duas portas com reguas diferentes: `score`
mede contra a NOSSA mediana e `score_campaign` contra o riscado da LOJA. O kit
de meias a R$ 49,99 dava 28,6% por uma (baseline R$ 69,99) e 54,6% pela outra
(riscado R$ 109,99). A excecao liberava quem melhorasse 10 pontos percentuais,
e a distancia entre as duas reguas era 26 -- entao o mesmo produto, pelo mesmo
preco, saia de novo:

    09-12 12:20  desc=28,6  preco=49,99  baseline=69,99
    09-12 13:41  desc=54,6  preco=49,99  baseline=109,99

Oitenta e um minutos, o mesmo preco, nenhuma mudanca no anuncio.

Com o preco como criterio a pergunta volta a ser a certa: ficou mais barato do
que estava quando eu postei?
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import Rules  # noqa: E402
from promo.db import SCHEMA, create_post, now, record_offer  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.scoring import _in_cooldown  # noqa: E402

REGRAS = Rules(
    min_discount_pct=10.0,
    baseline_window_days=14,
    min_observations=3,
    repost_cooldown_days=1,
    max_offers_per_run=6,
)


def oferta(preco: float) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB1",
        title="Kit 15 Pares Meias Sport Algodao",
        price=preco,
        url="https://mercadolivre.com.br/MLB1",
    )


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    record_offer(conn, oferta(49.99))
    return conn


def postar(conn, preco, baseline, desconto, dias_atras=0.0):
    post_id = create_post(
        conn, "mercadolivre:MLB1", preco, baseline, desconto, "texto"
    )
    if dias_atras:
        quando = (now() - timedelta(days=dias_atras)).isoformat()
        conn.execute(
            "UPDATE posts SET created_at = ? WHERE id = ?", (quando, post_id)
        )
    return post_id


# ---------- o caso que aconteceu de verdade ----------


def test_o_mesmo_preco_por_outra_regua_nao_repete():
    """Post anterior: 28,6% contra a nossa mediana. Agora: 54,6% contra o
    riscado. Vinte e seis pontos de diferenca, zero centavo de diferenca."""
    conn = make_conn()
    postar(conn, preco=49.99, baseline=69.99, desconto=28.6)

    assert _in_cooldown(conn, oferta(49.99), REGRAS) is True


def test_preco_um_pouco_menor_ainda_nao_repete():
    """R$ 1 num produto de R$ 50 nao e oferta nova, e o mesmo post."""
    conn = make_conn()
    postar(conn, preco=49.99, baseline=69.99, desconto=28.6)

    assert _in_cooldown(conn, oferta(48.99), REGRAS) is True


def test_queda_de_dez_por_cento_libera():
    conn = make_conn()
    postar(conn, preco=49.99, baseline=69.99, desconto=28.6)

    assert _in_cooldown(conn, oferta(44.00), REGRAS) is False


def test_preco_maior_nunca_libera():
    conn = make_conn()
    postar(conn, preco=49.99, baseline=69.99, desconto=28.6)

    assert _in_cooldown(conn, oferta(59.99), REGRAS) is True


# ---------- o que continua valendo ----------


def test_fora_da_janela_libera_mesmo_com_o_preco_igual():
    conn = make_conn()
    postar(conn, preco=49.99, baseline=69.99, desconto=28.6, dias_atras=2)

    assert _in_cooldown(conn, oferta(49.99), REGRAS) is False


def test_produto_que_nunca_saiu_nao_esta_em_cooldown():
    assert _in_cooldown(make_conn(), oferta(49.99), REGRAS) is False


def test_post_antigo_sem_preco_segura_a_repeticao():
    """Na duvida nao repete: sem preco gravado nao da para comparar nada."""
    conn = make_conn()
    post_id = postar(conn, preco=49.99, baseline=69.99, desconto=28.6)
    conn.execute("UPDATE posts SET price = 0 WHERE id = ?", (post_id,))

    assert _in_cooldown(conn, oferta(30.00), REGRAS) is True


def test_a_bicicleta_ergometrica_nao_sai_quatro_dias_seguidos():
    """O caso que o dono citou: R$ 420,80 em 09, 10, 11 e 12 de setembro.

    Com o cooldown de um dia cada post individual era "legal"; o que o leitor
    via era a mesma bicicleta pelo mesmo preco a semana inteira.
    """
    conn = make_conn()
    # ontem, pela nossa mediana
    postar(conn, preco=420.80, baseline=483.92, desconto=13.0, dias_atras=0.5)

    # hoje, pelo riscado da loja: 62,6% contra 13% -- quase 50 pontos a mais
    assert _in_cooldown(conn, oferta(420.80), REGRAS) is True

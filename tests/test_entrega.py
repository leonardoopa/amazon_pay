"""Link de afiliado, envio com imagem e a fila de posts.

Os tres pontos onde o projeto perde dinheiro em silencio: post sem link de
afiliado (comissao zero), oferta duplicada quando a janela de 24h fecha, e
post descartado por um erro de rede passageiro.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import post_max_age_minutes  # noqa: E402
from promo.db import (  # noqa: E402
    MAX_SEND_ATTEMPTS,
    SCHEMA,
    affiliate_link,
    create_post,
    expire_stale_posts,
    last_post,
    mark_post_failed,
    mark_post_sent,
    pending_posts,
    products_missing_link,
    now,
    record_offer,
    save_affiliate_link,
)
from promo.delivery.whatsapp import CAPTION_LIMIT, WhatsApp, WindowClosed  # noqa: E402
from promo.models import Offer  # noqa: E402

PRODUCT = "mercadolivre:MLB1"
LINK = "https://meli.la/18kqB7B"


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def make_offer(external_id: str = "MLB1", price: float = 100.0) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id=external_id,
        title="Produto",
        price=price,
        url="https://produto.mercadolivre.com.br/x",
    )


def add_post(conn: sqlite3.Connection, product: str = PRODUCT) -> int:
    return create_post(conn, product, 100.0, 150.0, 33.0, "texto", None)


# ---------- link de afiliado ----------


def test_sem_link_cadastrado_devolve_none():
    """Nao inventar link e o ponto: o ML so emite pelo Link Builder."""
    conn = make_conn()
    record_offer(conn, make_offer())
    assert affiliate_link(conn, PRODUCT) is None


def test_salva_e_le_o_link():
    conn = make_conn()
    record_offer(conn, make_offer())
    save_affiliate_link(conn, PRODUCT, LINK)
    assert affiliate_link(conn, PRODUCT) == LINK


def test_regravar_substitui_o_link():
    conn = make_conn()
    record_offer(conn, make_offer())
    save_affiliate_link(conn, PRODUCT, LINK)
    save_affiliate_link(conn, PRODUCT, "https://meli.la/OUTRO")
    assert affiliate_link(conn, PRODUCT) == "https://meli.la/OUTRO"


def test_pendentes_listam_so_quem_nao_tem_link():
    conn = make_conn()
    record_offer(conn, make_offer("MLB1"))
    record_offer(conn, make_offer("MLB2"))
    save_affiliate_link(conn, PRODUCT, LINK)

    faltando = [row["external_id"] for row in products_missing_link(conn)]
    assert faltando == ["MLB2"]


# ---------- cooldown x fila ----------


def test_post_na_fila_segura_o_cooldown():
    """O bug: last_post so olhava 'sent', entao a rodada seguinte duplicava
    a oferta enquanto o primeiro post esperava a janela de 24h reabrir."""
    conn = make_conn()
    record_offer(conn, make_offer())
    add_post(conn)  # fica 'pending'

    assert last_post(conn, PRODUCT) is not None


def test_post_enviado_tambem_segura():
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = add_post(conn)
    mark_post_sent(conn, post_id)

    assert last_post(conn, PRODUCT) is not None


def test_post_desistido_nao_segura_o_cooldown():
    """Depois de esgotar as tentativas o produto pode voltar a concorrer."""
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = add_post(conn)
    for _ in range(MAX_SEND_ATTEMPTS):
        mark_post_failed(conn, post_id, "boom")

    assert last_post(conn, PRODUCT) is None


# ---------- retentativa ----------


def test_falha_transitoria_continua_na_fila():
    """Antes, um timeout marcava 'failed' na hora e a oferta sumia pra sempre."""
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = add_post(conn)

    mark_post_failed(conn, post_id, "timeout")

    assert [row["id"] for row in pending_posts(conn)] == [post_id]


def test_desiste_depois_do_teto_de_tentativas():
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = add_post(conn)

    for _ in range(MAX_SEND_ATTEMPTS):
        mark_post_failed(conn, post_id, "boom")

    assert pending_posts(conn) == []
    row = conn.execute(
        "SELECT status, attempts FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    assert row["status"] == "failed"
    assert row["attempts"] == MAX_SEND_ATTEMPTS


def test_envio_bem_sucedido_sai_da_fila():
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = add_post(conn)
    mark_post_sent(conn, post_id)

    assert pending_posts(conn) == []


# ---------- WhatsApp: imagem ----------


class FakeWhatsApp(WhatsApp):
    """Substitui so o _post, pra inspecionar o payload que iria pra Meta."""

    def __init__(self, falha_imagem: Exception | None = None) -> None:
        class Cfg:
            phone_number_id = "1"
            token = "t"
            to = "5511999999999"
            ping_template = "ofertas_ping"
            template_lang = "pt_BR"

        super().__init__(Cfg())
        self.enviados: list[dict] = []
        self.falha_imagem = falha_imagem

    def _post(self, payload: dict) -> dict:
        if payload["type"] == "image" and self.falha_imagem:
            raise self.falha_imagem
        self.enviados.append(payload)
        return {"ok": True}


def test_manda_imagem_com_legenda_numa_mensagem_so():
    """Duas mensagens separadas quebrariam a associacao foto/preco ao encaminhar."""
    wa = FakeWhatsApp()
    wa.send_post("Oferta boa", "https://http2.mlstatic.com/foto-O.jpg")

    assert len(wa.enviados) == 1
    payload = wa.enviados[0]
    assert payload["type"] == "image"
    assert payload["image"]["link"] == "https://http2.mlstatic.com/foto-O.jpg"
    assert payload["image"]["caption"] == "Oferta boa"


def test_sem_imagem_manda_texto():
    wa = FakeWhatsApp()
    wa.send_post("Oferta boa", None)

    assert wa.enviados[0]["type"] == "text"


def test_texto_longo_demais_para_legenda_vira_texto():
    wa = FakeWhatsApp()
    wa.send_post("x" * (CAPTION_LIMIT + 1), "https://http2.mlstatic.com/foto-O.jpg")

    assert wa.enviados[0]["type"] == "text"


def test_imagem_recusada_cai_para_texto():
    """A Meta recusar a URL nao pode custar a oferta."""
    wa = FakeWhatsApp(falha_imagem=RuntimeError("media download failed"))
    wa.send_post("Oferta boa", "https://exemplo.com/quebrada.jpg")

    assert len(wa.enviados) == 1
    assert wa.enviados[0]["type"] == "text"


def test_janela_fechada_nao_e_mascarada_pelo_fallback():
    """WindowClosed precisa subir: quem trata dela e a fila, nao o fallback."""
    wa = FakeWhatsApp(falha_imagem=WindowClosed("131047"))
    with pytest.raises(WindowClosed):
        wa.send_post("Oferta boa", "https://http2.mlstatic.com/foto-O.jpg")

    assert wa.enviados == []


# ---------- prova medida x repasse da loja ----------


def test_post_nasce_marcado_como_verificado():
    """O caminho normal e a nossa medicao contra a mediana."""
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = create_post(conn, PRODUCT, 100.0, 150.0, 33.0, "texto", None)

    linha = conn.execute(
        "SELECT verified FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    assert linha["verified"] == 1


def test_repasse_da_vitrine_fica_gravado_como_nao_verificado():
    """`ScoredOffer.verified` existia so em memoria e morria no INSERT. O site
    lia repasse do "de/por" da loja como desconto medido por nos -- e chamava
    o preco riscado do anuncio de "media"."""
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = create_post(
        conn, PRODUCT, 100.0, 150.0, 33.0, "texto", None, verified=False
    )

    linha = conn.execute(
        "SELECT verified FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    assert linha["verified"] == 0


# ---------- preco velho sai da fila ----------


def test_post_recente_continua_na_fila():
    conn = make_conn()
    record_offer(conn, make_offer())
    add_post(conn)

    assert expire_stale_posts(conn) == []
    assert len(pending_posts(conn)) == 1


def test_post_velho_sai_da_fila_como_expired():
    """Preco no texto e medicao com hora. Uma hora depois pode nao valer mais,
    e mandar valor que o link nao confirma queima a confianca que a medicao
    inteira existe para construir."""
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = add_post(conn)
    antigo = (now() - timedelta(minutes=post_max_age_minutes() + 1)).isoformat()
    conn.execute("UPDATE posts SET created_at = ? WHERE id = ?", (antigo, post_id))

    expirados = expire_stale_posts(conn)

    assert [linha["id"] for linha in expirados] == [post_id]
    assert pending_posts(conn) == []
    linha = conn.execute(
        "SELECT status, error FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    assert linha["status"] == "expired"
    assert "velho" in linha["error"] or "min" in linha["error"]


def test_expiracao_nao_toca_no_que_ja_foi_enviado():
    """Post enviado e historico: a vitrine do site le dele."""
    conn = make_conn()
    record_offer(conn, make_offer())
    post_id = add_post(conn)
    mark_post_sent(conn, post_id)
    antigo = (now() - timedelta(days=30)).isoformat()
    conn.execute("UPDATE posts SET created_at = ? WHERE id = ?", (antigo, post_id))

    assert expire_stale_posts(conn) == []
    assert (
        conn.execute("SELECT status FROM posts WHERE id = ?", (post_id,)).fetchone()[
            "status"
        ]
        == "sent"
    )

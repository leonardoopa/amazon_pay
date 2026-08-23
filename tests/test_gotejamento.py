"""Entrega gotejada: um post de cada vez, com intervalo irregular.

Rajada de 10 mensagens tem dois problemas distintos. O grupo le como flood e
silencia a conversa. E o padrao -- intervalo zero, rajada perfeita -- e o que o
antifraude da Meta procura num numero pareado por cliente nao oficial.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.config import post_max_age_minutes  # noqa: E402
from promo.db import (  # noqa: E402
    SCHEMA,
    create_post,
    now,
    pending_posts,
)


class FakeDelivery:
    """Registra o que foi enviado, sem falar com ninguem."""

    def __init__(
        self, falha_em: int | None = None, erro: Exception | None = None
    ) -> None:
        self.enviados: list[str] = []
        self.tentativas = 0
        self.falha_em = falha_em
        self.erro = erro
        self.config = type("C", (), {"group_jid": "x@g.us"})()

    def send_post(self, text, image_url=None):
        # Conta TENTATIVAS, nao sucessos: contar sucessos faria a falha se
        # repetir pra sempre, e o teste mediria o fake em vez do codigo.
        indice, self.tentativas = self.tentativas, self.tentativas + 1
        if self.falha_em is not None and indice == self.falha_em:
            raise self.erro
        self.enviados.append(text)
        return {"ok": True}

    def send_ping_template(self, pending):
        return {"ok": True}


class Relogio:
    """Tempo controlado: o teste nao pode dormir de verdade."""

    def __init__(self) -> None:
        self.agora = 0.0
        self.dormidas: list[float] = []

    def sleep(self, segundos: float) -> None:
        self.dormidas.append(segundos)
        self.agora += segundos

    def monotonic(self) -> float:
        return self.agora


@pytest.fixture
def banco(tmp_path, monkeypatch):
    caminho = tmp_path / "t.db"
    monkeypatch.setenv("DB_PATH", str(caminho))
    conn = sqlite3.connect(caminho)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO products (id, source, external_id, title, url, first_seen_at,"
        " last_seen_at) VALUES ('mercadolivre:MLB1','mercadolivre','MLB1','P','u','x','x')"
    )
    conn.commit()
    conn.close()
    return caminho


def enfileira(banco, quantos: int) -> None:
    conn = sqlite3.connect(banco)
    conn.row_factory = sqlite3.Row
    for i in range(quantos):
        create_post(conn, "mercadolivre:MLB1", 10.0, 20.0, 50.0, f"post {i}", None)
    conn.commit()
    conn.close()


def flush(monkeypatch, entrega, relogio, budget=None):
    monkeypatch.setattr(pipeline, "build_delivery", lambda: entrega)
    pipeline.flush_pending(
        budget_seconds=budget, sleep=relogio.sleep, monotonic=relogio.monotonic
    )


# ---------- gotejamento ----------


def test_primeiro_post_sai_na_hora(banco, monkeypatch):
    """Esperar antes do primeiro seria atrasar a oferta a toa."""
    enfileira(banco, 1)
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio)

    assert len(entrega.enviados) == 1
    assert relogio.dormidas == []


def test_espera_entre_um_post_e_o_proximo(banco, monkeypatch):
    enfileira(banco, 3)
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio, budget=10_000)

    assert len(entrega.enviados) == 3
    assert len(relogio.dormidas) == 2  # entre 1-2 e entre 2-3


def test_intervalo_e_irregular(banco, monkeypatch):
    """Intervalo exato e assinatura de robo."""
    enfileira(banco, 6)
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio, budget=10_000)

    assert len(set(relogio.dormidas)) > 1


def test_intervalo_fica_perto_do_configurado(banco, monkeypatch):
    monkeypatch.setenv("DRIP_INTERVAL_SECONDS", "100")
    enfileira(banco, 8)
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio, budget=10_000)

    assert all(60 <= d <= 140 for d in relogio.dormidas), relogio.dormidas


# ---------- orcamento de tempo ----------


def test_orcamento_corta_a_drenagem(banco, monkeypatch):
    """O que nao couber sai na proxima rodada, nao se perde.

    Jitter zerado aqui de proposito: com variacao o total oscila e o teste
    vira moeda. A irregularidade tem teste proprio.
    """
    monkeypatch.setenv("DRIP_INTERVAL_SECONDS", "100")
    monkeypatch.setattr(pipeline, "DRIP_JITTER", 0.0)
    enfileira(banco, 10)
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio, budget=250)

    # 1o na hora, depois 100s por post: em 250s cabem 3.
    assert len(entrega.enviados) == 3

    conn = sqlite3.connect(banco)
    conn.row_factory = sqlite3.Row
    assert len(pending_posts(conn)) == 10 - len(entrega.enviados)


def test_o_que_sobra_continua_pendente(banco, monkeypatch):
    monkeypatch.setenv("DRIP_INTERVAL_SECONDS", "100")
    monkeypatch.setattr(pipeline, "DRIP_JITTER", 0.0)
    enfileira(banco, 5)
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio, budget=150)

    conn = sqlite3.connect(banco)
    conn.row_factory = sqlite3.Row
    restantes = pending_posts(conn)
    assert restantes
    assert restantes[0]["copy"] == f"post {len(entrega.enviados)}"


def test_orcamento_nao_impede_o_primeiro(banco, monkeypatch):
    """Orcamento zero ainda entrega um post -- senao a fila nunca anda."""
    enfileira(banco, 3)
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio, budget=0)

    assert len(entrega.enviados) == 1


# ---------- falhas no meio da drenagem ----------


def test_instancia_caida_para_a_drenagem(banco, monkeypatch):
    """Reparear exige QR na mao; insistir so gastaria tentativa."""
    from promo.delivery import NotConnected

    enfileira(banco, 5)
    entrega = FakeDelivery(falha_em=2, erro=NotConnected("caiu"))
    relogio = Relogio()
    flush(monkeypatch, entrega, relogio, budget=10_000)

    assert len(entrega.enviados) == 2
    conn = sqlite3.connect(banco)
    conn.row_factory = sqlite3.Row
    assert len(pending_posts(conn)) == 3


def test_erro_pontual_nao_para_os_outros(banco, monkeypatch):
    """Um post que falha nao pode segurar a fila inteira."""
    enfileira(banco, 4)
    entrega = FakeDelivery(falha_em=1, erro=RuntimeError("timeout"))
    relogio = Relogio()
    flush(monkeypatch, entrega, relogio, budget=10_000)

    assert len(entrega.enviados) == 3  # o que falhou nao entrou


def test_fila_vazia_nao_envia_nada(banco, monkeypatch):
    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio)

    assert entrega.enviados == []


# ---------- preco velho nao vai para o grupo ----------


def test_flush_descarta_post_velho_antes_de_drenar(banco, monkeypatch):
    """A fila pode segurar um post por mais de uma hora (orcamento de
    drenagem + gotejamento). Preco de anuncio nao espera: o texto diz um valor
    que o link talvez nao confirme mais."""
    enfileira(banco, 2)
    conn = sqlite3.connect(banco)
    antigo = (now() - timedelta(minutes=post_max_age_minutes() + 5)).isoformat()
    conn.execute("UPDATE posts SET created_at = ? WHERE id = 1", (antigo,))
    conn.commit()
    conn.close()

    entrega, relogio = FakeDelivery(), Relogio()
    flush(monkeypatch, entrega, relogio, budget=10_000)

    assert entrega.enviados == ["post 1"]

    conn = sqlite3.connect(banco)
    conn.row_factory = sqlite3.Row
    status = {
        linha["id"]: linha["status"]
        for linha in conn.execute("SELECT id, status FROM posts")
    }
    conn.close()
    assert status == {1: "expired", 2: "sent"}

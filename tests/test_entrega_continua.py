"""A entrega continua: um post por vez, sem esperar a rodada.

O que o dono viu em 02/10/2026 foi o grupo em silencio. O que os numeros
mostraram: o Geral mandou 12 posts em 17 minutos (14h47 a 15h04), bateu o teto
de 12 por hora e nao mandou mais nada, enquanto 36 posts esperavam e expiravam.
A drenagem so rodava no fim de cada rodada, e a rodada leva de 35 a 45 minutos.

Estes testes guardam tres coisas: o ritmo (um post de cada vez, espaçado), os
tetos (Geral por hora e piso dos tematicos) e a exclusao mutua com a drenagem
antiga, para o mesmo post nunca sair duas vezes.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.db import SCHEMA, create_post, record_offer  # noqa: E402
from promo.delivery import NotConnected  # noqa: E402
from promo.models import Offer  # noqa: E402

GERAL = ""
MULHERES = "120363428560546806@g.us"
ESPORTES = "120363411852739657@g.us"


class EntregaFalsa:
    """Guarda o que foi enviado; pode falhar de um jeito escolhido."""

    def __init__(self, falha: Exception | None = None) -> None:
        self.enviados: list[tuple[str, str | None]] = []
        self.falha = falha

    def send_post(self, text, image_url=None, to=None):
        if self.falha:
            raise self.falha
        self.enviados.append((text, to))
        return {"ok": True}


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setenv("DRIP_INTERVAL_SECONDS", "100")
    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "12")
    monkeypatch.setenv("TEMATICO_GAP_SEGUNDOS", "900")
    monkeypatch.setenv("POST_MAX_AGE_MINUTES", "60")
    pipeline._DRENAGEM_CONTINUA.clear()
    yield
    pipeline._DRENAGEM_CONTINUA.clear()


@pytest.fixture
def banco(banco_em_memoria) -> sqlite3.Connection:
    return banco_em_memoria


def agora() -> datetime:
    return datetime.now(UTC)


def enfileirar(conn, nome: str, grupo: str = GERAL, prioridade: int = 0,
               idade_min: float = 1.0) -> int:
    """Um post pendente, criado `idade_min` minutos atras."""
    oferta = Offer(
        source="mercadolivre", external_id=nome, title=f"Produto {nome}",
        price=100.0, url=f"https://mercadolivre.com.br/{nome}",
    )
    record_offer(conn, oferta)
    post_id = create_post(
        conn, oferta.product_id, 100.0, 150.0, 33.0, f"texto {nome}",
        grupo_jid=grupo, prioridade=prioridade,
    )
    criado = (agora() - timedelta(minutes=idade_min)).isoformat()
    conn.execute("UPDATE posts SET created_at = ? WHERE id = ?", (criado, post_id))
    conn.commit()
    return post_id


def ja_enviado(conn, nome: str, grupo: str, ha_segundos: float) -> None:
    """Um post enviado `ha_segundos` atras, para o ritmo ter de onde contar."""
    oferta = Offer(
        source="mercadolivre", external_id=nome, title=f"Produto {nome}",
        price=100.0, url=f"https://mercadolivre.com.br/{nome}",
    )
    record_offer(conn, oferta)
    post_id = create_post(
        conn, oferta.product_id, 100.0, 150.0, 33.0, f"texto {nome}", grupo_jid=grupo
    )
    quando = (agora() - timedelta(seconds=ha_segundos)).isoformat()
    conn.execute(
        "UPDATE posts SET status = 'sent', sent_at = ?, created_at = ? WHERE id = ?",
        (quando, quando, post_id),
    )
    conn.commit()


def status(conn, post_id: int) -> str:
    return conn.execute("SELECT status FROM posts WHERE id = ?", (post_id,)).fetchone()[0]


# ---------- um post por vez ----------


def test_envia_um_post_e_marca_como_enviado(banco):
    primeiro = enfileirar(banco, "A")
    enfileirar(banco, "B")
    entrega = EntregaFalsa()

    resultado = pipeline.drip_once(entrega)

    assert resultado == pipeline.ENVIOU
    assert len(entrega.enviados) == 1
    assert status(banco, primeiro) == "sent"


def test_dentro_do_grupo_vale_a_prioridade_da_fila(banco):
    """Comida e bebida de outro grupo (faixa 2) sai antes do resto do Geral."""
    enfileirar(banco, "COMUM", prioridade=1)
    vodka = enfileirar(banco, "VODKA", prioridade=2)
    entrega = EntregaFalsa()

    pipeline.drip_once(entrega)

    assert status(banco, vodka) == "sent"
    assert entrega.enviados[0][0] == "texto VODKA"


def test_fila_vazia_nao_envia_nada(banco):
    assert pipeline.drip_once(EntregaFalsa()) == pipeline.NADA


def test_o_geral_vai_com_destino_vazio_para_usar_o_do_env(banco):
    enfileirar(banco, "A", grupo=GERAL)
    entrega = EntregaFalsa()

    pipeline.drip_once(entrega)

    assert entrega.enviados[0][1] is None


def test_o_tematico_vai_para_o_proprio_grupo(banco):
    enfileirar(banco, "A", grupo=MULHERES)
    entrega = EntregaFalsa()

    pipeline.drip_once(entrega)

    assert entrega.enviados[0][1] == MULHERES


# ---------- o ritmo ----------


def test_nao_manda_outro_logo_atras_do_ultimo(banco):
    """Dez segundos depois do ultimo envio, de qualquer grupo: espera."""
    ja_enviado(banco, "ANTES", MULHERES, ha_segundos=10)
    enfileirar(banco, "A", grupo=GERAL)
    entrega = EntregaFalsa()

    assert pipeline.drip_once(entrega) == pipeline.NADA
    assert entrega.enviados == []


def test_passado_o_intervalo_volta_a_enviar(banco):
    ja_enviado(banco, "ANTES", MULHERES, ha_segundos=200)
    enfileirar(banco, "A", grupo=GERAL)

    assert pipeline.drip_once(EntregaFalsa()) == pipeline.ENVIOU


def test_o_geral_reparte_o_teto_em_intervalos_iguais(banco):
    """12 por hora sao 5 minutos entre posts: 100 s depois do ultimo Geral, nao."""
    ja_enviado(banco, "ANTES", GERAL, ha_segundos=100)
    enfileirar(banco, "A", grupo=GERAL)
    entrega = EntregaFalsa()

    assert pipeline.drip_once(entrega) == pipeline.NADA


def test_o_geral_sai_quando_o_intervalo_dele_passa(banco):
    ja_enviado(banco, "ANTES", GERAL, ha_segundos=260)  # 0,8 x 300 s = 240 s
    enfileirar(banco, "A", grupo=GERAL)

    assert pipeline.drip_once(EntregaFalsa()) == pipeline.ENVIOU


def test_geral_que_ainda_espera_cede_a_vez_a_um_tematico(banco):
    ja_enviado(banco, "ANTES", GERAL, ha_segundos=150)
    enfileirar(banco, "G", grupo=GERAL)
    mulheres = enfileirar(banco, "M", grupo=MULHERES)
    entrega = EntregaFalsa()

    pipeline.drip_once(entrega)

    assert status(banco, mulheres) == "sent"


def test_o_tematico_respeita_o_piso_de_900_s(banco):
    ja_enviado(banco, "ANTES", MULHERES, ha_segundos=400)
    enfileirar(banco, "M", grupo=MULHERES)

    assert pipeline.drip_once(EntregaFalsa()) == pipeline.NADA


def test_o_teto_do_geral_por_hora_continua_valendo(banco):
    """12 enviados na ultima hora: o Geral nao manda mais, mesmo com o
    intervalo em dia."""
    for i in range(12):
        ja_enviado(banco, f"H{i}", GERAL, ha_segundos=1500 + i * 100)
    enfileirar(banco, "G", grupo=GERAL)

    assert pipeline.drip_once(EntregaFalsa()) == pipeline.NADA


def test_com_o_teto_desligado_o_geral_so_obedece_o_gotejamento(banco, monkeypatch):
    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "0")
    ja_enviado(banco, "ANTES", GERAL, ha_segundos=150)
    enfileirar(banco, "G", grupo=GERAL)

    assert pipeline.drip_once(EntregaFalsa()) == pipeline.ENVIOU


def test_a_vez_e_de_quem_esta_ha_mais_tempo_sem_sair(banco):
    ja_enviado(banco, "G0", GERAL, ha_segundos=2000)
    ja_enviado(banco, "M0", MULHERES, ha_segundos=5000)
    ja_enviado(banco, "E0", ESPORTES, ha_segundos=3000)
    geral = enfileirar(banco, "G", grupo=GERAL)
    mulheres = enfileirar(banco, "M", grupo=MULHERES)
    esportes = enfileirar(banco, "E", grupo=ESPORTES)

    pipeline.drip_once(EntregaFalsa())

    assert status(banco, mulheres) == "sent"
    assert status(banco, geral) == "pending"
    assert status(banco, esportes) == "pending"


def test_grupo_que_nunca_enviou_tem_a_vez(banco):
    ja_enviado(banco, "G0", GERAL, ha_segundos=2000)
    novo = enfileirar(banco, "M", grupo=MULHERES)
    enfileirar(banco, "G", grupo=GERAL)

    pipeline.drip_once(EntregaFalsa())

    assert status(banco, novo) == "sent"


def test_na_janela_de_silencio_o_intervalo_estica(banco, monkeypatch):
    """Madrugada: o piso global e o do gotejamento multiplicado, e domina."""
    monkeypatch.setattr(pipeline, "em_horario_silencioso", lambda *a, **k: True)
    monkeypatch.setenv("QUIET_DRIP_MULTIPLIER", "6")
    ja_enviado(banco, "ANTES", GERAL, ha_segundos=300)  # 100 x 6 x 0,6 = 360 s
    enfileirar(banco, "G", grupo=GERAL)

    assert pipeline.drip_once(EntregaFalsa()) == pipeline.NADA


# ---------- o que envelheceu e o que falha ----------


def test_post_com_mais_de_60_min_expira_em_vez_de_sair(banco):
    velho = enfileirar(banco, "VELHO", idade_min=75)
    entrega = EntregaFalsa()

    assert pipeline.drip_once(entrega) == pipeline.NADA
    assert entrega.enviados == []
    assert status(banco, velho) == "expired"


def test_instancia_caida_deixa_o_post_pendente_e_pede_para_recuar(banco):
    post = enfileirar(banco, "A")
    entrega = EntregaFalsa(falha=NotConnected("fora"))

    assert pipeline.drip_once(entrega) == pipeline.RECUAR
    assert status(banco, post) == "pending"


def test_erro_do_envio_conta_a_tentativa_e_so_desiste_no_limite(banco):
    """Erro transitorio nao pode custar o post: ele segue pendente ate esgotar
    as tentativas, e a thread espera entre elas."""
    from promo.db import MAX_SEND_ATTEMPTS

    post = enfileirar(banco, "A")
    entrega = EntregaFalsa(falha=RuntimeError("recusou"))

    assert pipeline.drip_once(entrega) == pipeline.RECUAR
    assert status(banco, post) == "pending"

    for _ in range(MAX_SEND_ATTEMPTS - 1):
        pipeline.drip_once(entrega)

    assert status(banco, post) == "failed"


# ---------- nunca duas vezes ----------


def test_com_um_envio_em_curso_a_segunda_chamada_nao_faz_nada(banco):
    enfileirar(banco, "A")
    entrega = EntregaFalsa()

    with pipeline._ENVIO:
        assert pipeline.drip_once(entrega) == pipeline.NADA

    assert entrega.enviados == []


def test_o_mesmo_post_nunca_sai_duas_vezes(banco):
    enfileirar(banco, "A", grupo=MULHERES)
    entrega = EntregaFalsa()
    banco.execute("UPDATE posts SET created_at = created_at")

    pipeline.drip_once(entrega)
    pipeline.drip_once(entrega)

    assert len(entrega.enviados) == 1


def test_com_a_thread_ligada_a_drenagem_antiga_nao_envia(banco, monkeypatch):
    """Drenar aqui tambem seria a rajada de volta."""
    enfileirar(banco, "A")
    entrega = EntregaFalsa()
    monkeypatch.setattr(pipeline, "build_delivery", lambda: entrega)
    pipeline._DRENAGEM_CONTINUA.set()

    gasto = pipeline.flush_pending()

    assert gasto == 0.0
    assert entrega.enviados == []


def test_sem_a_thread_a_drenagem_antiga_continua_igual(banco, monkeypatch):
    post = enfileirar(banco, "A")
    entrega = EntregaFalsa()
    monkeypatch.setattr(pipeline, "build_delivery", lambda: entrega)

    pipeline.flush_pending(budget_seconds=60, sleep=lambda _s: None)

    assert status(banco, post) == "sent"


def test_a_rodada_so_enfileira_quando_a_thread_esta_ligada(banco, monkeypatch):
    """`deliver` grava o rascunho e devolve sem drenar."""
    from promo.models import ScoredOffer

    entrega = EntregaFalsa()
    monkeypatch.setattr(pipeline, "build_delivery", lambda: entrega)
    pipeline._DRENAGEM_CONTINUA.set()
    oferta = Offer(
        source="mercadolivre", external_id="NOVO", title="Produto novo",
        price=100.0, url="https://mercadolivre.com.br/NOVO",
    )
    record_offer(banco, oferta)
    scored = ScoredOffer(
        offer=oferta, baseline=150.0, discount_pct=33.0, observations=0,
        lowest_ever=False, verified=False,
    )
    grupos = [pipeline.GrupoDestino(nome="Geral", jid="")]

    gasto = pipeline.deliver([(scored, "texto do post")], 600.0, grupos)

    assert gasto == 0.0
    assert entrega.enviados == []
    assert banco.execute("SELECT COUNT(*) FROM posts WHERE status = 'pending'").fetchone()[0] == 1


# ---------- a thread ----------


def test_o_laco_liga_e_desliga_a_marca_e_envia(monkeypatch):
    chamadas = {"n": 0}
    parar = threading.Event()

    def uma_vez(delivery=None):
        chamadas["n"] += 1
        assert pipeline._DRENAGEM_CONTINUA.is_set()
        parar.set()
        return pipeline.ENVIOU

    monkeypatch.setattr(pipeline, "drip_once", uma_vez)
    monkeypatch.setattr(pipeline, "_drip_gap", lambda: 0.01)

    pipeline.drenar_continuamente(parar)

    assert chamadas["n"] == 1
    assert not pipeline._DRENAGEM_CONTINUA.is_set()


def test_excecao_no_laco_nao_mata_a_thread(monkeypatch):
    parar = threading.Event()
    chamadas = {"n": 0}

    def quebra_e_depois_para(delivery=None):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise RuntimeError("banco indisponivel")
        parar.set()
        return pipeline.NADA

    monkeypatch.setattr(pipeline, "drip_once", quebra_e_depois_para)
    monkeypatch.setattr(pipeline, "ESPERA_APOS_QUEDA", 0.01)
    monkeypatch.setattr(pipeline, "ESPERA_SEM_NADA", 0.01)

    pipeline.drenar_continuamente(parar)

    assert chamadas["n"] == 2


def test_so_o_backend_evolution_sobe_a_thread(monkeypatch):
    from promo.worker import start_drenagem

    monkeypatch.setenv("DELIVERY_BACKEND", "cloud")

    assert start_drenagem(threading.Event()) is None


def test_entrega_continua_false_desliga(monkeypatch):
    from promo.worker import start_drenagem

    monkeypatch.setenv("DELIVERY_BACKEND", "evolution")
    monkeypatch.setenv("ENTREGA_CONTINUA", "false")

    assert start_drenagem(threading.Event()) is None


def test_com_evolution_a_thread_sobe_e_para_com_o_evento(monkeypatch):
    from promo.worker import start_drenagem

    monkeypatch.setenv("DELIVERY_BACKEND", "evolution")
    monkeypatch.delenv("ENTREGA_CONTINUA", raising=False)
    monkeypatch.setattr(pipeline, "drip_once", lambda delivery=None: pipeline.NADA)
    monkeypatch.setattr(pipeline, "ESPERA_SEM_NADA", 0.01)
    parar = threading.Event()

    thread = start_drenagem(parar)
    assert thread is not None and thread.name == "promo-drip"
    parar.set()
    thread.join(timeout=2)

    assert not thread.is_alive()

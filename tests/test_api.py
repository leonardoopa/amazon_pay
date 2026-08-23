"""Testes da API HTTP: health check e a protecao dos endpoints que mudam estado."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import api  # noqa: E402

SEGREDO = "segredo-de-teste"


def _client(tmp_path, monkeypatch, *, segredo: str | None = SEGREDO):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    if segredo is None:
        monkeypatch.delenv("API_SECRET", raising=False)
    else:
        monkeypatch.setenv("API_SECRET", segredo)
    # worker_enabled=False: o teste nao quer um loop de coleta de verdade
    # subindo junto e batendo na API do ML.
    return TestClient(api.create_app(worker_enabled=False))


@pytest.fixture
def client(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as c:
        yield c


def test_health_aberto_e_saudavel(client):
    """O /health nao pede chave -- e o healthcheck do container que o consulta."""
    resposta = client.get("/health")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert corpo["database"] == "ok"
    # Sem worker ligado, /health nao pode reclamar de worker morto.
    assert corpo["worker"] == "off"


def test_health_nunca_rodou_nao_e_degradado(client):
    """Container novo ainda nao carimbou rodada, e isso e normal, nao falha.

    Se o /health respondesse 503 aqui, o restart do compose entraria em loop
    antes da primeira coleta terminar.
    """
    corpo = client.get("/health").json()

    assert corpo["last_run_hours_ago"] is None
    assert corpo["status"] == "ok"


def test_health_reporta_worker_morto(tmp_path, monkeypatch):
    """Worker ligado mas thread caida tem que virar 503, senao ninguem descobre."""
    with _client(tmp_path, monkeypatch) as c:
        c.app.state.worker_enabled = True
        monkeypatch.setattr(api, "worker_running", lambda: False)

        resposta = c.get("/health")

    assert resposta.status_code == 503
    assert resposta.json()["worker"] == "dead"


def test_stats_exige_chave(client):
    assert client.get("/stats").status_code == 401
    assert client.get("/stats", headers={"X-API-Key": "errada"}).status_code == 401


def test_stats_com_chave_devolve_contagens(client):
    resposta = client.get("/stats", headers={"X-API-Key": SEGREDO})

    assert resposta.status_code == 200
    assert resposta.json() == {
        "products": 0,
        "price_points": 0,
        "with_baseline": 0,
        "posts_sent": 0,
        "posts_sent_verified": 0,
        "posts_pending": 0,
        "posts_failed": 0,
        "posts_expired": 0,
        "last_run_error": None,
    }


def test_health_nao_expoe_texto_de_erro(client):
    """O /health e aberto. Mensagem de excecao carrega URL interna, caminho de
    arquivo e as vezes o JID do grupo -- isso sai no /stats, com chave."""
    corpo = client.get("/health").json()

    assert "last_error" not in corpo
    assert corpo["last_run_failed"] is False


def test_erro_da_rodada_sai_no_stats_com_chave(client, tmp_path, monkeypatch):
    from promo.db import connect, set_meta
    from promo.worker import LAST_ERROR_KEY

    with connect() as conn:
        set_meta(conn, LAST_ERROR_KEY, "boom: http://evolution:8080/x")

    aberto = client.get("/health").json()
    assert aberto["last_run_failed"] is True
    assert "evolution" not in str(aberto)

    protegido = client.get("/stats", headers={"X-API-Key": SEGREDO}).json()
    assert protegido["last_run_error"] == "boom: http://evolution:8080/x"


def test_run_exige_chave(client):
    assert client.post("/run").status_code == 401
    assert client.post("/run", headers={"X-API-Key": "errada"}).status_code == 401


def test_sem_segredo_configurado_endpoint_fecha(tmp_path, monkeypatch):
    """API_SECRET vazio nao pode virar endpoint aberto. Falha fechada."""
    with _client(tmp_path, monkeypatch, segredo=None) as c:
        assert c.post("/run").status_code == 503
        assert c.get("/stats").status_code == 503
        # O health continua respondendo -- ele nunca dependeu do segredo.
        assert c.get("/health").status_code == 200


def test_run_dispara_o_pipeline(client, monkeypatch):
    chamou = threading.Event()
    monkeypatch.setattr(api, "run", chamou.set)

    resposta = client.post("/run", headers={"X-API-Key": SEGREDO})

    assert resposta.status_code == 202
    assert chamou.wait(timeout=5), "a rodada nao foi disparada"


def test_run_recusa_rodada_concorrente(client):
    """Duas rodadas no mesmo SQLite dariam post duplicado. Tem que ser 409."""
    assert api._lock.acquire(blocking=False)
    try:
        resposta = client.post("/run", headers={"X-API-Key": SEGREDO})
    finally:
        api._lock.release()

    assert resposta.status_code == 409

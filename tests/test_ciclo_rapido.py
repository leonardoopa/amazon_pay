"""O ciclo rapido dos grupos-fonte prioritarios.

A rodada completa leva de 35 a 56 minutos (medido em 03/10/2026: 3386 s numa
delas), e uma oferta do #BVA que chegava no comeco dela so virava post no fim.
O ciclo rapido le SO os grupos `prioridade: true` a cada 3 minutos e enfileira o
que for novo; quem envia continua sendo a thread de entrega continua.

Duas coisas precisam ficar de pe: o ciclo curto nao pode roubar o que e da
rodada completa (carimbo, intervalo, /health), e as duas nao podem ler a mesma
fonte ao mesmo tempo, senao o mesmo produto nasce duas vezes.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo import pipeline  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.sources.grupo_wa import Pista  # noqa: E402


@pytest.fixture(autouse=True)
def limpa():
    pipeline._DA_FONTE_PRIORITARIA.clear()
    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()
    pipeline._FAST_LANE_ATIVA.clear()
    pipeline._DRENAGEM_CONTINUA.clear()
    yield
    pipeline._DA_FONTE_PRIORITARIA.clear()
    pipeline._VISTOS_EM_OUTRO_GRUPO.clear()
    pipeline._FAST_LANE_ATIVA.clear()
    pipeline._DRENAGEM_CONTINUA.clear()


FONTES = [
    {"jid": "xet@g.us", "nome": "xet"},
    {"jid": "bva@g.us", "nome": "BVA", "tipo": "pelando", "prioridade": True},
]


@pytest.fixture
def leitura(monkeypatch, banco_em_memoria):
    """Registra quais fontes foram lidas, sem rede."""
    from promo.config import EvolutionConfig
    from promo.sources import grupo_wa, pelando_grupo

    lidas: list[str] = []
    monkeypatch.setenv("AMAZON_PARTNER_TAG", "comunidaded0a-20")
    monkeypatch.setattr(
        "promo.pipeline.load_grupos_fonte", lambda *a, **k: list(FONTES)
    )
    monkeypatch.setattr(
        EvolutionConfig,
        "load",
        classmethod(
            lambda cls, *a, **k: EvolutionConfig(
                base_url="http://e", api_key="k", instance="i", group_jid="g@g.us"
            )
        ),
    )

    def pistas_do_xet(**k):
        lidas.append("xet")
        return [Pista(external_id="MLB_XET", titulo="Do xet", origem="xet")]

    def pistas_do_bva(*a, **k):
        lidas.append("bva")
        return ([Pista(external_id="MLB_BVA", titulo="Do BVA", origem="bva")], [])

    monkeypatch.setattr(grupo_wa, "pistas", pistas_do_xet)
    monkeypatch.setattr(pelando_grupo, "ofertas_do_grupo", pistas_do_bva)
    monkeypatch.setattr(grupo_wa, "ler_mensagens", lambda *a, **k: [])
    monkeypatch.setattr("promo.pipeline._amazon_do_grupo", lambda c, f: [])
    return lidas


class MLFalso:
    def fetch_by_ids(self, alvos):
        return [
            Offer(source="mercadolivre", external_id=e, title=t, price=10.0, url="u")
            for e, t, _ in alvos
        ]


# ---------- a leitura ----------


def test_o_ciclo_rapido_le_so_as_fontes_prioritarias(leitura):
    pipeline.collect_de_outros_grupos(MLFalso(), so_prioritarias=True)

    assert leitura == ["bva"]


def test_o_ciclo_rapido_nao_gasta_o_intervalo_da_rodada_completa(
    leitura, banco_em_memoria
):
    from promo.db import get_meta

    pipeline.collect_de_outros_grupos(MLFalso(), so_prioritarias=True)

    assert get_meta(banco_em_memoria, "last_grupos_fonte") is None


def test_o_ciclo_rapido_le_mesmo_logo_depois_de_outro(leitura):
    """Ele e pacado pelo proprio laco, e nao pelo intervalo de horas."""
    pipeline.collect_de_outros_grupos(MLFalso(), so_prioritarias=True)
    pipeline.collect_de_outros_grupos(MLFalso(), so_prioritarias=True)

    assert leitura == ["bva", "bva"]


def test_a_rodada_completa_le_todas_as_fontes_sem_o_ciclo_rapido(leitura):
    pipeline.collect_de_outros_grupos(MLFalso())

    assert sorted(leitura) == ["bva", "xet"]


def test_com_o_ciclo_rapido_vivo_a_rodada_completa_pula_as_prioritarias(leitura):
    """Senao o mesmo produto nasceria nas duas threads."""
    pipeline._FAST_LANE_ATIVA.set()

    pipeline.collect_de_outros_grupos(MLFalso())

    assert leitura == ["xet"]


def test_a_rodada_completa_consome_o_intervalo_normalmente(leitura, banco_em_memoria):
    from promo.db import get_meta

    pipeline.collect_de_outros_grupos(MLFalso())

    assert get_meta(banco_em_memoria, "last_grupos_fonte") is not None


# ---------- collect_rapido ----------


def test_collect_rapido_usa_a_fonte_que_mede_preco_de_catalogo(leitura):
    class SemMedicao:
        name = "ml_ofertas"

    achados = pipeline.collect_rapido([SemMedicao(), MLFalso()])

    assert [o.external_id for o in achados] == ["MLB_BVA"]


def test_collect_rapido_sem_fonte_de_catalogo_devolve_vazio(leitura):
    class SemMedicao:
        name = "ml_ofertas"

    assert pipeline.collect_rapido([SemMedicao()]) == []
    assert leitura == []


# ---------- run(rapido=True) ----------


class FonteFalsa:
    def __init__(self, nome: str) -> None:
        self.name = nome

    def affiliate_url(self, offer):
        return f"https://nosso.link/{offer.external_id}"


class RedatorFalso:
    def write(self, scored, link, cupom, recentes, final):
        return f"Texto de {scored.offer.title}\n{link}"


@pytest.fixture
def rodada_rapida(monkeypatch, banco_em_memoria):
    chamadas = {"flush": 0, "completa": 0, "tematicos": 0}
    monkeypatch.setattr(
        pipeline,
        "build_sources",
        lambda: [FonteFalsa("mercadolivre"), FonteFalsa("amazon")],
    )
    monkeypatch.setattr(pipeline, "Copywriter", RedatorFalso)
    monkeypatch.setattr(pipeline, "cupons_vigentes", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "load_exclude", lambda *a, **k: ["smartphone"])

    def flush(*a, **k):
        chamadas["flush"] += 1
        return 0.0

    def completa(*a, **k):
        chamadas["completa"] += 1
        return []

    def tematicos(*a, **k):
        chamadas["tematicos"] += 1
        return []

    monkeypatch.setattr(pipeline, "flush_pending", flush)
    monkeypatch.setattr(pipeline, "collect", completa)
    monkeypatch.setattr(pipeline, "_rodada_dos_grupos", tematicos)
    pipeline._DRENAGEM_CONTINUA.set()
    return chamadas


def oferta_do_bva(external_id: str, titulo: str, source: str = "amazon") -> Offer:
    return Offer(
        source=source,
        external_id=external_id,
        title=titulo,
        price=89.9,
        url=f"https://exemplo.com/{external_id}",
        original_price=150.0,
    )


def test_run_rapido_enfileira_o_que_o_bva_postou_na_faixa_2(
    monkeypatch, rodada_rapida, banco_em_memoria
):
    pipeline._DA_FONTE_PRIORITARIA.update({"B0NOTEBOOK", "B0PHONE0001"})
    pipeline._VISTOS_EM_OUTRO_GRUPO.update({"B0NOTEBOOK", "B0PHONE0001"})
    monkeypatch.setattr(
        pipeline,
        "collect_rapido",
        lambda sources: [
            oferta_do_bva("B0NOTEBOOK", "Notebook Dell i5-1334U Windows 11"),
            # Na lista de exclusao do dono; o #BVA manda enviar por inteiro.
            oferta_do_bva("B0PHONE0001", "Smartphone Realme C73 256gb"),
        ],
    )

    pipeline.run(rapido=True)

    posts = banco_em_memoria.execute(
        "SELECT p.external_id, po.prioridade, po.status, po.grupo_jid FROM posts po "
        "JOIN products p ON p.id = po.product_id"
    ).fetchall()
    geral = {linha["external_id"]: linha for linha in posts if linha["grupo_jid"] == ""}
    assert set(geral) == {"B0NOTEBOOK", "B0PHONE0001"}
    assert all(
        linha["prioridade"] == 2 and linha["status"] == "pending"
        for linha in geral.values()
    )


def test_run_rapido_nao_drena_nem_coleta_nem_faz_a_busca_dos_tematicos(
    monkeypatch, rodada_rapida
):
    pipeline._DA_FONTE_PRIORITARIA.add("B0NOTEBOOK")
    pipeline._VISTOS_EM_OUTRO_GRUPO.add("B0NOTEBOOK")
    monkeypatch.setattr(
        pipeline,
        "collect_rapido",
        lambda sources: [
            oferta_do_bva("B0NOTEBOOK", "Notebook Dell i5-1334U Windows 11")
        ],
    )

    pipeline.run(rapido=True)

    # `flush_pending` so e chamado por `deliver`, que e no-op com a thread ligada;
    # o que importa e que a rodada rapida nao o chama ANTES da coleta.
    assert rodada_rapida["completa"] == 0
    assert rodada_rapida["tematicos"] == 0


def test_run_rapido_sem_oferta_nova_sai_cedo(monkeypatch, rodada_rapida):
    monkeypatch.setattr(pipeline, "collect_rapido", lambda sources: [])

    assert pipeline.run(rapido=True) == []
    assert rodada_rapida["flush"] == 0


# ---------- o laco e a thread ----------


def test_loop_do_ciclo_rapido_nao_carimba_o_fim_da_rodada(monkeypatch):
    """`last_run` e o que o /health le para saber se a rodada COMPLETA vive."""
    from promo import worker

    carimbos: list[str | None] = []
    monkeypatch.setattr(worker, "stamp_run", lambda erro=None: carimbos.append(erro))
    parar = threading.Event()

    def tarefa():
        parar.set()

    worker.loop(parar, 0.01, tarefa, registrar_fim=False)

    assert carimbos == []


def test_loop_normal_continua_carimbando(monkeypatch):
    from promo import worker

    carimbos: list[str | None] = []
    monkeypatch.setattr(worker, "stamp_run", lambda erro=None: carimbos.append(erro))
    parar = threading.Event()

    worker.loop(parar, 0.01, lambda: parar.set())

    assert carimbos == [None]


def test_a_thread_sobe_com_entrega_continua_evolution_e_fonte_prioritaria(monkeypatch):
    from promo import worker

    monkeypatch.setenv("DELIVERY_BACKEND", "evolution")
    monkeypatch.delenv("FONTE_RAPIDA", raising=False)
    monkeypatch.delenv("ENTREGA_CONTINUA", raising=False)
    monkeypatch.setenv("FONTE_RAPIDA_INTERVALO_SEGUNDOS", "0.01")
    monkeypatch.setattr(
        "promo.pipeline.load_grupos_fonte", lambda *a, **k: list(FONTES)
    )
    chamadas = {"n": 0}
    parar = threading.Event()

    def uma_rodada(dry_run=False, rapido=False):
        assert rapido is True
        assert pipeline._FAST_LANE_ATIVA.is_set()
        chamadas["n"] += 1
        parar.set()
        return []

    monkeypatch.setattr(pipeline, "run", uma_rodada)

    thread = worker.start_fonte_rapida(parar)
    assert thread is not None and thread.name == "promo-rapido"
    thread.join(timeout=3)

    assert chamadas["n"] == 1
    assert not pipeline._FAST_LANE_ATIVA.is_set()  # solta ao terminar


@pytest.mark.parametrize(
    "ambiente",
    [
        {"FONTE_RAPIDA": "false"},
        {"ENTREGA_CONTINUA": "false"},
        {"DELIVERY_BACKEND": "cloud"},
    ],
)
def test_a_thread_nao_sobe_sem_as_condicoes(monkeypatch, ambiente):
    from promo import worker

    monkeypatch.setenv("DELIVERY_BACKEND", "evolution")
    monkeypatch.setattr(
        "promo.pipeline.load_grupos_fonte", lambda *a, **k: list(FONTES)
    )
    for chave, valor in ambiente.items():
        monkeypatch.setenv(chave, valor)

    assert worker.start_fonte_rapida(threading.Event()) is None


def test_a_thread_nao_sobe_sem_nenhuma_fonte_prioritaria(monkeypatch):
    from promo import worker

    monkeypatch.setenv("DELIVERY_BACKEND", "evolution")
    monkeypatch.setattr(
        "promo.pipeline.load_grupos_fonte",
        lambda *a, **k: [{"jid": "xet@g.us", "nome": "xet"}],
    )

    assert worker.start_fonte_rapida(threading.Event()) is None


def test_o_padrao_e_de_tres_minutos(monkeypatch):
    from promo.config import fonte_rapida_intervalo_segundos

    monkeypatch.delenv("FONTE_RAPIDA_INTERVALO_SEGUNDOS", raising=False)

    assert fonte_rapida_intervalo_segundos() == 180.0


# ---------- o token do ML ----------


def test_duas_threads_nao_renovam_o_token_do_ml_ao_mesmo_tempo(monkeypatch, tmp_path):
    """O refresh_token e de uso unico: a segunda renovacao levaria invalid_grant.

    Banco em arquivo, e nao em memoria: cada thread abre a propria conexao, como
    em producao, e a conexao em memoria so vive na thread que a criou.
    """
    import sqlite3
    from contextlib import contextmanager
    from datetime import timedelta

    from promo.config import MercadoLivreConfig
    from promo.db import SCHEMA, now, save_token
    from promo.sources import mercadolivre
    from promo.sources.mercadolivre import MercadoLivre

    arquivo = tmp_path / "promos.db"
    inicial = sqlite3.connect(arquivo)
    inicial.executescript(SCHEMA)
    inicial.close()

    @contextmanager
    def conectar():
        conn = sqlite3.connect(arquivo, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(mercadolivre, "connect", conectar)
    with conectar() as conn:
        save_token(
            conn, "mercadolivre", "velho", "refresh1", now() - timedelta(hours=1)
        )

    fonte = MercadoLivre(
        MercadoLivreConfig(
            client_id="1",
            client_secret="2",
            redirect_uri="https://exemplo.com/cb",
            site_id="MLB",
        )
    )
    renovacoes: list[str] = []

    def renova(refresh_token):
        renovacoes.append(refresh_token)
        with conectar() as conn:
            save_token(
                conn, "mercadolivre", "novo", "refresh2", now() + timedelta(hours=6)
            )

    monkeypatch.setattr(fonte, "_refresh", renova)
    resultados: list[str] = []

    def pede():
        resultados.append(fonte.access_token())

    threads = [threading.Thread(target=pede) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert renovacoes == ["refresh1"]
    assert resultados == ["novo"] * 4

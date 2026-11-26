"""Vetor ligado com parte do catalogo, prioridade do grupo e "play5"."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.assistente import banco, indice  # noqa: E402
from promo.assistente.busca import Busca  # noqa: E402
from promo.assistente.conversa import pedido_simples  # noqa: E402
from promo.assistente.indice import sincronizar, vetorizar_pendentes  # noqa: E402
from promo.assistente.servico import indexar  # noqa: E402

from test_assistente import (  # noqa: E402,F401
    CATALOGO,
    EmbeddingsFalsos,
    bancos,
    indexado,
    produto,
    saiu_no_grupo,
)


class EmbeddingsDeParte(EmbeddingsFalsos):
    """Vetoriza so os `quantos` primeiros textos do lote: um 429 no meio."""

    def __init__(self, quantos: int) -> None:
        super().__init__()
        self.quantos = quantos

    def documentos(self, textos):
        return super().documentos(textos)[: self.quantos]


@pytest.mark.parametrize("vetorizados, ligado", [(30, True), (20, False)])
def test_vetor_liga_com_dois_mil_vetores_mesmo_abaixo_de_90_por_cento(
    bancos, monkeypatch, vetorizados, ligado  # noqa: F811
):
    monkeypatch.setattr(indice, "MINIMO_DE_VETORES", 30)
    for n in range(40):
        produto(f"Produto numero {n}", 10.0, f"MLB{n}")
    busca = Busca(embeddings=EmbeddingsDeParte(vetorizados))

    resumo = indexar(busca, busca._embeddings)

    assert resumo["cobertura_dos_vetores"] < 0.9
    assert (busca._vetores is not None) is ligado


def test_produto_do_grupo_vetoriza_primeiro(bancos):  # noqa: F811
    antigo = produto("Console PlayStation 5 Slim", 4259.0, "MLB1", dias_sem_ver=5)
    produto("Fone Bluetooth JBL", 199.0, "MLB2", dias_sem_ver=2)
    produto("Air Fryer Mondial 4L", 299.0, "MLB3")
    saiu_no_grupo(antigo, 4259.0, horas=100)

    with banco.conectar() as conn:
        sincronizar(conn)
        vetorizar_pendentes(conn, EmbeddingsFalsos(), limite=1)
        titulos = [
            r["titulo"]
            for r in conn.execute(
                "SELECT i.titulo FROM vetores v JOIN itens i ON i.id = v.id"
            )
        ]

    assert titulos == ["Console PlayStation 5 Slim"]


def test_prioridade_sai_quando_o_post_fica_velho(bancos):  # noqa: F811
    novo = produto("Console PlayStation 5 Slim", 4259.0, "MLB1")
    velho = produto("Fone Bluetooth JBL", 199.0, "MLB2")
    saiu_no_grupo(novo, 4259.0, horas=2)
    saiu_no_grupo(velho, 199.0, horas=31 * 24)

    with banco.conectar() as conn:
        sincronizar(conn)
        prioridades = {
            r["product_id"]: r["prioridade"]
            for r in conn.execute("SELECT product_id, prioridade FROM itens")
        }

    assert prioridades == {novo.product_id: 1, velho.product_id: 0}


def test_prioridade_volta_a_zero_quando_o_post_sai_da_janela(bancos):  # noqa: F811
    from promo.db import connect

    oferta = produto("Console PlayStation 5 Slim", 4259.0, "MLB1")
    saiu_no_grupo(oferta, 4259.0, horas=2)
    with banco.conectar() as conn:
        sincronizar(conn)
    antigo = (datetime.now(UTC) - timedelta(days=40)).isoformat()
    with connect() as bot:
        bot.execute("UPDATE posts SET sent_at = ?", (antigo,))
    with banco.conectar() as conn:
        sincronizar(conn)
        assert conn.execute("SELECT prioridade FROM itens").fetchone()[0] == 0


def test_banco_antigo_ganha_a_coluna_prioridade(tmp_path):
    import sqlite3

    arquivo = tmp_path / "antigo.db"
    velho = sqlite3.connect(arquivo)
    velho.execute(
        "CREATE TABLE itens (id INTEGER PRIMARY KEY, product_id TEXT NOT NULL UNIQUE, "
        "titulo TEXT NOT NULL, busca TEXT NOT NULL, fonte TEXT NOT NULL, "
        "visto_em TEXT NOT NULL)"
    )
    velho.commit()
    velho.close()

    with banco.conectar(arquivo) as conn:
        colunas = [c["name"] for c in conn.execute("PRAGMA table_info(itens)")]

    assert "prioridade" in colunas


@pytest.mark.parametrize(
    "pergunta",
    [
        "me mande promoções do play5",
        "play 5",
        "playstation5",
        "play station 5",
    ],
)
def test_play5_acha_o_playstation_5(bancos, pergunta):  # noqa: F811
    produto("Console PlayStation 5 Slim Digital", 4259.0, "MLB1")
    produto("Fone Bluetooth JBL", 199.0, "MLB2")

    achados = indexado().buscar(pedido_simples(pergunta).consulta)

    assert [r.titulo for r in achados] == ["Console PlayStation 5 Slim Digital"]


def test_com_vetor_ligado_a_palavra_continua_na_frente(bancos):  # noqa: F811
    for n, titulo in enumerate(CATALOGO):
        produto(titulo, 100.0 + n, f"MLB{n}")
    produto("Fone Bluetooth Philips", 150.0, "MLBFONE")
    produto("Geladeira Frost Free Consul", 3000.0, "MLBGEL")

    achados = indexado(EmbeddingsFalsos()).buscar("fone bluetooth")

    assert achados[0].external_id == "MLBFONE"
    assert "MLBGEL" not in [r.external_id for r in achados]


def test_tipo_pedido_vem_antes_do_acessorio(bancos):  # noqa: F811
    """Ensaio de 26/11: "console do play 5" trouxe portal e conversor (que citam
    "console" e sairam no grupo) e nenhum console."""
    portal = produto(
        "Portal Ps5 Jogue com seu console Ps5 de qualquer lugar", 1893.0, "MLB1"
    )
    saiu_no_grupo(portal, 1893.0)
    conversor = produto(
        "Conversor Pxn N5 Para Console Ps5 Jogos De Corrida", 399.99, "MLB2"
    )
    saiu_no_grupo(conversor, 399.99)
    produto("Console PlayStation 5 Slim Edição Digital 825 GB", 3989.0, "MLB3")

    achados = indexado().buscar(pedido_simples("e o console do play 5").consulta)

    assert achados[0].external_id == "MLB3"

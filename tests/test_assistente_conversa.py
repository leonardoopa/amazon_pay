"""A memoria da ultima resposta e a pergunta de historico de preco."""

# flake8: noqa: F811

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from promo.assistente import banco  # noqa: E402
from promo.assistente.banco import Contexto  # noqa: E402
from promo.assistente.busca import Historico, Resultado, historico  # noqa: E402
from promo.assistente.conversa import (  # noqa: E402
    DIVULGACAO,
    Assistente,
    Pedido,
    pedido_simples,
    preco_citado,
    texto_do_historico,
)
from promo.assistente.servico import Atendente  # noqa: E402
from test_assistente import (  # noqa: E402,F401
    EntregaFalsa,
    GeminiFalso,
    bancos,
    indexado,
    produto,
    saiu_no_grupo,
    tudo,
)

FRASE = "esse foi o melhor preço? vi que teve promoção esses dias por 4k"


def responde(conn, id, telefone, produtos, consulta="ps5"):
    banco.registrar_mensagem(conn, id, f"{telefone}@s.whatsapp.net", telefone, "x")
    banco.finalizar(
        conn, id, "respondida", consulta=consulta, resultados=len(produtos),
        resposta="r", produtos=produtos,
    )  # fmt: skip


# ---------- a memoria ----------


def test_produtos_mostrados_ficam_gravados(bancos):
    with banco.conectar() as conn:
        responde(conn, "M1", "5581988887777", ["p1", "p2"])
        linha = conn.execute("SELECT produtos FROM mensagens").fetchone()
    assert json.loads(linha["produtos"]) == ["p1", "p2"]


def test_contexto_dos_ultimos_30_minutos(bancos):
    with banco.conectar() as conn:
        responde(conn, "M1", "5581988887777", ["p1", "p2"])
        contexto = banco.ultimo_contexto(conn, "5581988887777")
    assert contexto == Contexto("ps5", ("p1", "p2"))


def test_contexto_expira_em_30_minutos(bancos):
    with banco.conectar() as conn:
        responde(conn, "M1", "5581988887777", ["p1"])
        agora = datetime.now(UTC)
        assert banco.ultimo_contexto(
            conn, "5581988887777", agora + timedelta(minutes=29)
        )
        assert (
            banco.ultimo_contexto(conn, "5581988887777", agora + timedelta(minutes=31))
            is None
        )


def test_contexto_e_do_proprio_telefone(bancos):
    with banco.conectar() as conn:
        responde(conn, "M1", "5581988887777", ["p1"])
        assert banco.ultimo_contexto(conn, "5511999990000") is None


def test_resposta_sem_produto_nao_e_contexto(bancos):
    with banco.conectar() as conn:
        responde(conn, "M1", "5581988887777", [])
        assert banco.ultimo_contexto(conn, "5581988887777") is None


def test_banco_antigo_ganha_a_coluna_produtos(tmp_path):
    caminho = tmp_path / "velho.db"
    antigo = sqlite3.connect(caminho)
    antigo.executescript(
        "CREATE TABLE mensagens (id TEXT PRIMARY KEY, jid TEXT NOT NULL, "
        "telefone TEXT NOT NULL, texto TEXT NOT NULL, recebida_em TEXT NOT NULL, "
        "status TEXT NOT NULL, motivo TEXT, consulta TEXT, resultados INTEGER, "
        "resposta TEXT, respondida_em TEXT);"
    )
    antigo.close()
    with banco.conectar(caminho) as conn:
        colunas = {c["name"] for c in conn.execute("PRAGMA table_info(mensagens)")}
    assert "produtos" in colunas


# ---------- ler a pergunta ----------


@pytest.mark.parametrize(
    "texto, valor",
    [
        ("vi por 4k", 4000.0),
        ("estava 4 mil", 4000.0),
        ("vi por R$ 3.799", 3799.0),
        ("custava 3799 reais", 3799.0),
    ],
)
def test_preco_citado(texto, valor):
    assert preco_citado(texto) == valor


def test_preco_citado_sem_valor():
    assert preco_citado("esse foi o melhor preço?") is None


def test_sem_gemini_pergunta_de_preco_com_contexto_vira_historico():
    pedido = pedido_simples(FRASE, tem_contexto=True)
    assert (pedido.tipo, pedido.item, pedido.preco_citado) == ("historico", 1, 4000.0)


def test_o_segundo_escolhe_o_item_2():
    pedido = pedido_simples("o segundo já esteve mais barato?", tem_contexto=True)
    assert (pedido.tipo, pedido.item) == ("historico", 2)


def test_sem_contexto_nao_e_historico():
    assert pedido_simples(FRASE).tipo != "historico"


# ---------- o historico no banco ----------


def test_historico_le_menor_preco_e_data_e_envio_ao_grupo(bancos):
    console = produto("Console PlayStation 5 Slim", 4259.0, "MLB1",
                      historico=[4500.0, 3999.0, 4600.0, 4400.0])  # fmt: skip
    saiu_no_grupo(console, 4100.0, horas=30)

    h = historico(console.product_id)

    assert h is not None
    assert h.menor_preco == 3999.0
    assert h.menor_em == (datetime.now(UTC).date() - timedelta(days=2)).isoformat()
    assert h.grupo_preco == 4100.0
    assert h.grupo_em is not None
    assert h.resultado.preco == 4259.0
    assert h.dias >= 4


def test_historico_de_produto_que_sumiu(bancos):
    assert historico("nao-existe") is None


def _h(**mudancas) -> Historico:
    r = Resultado(
        "p1", "Console PS5", "mercadolivre", "MLB1", "https://x/1", 4259.0,
        datetime.now(UTC).isoformat(), 4400.0, 20, 3999.0,
    )  # fmt: skip
    base = dict(resultado=r, menor_preco=3999.0, menor_em="2026-11-10", dias=21,
                grupo_em=None, grupo_preco=None)  # fmt: skip
    base.update(mudancas)
    return Historico(**base)


def test_texto_do_historico_compara_o_preco_citado():
    sim = texto_do_historico(_h(), "https://l/1", 4500.0, None)
    assert (
        "Sim: medimos R$ 3.999,00 em 10/11, abaixo dos R$ 4.500,00 que você viu." in sim
    )
    assert "*Console PS5*" in sim
    assert "Menor preço que medimos em 60 dias: R$ 3.999,00, em 10/11." in sim
    assert sim.index("https://l/1") < sim.index(DIVULGACAO)
    assert sim.endswith(DIVULGACAO)

    nao = texto_do_historico(_h(), "https://l/1", 3500.0, None)
    assert "Não medimos esse produto a R$ 3.500,00 ou menos nos últimos 60 dias." in nao


def test_texto_do_historico_sem_dias_suficientes_nao_compara():
    r = _h().resultado
    curto = _h(resultado=Resultado(**{**r.__dict__, "dias": 3, "mediana": 4300.0}))
    texto = texto_do_historico(curto, "https://l/1", None, None)
    assert "Ainda sem histórico suficiente para comparar o preço." in texto
    assert "Menor preço que medimos" not in texto


def test_texto_do_historico_conta_o_envio_ao_grupo():
    h = _h(
        grupo_em=datetime(2026, 11, 20, 15, tzinfo=UTC).isoformat(), grupo_preco=4100.0
    )
    assert "Saiu no grupo em 20/11 por R$ 4.100,00." in texto_do_historico(
        h, "l", None, None
    )


# ---------- o assistente ----------


def _contexto(*ofertas):
    return Contexto("ps5", tuple(o.product_id for o in ofertas))


def test_responder_historico_com_gemini_e_sem_gemini(bancos):
    console = produto("Console PlayStation 5 Slim", 4259.0, "MLB1",
                      historico=[4500.0, 3999.0, 4600.0, 4400.0, 4450.0, 4480.0, 4470.0, 4460.0])  # fmt: skip
    contexto = _contexto(console)
    pedido = Pedido(tipo="historico", item=1, preco_citado=4000.0)

    com = Assistente(indexado(), client=GeminiFalso(pedido=pedido)).responder(
        FRASE, contexto=contexto
    )
    sem = Assistente(indexado(), client=GeminiFalso(falhar=True)).responder(
        FRASE, contexto=contexto
    )

    for resposta in (com, sem):
        assert "Sim: medimos R$ 3.999,00" in resposta.texto
        assert "comunidaded00-20" in resposta.texto or "exemplo.com" in resposta.texto
        assert resposta.produtos == contexto.produtos
        assert resposta.cartoes == ()


def test_historico_sem_contexto_ou_item_fora_da_lista_pede_o_produto(bancos):
    console = produto("Console PlayStation 5 Slim", 4259.0, "MLB1")
    gemini = GeminiFalso(pedido=Pedido(tipo="historico", item=3))
    assistente = Assistente(indexado(), client=gemini)
    msg = "Me diga de qual produto você quer saber o preço."

    assert assistente.responder("e o preço?", contexto=None).texto == msg
    assert assistente.responder("e o preço?", contexto=_contexto(console)).texto == msg


def test_historico_de_produto_que_sumiu_na_resposta(bancos):
    gemini = GeminiFalso(pedido=Pedido(tipo="historico", item=1))
    resposta = Assistente(indexado(), client=gemini).responder(
        "melhor preço?", contexto=Contexto("ps5", ("sumiu",))
    )
    assert (
        resposta.texto
        == "Esse produto não aparece mais disponível nas lojas que acompanhamos."
    )


def test_busca_guarda_os_produtos_mostrados(bancos):
    console = produto("Console PlayStation 5 Slim", 4259.0, "MLB1")
    resposta = Assistente(indexado(), client=GeminiFalso(falhar=True)).responder("ps5")
    assert resposta.produtos == (console.product_id,)


def test_entender_manda_a_conversa_anterior_ao_gemini(bancos):
    enviados = []

    class Espiao(GeminiFalso):
        def generate_content(self, model, contents, config):
            enviados.append(contents)
            return super().generate_content(model, contents, config)

    gemini = Espiao(pedido=Pedido(tipo="produto", consulta="console ps5"))
    pedido = Assistente(indexado(), client=gemini).entender(
        "e o console?", "Busca anterior: ps5"
    )
    assert pedido.consulta == "console ps5"
    assert enviados[0].startswith("Conversa anterior:")
    assert "Busca anterior: ps5" in enviados[0] and "e o console?" in enviados[0]


# ---------- o atendente ----------


def test_atendente_passa_o_contexto_da_resposta_anterior(bancos):
    vistos = []

    class Espiao:
        def responder(self, texto, agora=None, contexto=None):
            from promo.assistente.conversa import Resposta

            vistos.append(contexto)
            return Resposta("ok", "ps5", 2, produtos=("p1", "p2"))

    atendente = Atendente(
        Espiao(), EntregaFalsa(), liberados=["5581988887777"], max_por_hora=10,
        esperar=lambda: None,
    )  # fmt: skip
    with banco.conectar() as conn:
        banco.registrar_mensagem(conn, "M1", "j", "5581988887777", "ps5")
        banco.registrar_mensagem(conn, "M2", "j", "5581988887777", "e o primeiro?")
    atendente.atender_uma()
    atendente.atender_uma()

    assert vistos[0] is None
    assert vistos[1] == Contexto("ps5", ("p1", "p2"))
    with banco.conectar() as conn:
        linha = conn.execute("SELECT produtos FROM mensagens WHERE id='M2'").fetchone()
    assert json.loads(linha["produtos"]) == ["p1", "p2"]

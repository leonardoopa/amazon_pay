"""O espelho: a publicacao do grupo-fonte sai igual no nosso, com o nosso link."""

from __future__ import annotations

import time

import pytest
from conftest import ContextoDeConexao

from promo import espelho
from promo.db import PRIORIDADE_ESPELHO, create_post, pending_posts
from promo.sources import grupo_wa
from promo.sources.grupo_wa import Pista

DESTINO = "120363411852739657@g.us"
ESPELHO = {"nome": "Radar", "jid": "1@g.us", "destino": DESTINO}

TEXTO = (
    "*KIT 4 SHORTS DRI-FIT* [masculino]\n\n"
    "De: ~R$ 141~\n🔥🔥 Por: *R$ 79*\n\n"
    "📏 Tamanhos: P, G, GG\n\n"
    "👉 Link para compra: https://meli.la/2ihU8vq\n\n"
    "Selecione a loja AGSTORE"
)

NOSSO = "https://meli.la/NOSSO"


def mensagem(texto=TEXTO, id_msg="AAA", idade=60, foto=True):
    conteudo = {"imageMessage": {"caption": texto}} if foto else {"conversation": texto}
    return {
        "key": {"id": id_msg},
        "messageTimestamp": int(time.time()) - idade,
        "message": conteudo,
    }


class Resultado:
    def __init__(self, links=None, recusados=None):
        self.links = links or {}
        self.recusados = recusados or {}


class Builder:
    def __init__(self, recusa=None, quebra=False):
        self.recusa, self.quebra, self.pedidos = recusa, quebra, []

    def create(self, urls):
        self.pedidos += urls
        if self.quebra:
            raise RuntimeError("painel fora")
        if self.recusa:
            return Resultado(recusados={u: self.recusa for u in urls})
        return Resultado(links={u: NOSSO for u in urls})


@pytest.fixture
def cenario(banco_em_memoria, monkeypatch):
    """Um grupo espelhado, a rede trocada por dubles, o banco em memoria."""
    estado = {"mensagens": [mensagem()], "builder": Builder(), "foto": "/f/AAA.jpg"}
    for modulo in ("promo.espelho", "promo.sources.affiliate"):
        monkeypatch.setattr(
            f"{modulo}.connect", lambda: ContextoDeConexao(banco_em_memoria)
        )
    monkeypatch.setattr(espelho, "load_espelhos", lambda: [ESPELHO])
    monkeypatch.setattr(espelho, "em_horario_silencioso", lambda: False)
    monkeypatch.setattr(espelho, "_config_da_evolution", lambda: _Config())
    monkeypatch.setattr(espelho, "ler_mensagens", lambda *a: estado["mensagens"])
    monkeypatch.setattr(espelho, "baixar_foto", lambda *a: estado["foto"])
    monkeypatch.setattr(espelho, "_link_builder", lambda: estado["builder"])
    monkeypatch.setattr(
        espelho,
        "produto_do_link",
        lambda url: Pista(
            external_id="MLB1234567",
            titulo="Kit 4 Shorts",
            origem="",
            url="https://www.mercadolivre.com.br/kit/p/MLB1234567",
        ),
    )
    # Como em producao: sem isto o banco de teste aceita link de produto que nao existe.
    banco_em_memoria.execute("PRAGMA foreign_keys = ON")
    estado["conn"] = banco_em_memoria
    return estado


class _Config:
    base_url = "http://evolution"
    instance = "i"
    api_key = "k"


def posts(estado):
    return estado["conn"].execute("SELECT * FROM posts ORDER BY id").fetchall()


def test_texto_e_foto_iguais_com_o_nosso_link_e_prioridade_maxima(cenario):
    assert espelho.espelhar() == 1

    (post,) = posts(cenario)
    assert post["copy"] == TEXTO.replace("https://meli.la/2ihU8vq", NOSSO)
    assert post["image_url"] == "/f/AAA.jpg"
    assert post["grupo_jid"] == DESTINO
    assert post["prioridade"] == PRIORIDADE_ESPELHO > 1
    assert post["price"] == 79
    assert post["product_id"] == "mercadolivre:MLB1234567"
    assert cenario["builder"].pedidos == [
        "https://www.mercadolivre.com.br/kit/p/MLB1234567"
    ]


def test_nao_grava_historico_de_preco(cenario):
    espelho.espelhar()
    total = cenario["conn"].execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    assert total == 0


def test_a_mesma_mensagem_sai_uma_vez_so(cenario):
    espelho.espelhar()
    assert espelho.espelhar() == 0
    assert len(posts(cenario)) == 1


def test_o_mesmo_produto_em_mensagem_nova_sai_de_novo(cenario):
    espelho.espelhar()
    cenario["mensagens"] = [mensagem(id_msg="BBB")]
    assert espelho.espelhar() == 1


def test_mensagem_velha_fica_de_fora(cenario):
    cenario["mensagens"] = [mensagem(idade=61 * 60)]
    assert espelho.espelhar() == 0


def test_sai_na_ordem_em_que_postaram(cenario):
    cenario["mensagens"] = [
        mensagem(id_msg="NOVA", idade=10),
        mensagem(texto="VELHA https://meli.la/x", id_msg="VELHA", idade=500),
    ]
    espelho.espelhar()
    assert posts(cenario)[0]["copy"] == f"VELHA {NOSSO}"


def test_troca_o_link_sem_comer_a_pontuacao(cenario):
    cenario["mensagens"] = [
        mensagem(texto="*corre* (meli.la/abc). *https://meli.la/z*")
    ]
    espelho.espelhar()
    assert posts(cenario)[0]["copy"] == f"*corre* ({NOSSO}). *{NOSSO}*"


def test_amazon_leva_a_nossa_tag(cenario, monkeypatch):
    monkeypatch.setattr(espelho, "amazon_partner_tag", lambda: "nossa-20")
    monkeypatch.setattr(espelho, "asin_do_link", lambda url: "B0ABCDEF12")
    cenario["mensagens"] = [mensagem(texto="Tenis https://amzn.to/abc")]
    assert espelho.espelhar() == 1
    assert posts(cenario)[0]["copy"] == (
        "Tenis https://www.amazon.com.br/dp/B0ABCDEF12?tag=nossa-20"
    )


@pytest.mark.parametrize(
    "texto",
    [
        "Tenis https://www.netshoes.com.br/p/1",
        "Tenis https://meli.la/ok e entre https://chat.whatsapp.com/XYZ",
        "Tenis meli.la/ok e www.lojaqualquer.com/x",
        "Bom dia, grupo!",
    ],
)
def test_o_que_nao_vira_link_nosso_nao_sai(cenario, texto, caplog):
    cenario["mensagens"] = [mensagem(texto=texto)]
    assert espelho.espelhar() == 0
    assert posts(cenario) == []
    assert "nao espelhada" in caplog.text


def test_recusa_do_programa_nao_sai_nem_tenta_de_novo(cenario):
    cenario["builder"] = Builder(recusa="inelegivel")
    assert espelho.espelhar() == 0
    cenario["builder"] = Builder()
    assert espelho.espelhar() == 0


@pytest.mark.parametrize("falha", ["builder", "foto"])
def test_falha_passageira_tenta_na_proxima(cenario, falha):
    if falha == "builder":
        cenario["builder"] = Builder(quebra=True)
    else:
        cenario["foto"] = ""
    assert espelho.espelhar() == 0
    cenario.update(builder=Builder(), foto="/f/AAA.jpg")
    assert espelho.espelhar() == 1


def test_mensagem_sem_foto_sai_sem_foto(cenario):
    cenario["mensagens"] = [mensagem(foto=False)]
    assert espelho.espelhar() == 1
    assert posts(cenario)[0]["image_url"] is None


def test_reacao_e_ignorada(cenario):
    cenario["mensagens"] = [
        {
            "key": {"id": "R"},
            "messageTimestamp": int(time.time()),
            "message": {"reactionMessage": {"text": "👍"}},
        }
    ]
    assert espelho.espelhar() == 0


def test_tira_da_fila_o_mesmo_produto_que_o_bot_ia_mandar(cenario):
    conn = cenario["conn"]
    conn.execute(
        "INSERT INTO products (id, source, external_id, title, url, first_seen_at,"
        " last_seen_at) VALUES ('mercadolivre:MLB1234567','mercadolivre',"
        "'MLB1234567','t','u','x','x')"
    )
    nosso = create_post(
        conn, "mercadolivre:MLB1234567", 80, 80, 0, "nosso", grupo_jid=DESTINO
    )
    geral = create_post(conn, "mercadolivre:MLB1234567", 80, 80, 0, "geral")
    espelho.espelhar()
    status = {p["id"]: p["status"] for p in posts(cenario)}
    assert status[nosso] == "expired"
    assert status[geral] == "pending"


def test_passa_na_frente_na_fila(cenario):
    conn = cenario["conn"]
    conn.execute(
        "INSERT INTO products (id, source, external_id, title, url, first_seen_at,"
        " last_seen_at) VALUES ('p','mercadolivre','p','t','u','x','x')"
    )
    create_post(conn, "p", 1, 1, 0, "repasse", grupo_jid=DESTINO, prioridade=1)
    espelho.espelhar()
    assert pending_posts(conn)[0]["prioridade"] == PRIORIDADE_ESPELHO


def test_silencio_nao_le_nada(cenario, monkeypatch):
    monkeypatch.setattr(espelho, "em_horario_silencioso", lambda: True)
    assert espelho.espelhar() == 0


def test_leitor_abre_a_mensagem_temporaria(monkeypatch):
    embrulhada = {
        "key": {"id": "E"},
        "message": {
            "ephemeralMessage": {"message": {"imageMessage": {"caption": "oi"}}}
        },
    }

    class Resposta:
        def raise_for_status(self):
            pass

        def json(self):
            return {"messages": {"records": [embrulhada]}}

    class Cliente:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **kw):
            return Resposta()

    monkeypatch.setattr(grupo_wa.httpx, "Client", Cliente)
    (registro,) = grupo_wa.ler_mensagens("http://e", "i", "k", "1@g.us")
    assert grupo_wa.tem_foto(registro)
    assert grupo_wa._texto_da_mensagem(registro) == "oi"


def test_o_watchlist_real_espelha_o_radar_no_esportes():
    (grupo,) = espelho.load_espelhos()
    assert grupo["jid"] == "120363416567666242@g.us"
    assert grupo["destino"] == DESTINO
    from promo.pipeline import load_grupos_fonte

    assert grupo["jid"] not in {f["jid"] for f in load_grupos_fonte()}


def test_anuncio_sem_cartao_usa_o_endereco_do_proprio_id(monkeypatch):
    """Pagina cujo og:title vem com o preco no fim: o cartao nao e achado, e o
    endereco sai do href que carrega o ID do anuncio. O href de OUTRO anuncio,
    da vitrine, nao serve."""
    html = (
        '<head><meta property="og:title" content="Camiseta X - R$ 79"></head>'
        '<body>{"item_id":"MLB2222397098"}'
        '<a href="https://produto.mercadolivre.com.br/MLB-4085597457-outra-_JM">o</a>'
        '<a href="https://produto.mercadolivre.com.br/MLB-2222397098-camiseta-_JM'
        '?searchVariation=1">c</a></body>'
    )
    transporte = grupo_wa.httpx.MockTransport(
        lambda req: grupo_wa.httpx.Response(200, text=html)
    )
    real = grupo_wa.httpx.Client
    monkeypatch.setattr(
        grupo_wa.httpx, "Client", lambda **kw: real(transport=transporte, **kw)
    )
    pista = grupo_wa.produto_do_link("https://meli.la/x")
    assert pista.url == (
        "https://produto.mercadolivre.com.br/MLB-2222397098-camiseta-_JM"
    )

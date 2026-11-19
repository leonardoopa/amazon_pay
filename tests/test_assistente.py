"""O assistente de busca no WhatsApp, de ponta a ponta, sem rede.

O Gemini e a Evolution entram como falsos; o SQLite e de verdade, em disco
temporario -- o assistente le o `promos.db` do bot e escreve no proprio.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.assistente import banco  # noqa: E402
from promo.assistente.busca import Busca, rrf, termos  # noqa: E402
from promo.assistente.conversa import (  # noqa: E402
    DIVULGACAO,
    Assistente,
    Pedido,
    fiel,
    pedido_simples,
    quando,
    veredito,
)
from promo.assistente.indice import (  # noqa: E402
    Embeddings,
    Vetores,
    normalizar,
    sincronizar,
    vetorizar_pendentes,
)
from promo.assistente.servico import (  # noqa: E402
    AVISO_PRIMEIRA_VEZ,
    ESPERA_APOS_FALHA,
    PAROU,
    Atendente,
    _laco,
    criar_app,
    liberado,
)
from promo.assistente.webhook import ler_evento  # noqa: E402
from promo.models import Offer  # noqa: E402

AGORA = datetime(2026, 11, 18, 18, 0, tzinfo=UTC)


@pytest.fixture
def bancos(tmp_path, monkeypatch):
    """O banco do bot e o do assistente, os dois em disco temporario."""
    from promo.db import init_db

    monkeypatch.setenv("DB_PATH", str(tmp_path / "promos.db"))
    monkeypatch.setenv("ASSISTENTE_DB_PATH", str(tmp_path / "assistente.db"))
    monkeypatch.setenv("AMAZON_PARTNER_TAG", "comunidaded00-20")
    monkeypatch.delenv("ML_AFFILIATE_COOKIE", raising=False)
    monkeypatch.delenv("GROUP_INVITE_URL", raising=False)
    init_db()
    return tmp_path


def produto(
    titulo: str,
    preco: float,
    external_id: str,
    source: str = "mercadolivre",
    historico: list[float] | None = None,
    dias_sem_ver: int = 0,
) -> Offer:
    """Um produto no banco do bot, com o preco de hoje e o historico pedido."""
    from promo.db import connect, record_offer

    oferta = Offer(
        source=source,
        external_id=external_id,
        title=titulo,
        price=preco,
        url=f"https://exemplo.com/{external_id}",
    )
    with connect() as conn:
        record_offer(conn, oferta)
        hoje = datetime.now(UTC).date()
        for n, valor in enumerate(historico or [], start=1):
            conn.execute(
                "INSERT INTO price_history (product_id, observed_on, price) "
                "VALUES (?, ?, ?)",
                (oferta.product_id, (hoje - timedelta(days=n)).isoformat(), valor),
            )
        if dias_sem_ver:
            visto = (datetime.now(UTC) - timedelta(days=dias_sem_ver)).isoformat()
            conn.execute(
                "UPDATE products SET last_seen_at = ? WHERE id = ?",
                (visto, oferta.product_id),
            )
    return oferta


# Sinonimos que o falso entende: e o que separa a busca por vetor da por texto.
SINONIMOS = {"videogame": "console", "fone": "headphone", "presente": "kit"}


class EmbeddingsFalsos(Embeddings):
    """Saco de palavras com sinonimos: uma dimensao por palavra. Deterministico.

    Uma dimensao por palavra, e nao hash em poucas dimensoes: com 32 delas,
    "geladeira" colidia com "air fryer", e o teste media a colisao do falso em
    vez da margem sobre a mediana.
    """

    def __init__(self) -> None:
        super().__init__(client=object(), modelo="falso", dim=512)
        self.chamadas = 0
        self._vocabulario: dict[str, int] = {}

    def _posicao(self, palavra: str) -> int:
        return self._vocabulario.setdefault(palavra, len(self._vocabulario))

    def _embed(self, textos, tarefa):
        self.chamadas += 1
        saida = []
        for texto in textos:
            v = np.zeros(self.dim, dtype=np.float32)
            for palavra in termos(texto):
                palavra = SINONIMOS.get(palavra, palavra)
                v[self._posicao(palavra) % self.dim] += 1.0
            saida.append(normalizar(v))
        return saida


def indexado(embeddings: Embeddings | None = None) -> Busca:
    with banco.conectar() as conn:
        sincronizar(conn)
        vetores = None
        if embeddings is not None:
            vetorizar_pendentes(conn, embeddings)
            vetores = Vetores.carregar(conn, embeddings.assinatura)
    return Busca(embeddings=embeddings, vetores=vetores)


# ---------- o evento da Evolution ----------


def evento(texto="tem ps5?", jid="5581988887777@s.whatsapp.net", **chave):
    return {
        "event": "messages.upsert",
        "instance": "ofertas",
        "data": {
            "key": {"remoteJid": jid, "fromMe": False, "id": "ABC123", **chave},
            "pushName": "Fulano",
            "message": {"conversation": texto},
            "messageTimestamp": int(AGORA.timestamp()) - 30,
        },
    }


def test_le_a_mensagem_privada_de_texto():
    [m] = ler_evento(evento(), AGORA)

    assert (m.id, m.telefone, m.texto) == ("ABC123", "5581988887777", "tem ps5?")
    assert m.jid == "5581988887777@s.whatsapp.net"


@pytest.mark.parametrize(
    "corpo",
    [
        evento(fromMe=True),  # a nossa propria resposta
        evento(jid="120363428560546806@g.us"),  # grupo
        evento(jid="status@broadcast"),
        evento(jid="123@newsletter"),
        evento(texto="   "),  # figurinha, audio
        {**evento(), "event": "connection.update"},
        evento(jid="99887766@lid"),  # LID sem o telefone junto
    ],
)
def test_o_que_nao_pede_resposta_fica_de_fora(corpo):
    assert ler_evento(corpo, AGORA) == []


def test_mensagem_antiga_reenviada_na_reconexao_fica_de_fora():
    corpo = evento()
    corpo["data"]["messageTimestamp"] = int((AGORA - timedelta(hours=3)).timestamp())

    assert ler_evento(corpo, AGORA) == []


def test_conta_por_lid_usa_o_telefone_que_vem_junto():
    corpo = evento(jid="99887766@lid", remoteJidAlt="5581988887777@s.whatsapp.net")

    [m] = ler_evento(corpo, AGORA)

    assert m.telefone == "5581988887777"
    assert m.jid == "5581988887777@s.whatsapp.net"


# ---------- indice e busca ----------


def test_a_busca_por_texto_acha_o_modelo_e_pega_o_registrado(bancos):
    """ "PlayStation®" vira o token "playstation": o caso que o `priority` do
    watchlist perdia por comparar pedaco de titulo."""
    produto("Console PlayStation® 5 Slim Edição Digital 825 GB", 3816.0, "MLB1")
    produto("Air Fryer Mondial 4L Preta", 299.0, "MLB2")

    achados = indexado().buscar("playstation 5")

    assert [r.external_id for r in achados] == ["MLB1"]


def test_produto_fora_da_janela_nao_entra(bancos):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1", dias_sem_ver=45)

    assert indexado().buscar("playstation") == []


def test_faixa_de_preco_vale(bancos):
    produto("Air Fryer Mondial 4L", 299.0, "MLB1")
    produto("Air Fryer Philips Walita 6L", 699.0, "MLB2")

    achados = indexado().buscar("air fryer", preco_max=400)

    assert [r.external_id for r in achados] == ["MLB1"]


def test_o_vetor_acha_o_que_o_texto_nao_acha(bancos):
    produto("Console Xbox Series S 512GB", 2299.0, "MLB1")
    embeddings = EmbeddingsFalsos()

    assert indexado().buscar("videogame") == []
    assert [r.external_id for r in indexado(embeddings).buscar("videogame")] == ["MLB1"]


def test_mesmo_produto_de_dois_vendedores_sai_uma_vez_e_o_mais_barato(bancos):
    produto("Console PlayStation 5 Slim Digital 825GB Branco", 3999.0, "MLB1")
    produto("Console PlayStation 5 Slim Digital 825GB Branco", 3816.0, "MLB2")

    achados = indexado().buscar("playstation 5 slim")

    assert [(r.external_id, r.preco) for r in achados] == [("MLB2", 3816.0)]


def test_o_titulo_trocado_refaz_o_vetor_e_o_resto_nao(bancos):
    produto("Fone Bluetooth JBL Tune 520", 199.0, "MLB1")
    produto("Air Fryer Mondial 4L", 299.0, "MLB2")
    embeddings = EmbeddingsFalsos()
    indexado(embeddings)
    produto("Fone Bluetooth JBL Tune 520BT Preto", 199.0, "MLB1")

    with banco.conectar() as conn:
        sincronizar(conn)
        refeitos = vetorizar_pendentes(conn, embeddings)

    assert refeitos == 1


def test_parecidos_sao_vizinhos_em_faixa_de_preco_parecida(bancos):
    produto("Headphone Sony WH-CH520 Bluetooth", 249.0, "MLB1")
    produto("Headphone JBL Tune 520 Bluetooth", 229.0, "MLB2")
    produto("Headphone Sony WH-1000XM5 Bluetooth", 2499.0, "MLB3")  # caro demais
    busca = indexado(EmbeddingsFalsos())

    [base] = busca.buscar("sony wh-ch520", limite=1)
    parecidos = busca.parecidos(base)

    assert [r.external_id for r in parecidos] == ["MLB2"]


def test_rrf_premia_quem_aparece_nas_duas_listas():
    assert rrf([1, 2, 3], [3, 4])[0] == 3


CATALOGO = [
    "Air Fryer Mondial 4L", "Cafeteira Nespresso Essenza", "Liquidificador Philco",
    "Whey Protein Growth 900g", "Creatina Max Titanium 300g", "Tenis Nike Revolution",
    "Camiseta Adidas Basica", "Perfume Malbec Boticario", "Shampoo Wella Fusion",
    "Protetor Solar La Roche", "Notebook Lenovo IdeaPad", "Monitor Gamer LG 24",
    "Mouse Logitech G203", "Teclado Redragon Kumara", "Cadeira Gamer ThunderX3",
    "Smartwatch Amazfit Bip", "Kindle Paperwhite", "Garrafa Stanley Quencher",
    "Mochila Samsonite", "Console Xbox Series S",
]  # fmt: skip


def test_com_catalogo_grande_o_vetor_nao_traz_o_que_nao_parece(bancos):
    """Sem a margem sobre a mediana, a busca por vetor sempre traz `k` itens --
    "geladeira" viria com o que estivesse mais perto, qualquer coisa."""
    for n, titulo in enumerate(CATALOGO):
        produto(titulo, 100.0 + n, f"MLB{n}")
    busca = indexado(EmbeddingsFalsos())

    assert busca.buscar("geladeira frost free") == []
    assert [r.titulo for r in busca.buscar("videogame")] == ["Console Xbox Series S"]


class EmbeddingsQueCaem(EmbeddingsFalsos):
    """Passa no primeiro lote e leva 429 no segundo."""

    def documentos(self, textos):
        if self.chamadas >= 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        return super().documentos(textos)


def test_o_429_no_meio_da_carga_devolve_o_que_ja_foi_feito(bancos):
    from promo.assistente.indice import LOTE_DE_EMBEDDING

    for n in range(LOTE_DE_EMBEDDING + 10):
        produto(f"Produto numero {n}", 10.0, f"MLB{n}")
    with banco.conectar() as conn:
        sincronizar(conn)
        feitos = vetorizar_pendentes(conn, EmbeddingsQueCaem())
        gravados = conn.execute("SELECT COUNT(*) FROM vetores").fetchone()[0]

    assert feitos == gravados == LOTE_DE_EMBEDDING


def test_o_servico_desligando_para_a_carga_entre_um_lote_e_outro(bancos):
    from promo.assistente.indice import LOTE_DE_EMBEDDING

    for n in range(LOTE_DE_EMBEDDING + 10):
        produto(f"Produto numero {n}", 10.0, f"MLB{n}")
    with banco.conectar() as conn:
        sincronizar(conn)
        feitos = vetorizar_pendentes(conn, EmbeddingsFalsos(), esperar=lambda: True)

    assert feitos == LOTE_DE_EMBEDDING


# ---------- veredito e hora ----------


def test_o_veredito_le_a_mediana_de_60_dias(bancos):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1", historico=[4259.0] * 10)

    [r] = indexado().buscar("playstation")

    assert r.mediana == 4259.0 and r.dias == 10
    assert veredito(r).startswith("Menor preço dos últimos 60 dias")


def test_sem_historico_o_veredito_nao_inventa(bancos):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1", historico=[4000.0] * 3)

    [r] = indexado().buscar("playstation")

    assert veredito(r) == "Ainda sem histórico suficiente para comparar o preço."


def test_a_hora_sai_em_horario_de_brasilia():
    visto = (AGORA - timedelta(minutes=10)).isoformat()

    assert quando(visto, AGORA) == "visto hoje às 14:50"


# ---------- a conversa ----------


class Resp:
    def __init__(self, text="", parsed=None):
        self.text = text
        self.parsed = parsed


class GeminiFalso:
    """Responde o pedido estruturado e o texto, ou levanta se mandarem."""

    def __init__(self, pedido=None, texto=None, falhar=False):
        self.models = self
        self._pedido = pedido
        self._texto = texto
        self._falhar = falhar
        self.textos_pedidos = 0

    def generate_content(self, model, contents, config):
        if self._falhar:
            raise RuntimeError("429 RESOURCE_EXHAUSTED")
        if config.response_mime_type == "application/json":
            return Resp(text=self._pedido.model_dump_json(), parsed=self._pedido)
        self.textos_pedidos += 1
        return Resp(text=self._texto(json.loads(contents)) if self._texto else "")


def test_o_texto_do_gemini_sai_quando_e_fiel(bancos):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1")

    def escreve(dados):
        item = dados["achados"][0]
        return f"Achei!\n*PS5 Slim* por {item['preco']} no ML\n{item['link']}"

    gemini = GeminiFalso(Pedido(consulta="playstation 5"), escreve)
    resposta = Assistente(indexado(), client=gemini).responder("tem ps5?", AGORA)

    assert resposta.texto.startswith("Achei!\n*PS5 Slim* por R$ 3.816,00")
    assert resposta.texto.endswith(DIVULGACAO)
    assert (resposta.consulta, resposta.resultados) == ("playstation 5", 1)


def test_preco_inventado_derruba_o_texto_e_sai_o_modelo_fixo(bancos):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1")

    def inventa(dados):
        return f"So hoje por R$ 2.999,00!\n{dados['achados'][0]['link']}"

    gemini = GeminiFalso(Pedido(consulta="playstation 5"), inventa)
    resposta = Assistente(indexado(), client=gemini).responder("tem ps5?", AGORA)

    assert "2.999,00" not in resposta.texto
    assert "R$ 3.816,00 no Mercado Livre" in resposta.texto


def test_sem_gemini_a_leitura_simples_e_o_modelo_fixo_respondem(bancos):
    produto("Air Fryer Mondial 4L", 299.0, "MLB1")
    produto("Air Fryer Philips Walita 6L", 699.0, "MLB2")

    resposta = Assistente(indexado(), client=GeminiFalso(falhar=True)).responder(
        "air fryer ate 400", AGORA
    )

    assert "Air Fryer Mondial 4L" in resposta.texto
    assert "Philips" not in resposta.texto


def test_o_link_da_amazon_sai_com_a_nossa_tag(bancos):
    produto("Kindle 11a Geracao 16GB", 499.0, "B0CFPJYX7P", source="amazon")
    gemini = GeminiFalso(Pedido(consulta="kindle"), falhar=False)

    resposta = Assistente(indexado(), client=gemini).responder("kindle", AGORA)

    assert (
        "https://www.amazon.com.br/dp/B0CFPJYX7P?tag=comunidaded00-20" in resposta.texto
    )


def test_cumprimento_recebe_as_boas_vindas_sem_busca(bancos):
    gemini = GeminiFalso(Pedido(tipo="conversa"))

    resposta = Assistente(indexado(), client=gemini).responder("oi, tudo bem?", AGORA)

    assert resposta.texto.startswith("Oi! Eu procuro preço")
    assert resposta.consulta is None


def test_nada_achado_diz_que_nao_achou(bancos):
    gemini = GeminiFalso(Pedido(consulta="geladeira brastemp", preco_max=2000))

    resposta = Assistente(indexado(), client=gemini).responder("geladeira", AGORA)

    assert resposta.texto.startswith("Não achei *geladeira brastemp* até R$ 2.000,00")


def test_a_leitura_simples_tira_o_teto_de_preco_do_texto():
    pedido = pedido_simples("tem air fryer até R$ 400?")

    assert (pedido.tipo, pedido.consulta, pedido.preco_max) == (
        "produto",
        "air fryer",
        400.0,
    )


def test_fiel_exige_todos_os_links_e_so_os_nossos():
    itens = [{"preco": "R$ 10,00", "veredito": "", "link": "https://a"}]

    assert fiel("R$ 10,00 https://a", itens)
    assert not fiel("R$ 10,00", itens)  # sumiu com o link
    assert not fiel("R$ 10,00 https://a https://golpe", itens)


# ---------- o atendimento ----------


class EntregaFalsa:
    def __init__(self, falhar=False):
        self.enviados: list[tuple[str, str]] = []
        self._falhar = falhar

    def send_text(self, texto, to=None):
        if self._falhar:
            raise RuntimeError("Evolution fora")
        self.enviados.append((to, texto))


class AssistenteFalso:
    def responder(self, texto, agora=None):
        from promo.assistente.conversa import Resposta

        return Resposta(f"resposta para {texto}", texto, 1)


def atendente(entrega, liberados=("5581988887777",), max_por_hora=10):
    return Atendente(
        AssistenteFalso(),
        entrega,
        liberados=list(liberados),
        max_por_hora=max_por_hora,
        esperar=lambda: None,
    )


def chega(texto, id="M1", telefone="5581988887777"):
    with banco.conectar() as conn:
        return banco.registrar_mensagem(
            conn, id, f"{telefone}@s.whatsapp.net", telefone, texto
        )


def status(id):
    with banco.conectar() as conn:
        linha = conn.execute(
            "SELECT status, motivo FROM mensagens WHERE id = ?", (id,)
        ).fetchone()
    return (linha["status"], linha["motivo"])


def test_a_primeira_resposta_leva_o_aviso_e_a_segunda_nao(bancos):
    entrega = EntregaFalsa()
    chega("tem ps5?", "M1")
    chega("e xbox?", "M2")
    a = atendente(entrega)

    a.atender_uma()
    a.atender_uma()

    assert entrega.enviados[0][1].endswith(AVISO_PRIMEIRA_VEZ)
    assert AVISO_PRIMEIRA_VEZ not in entrega.enviados[1][1]
    assert status("M2") == ("respondida", None)


def test_numero_fora_da_lista_nao_recebe_nada(bancos):
    entrega = EntregaFalsa()
    chega("tem ps5?", telefone="5511999990000")

    atendente(entrega).atender_uma()

    assert entrega.enviados == []
    assert status("M1") == ("ignorada", "fora da lista liberada")


def test_a_lista_aceita_o_numero_com_e_sem_o_nono_digito():
    assert liberado("558188887777", ["5581988887777"])
    assert liberado("5581988887777", ["558188887777"])
    assert not liberado("5581988887778", ["5581988887777"])


def test_parar_desliga_e_voltar_religa(bancos):
    entrega = EntregaFalsa()
    a = atendente(entrega)
    for n, texto in enumerate(["PARAR", "tem ps5?", "voltar", "tem ps5?"], 1):
        chega(texto, f"M{n}")
        a.atender_uma()

    assert [t for _, t in entrega.enviados][0] == PAROU
    assert status("M2") == ("ignorada", "pediu para parar")
    assert status("M4") == ("respondida", None)


def test_o_limite_por_hora_segura_o_excesso(bancos):
    entrega = EntregaFalsa()
    a = atendente(entrega, max_por_hora=2)
    for n in range(1, 4):
        chega(f"pergunta {n}", f"M{n}")
        a.atender_uma()

    assert len(entrega.enviados) == 2
    assert status("M3") == ("ignorada", "limite por hora")


def test_falha_no_envio_marca_erro_e_a_fila_segue(bancos):
    chega("tem ps5?", "M1")
    chega("e xbox?", "M2")
    a = atendente(EntregaFalsa(falhar=True))

    assert a.atender_uma() and a.atender_uma()
    assert status("M1")[0] == "erro" and status("M2")[0] == "erro"
    assert a.atender_uma() is False  # fila vazia


def test_a_mesma_mensagem_reenviada_nao_entra_duas_vezes(bancos):
    assert chega("tem ps5?", "M1") is True
    assert chega("tem ps5?", "M1") is False


# ---------- o webhook ----------


def test_o_webhook_so_aceita_o_token_certo(bancos, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSISTENTE_WEBHOOK_TOKEN", "segredo")
    monkeypatch.setenv("ASSISTENTE_NUMEROS", "5581988887777")
    cliente = TestClient(criar_app())
    corpo = evento()
    corpo["data"]["messageTimestamp"] = int(datetime.now(UTC).timestamp())

    assert cliente.post("/webhook/errado", json=corpo).status_code == 404
    resposta = cliente.post("/webhook/segredo", json=corpo)

    assert resposta.json() == {"novas": 1}
    assert status("ABC123") == ("pendente", None)


def test_o_webhook_nao_guarda_mensagem_de_quem_esta_fora_do_beta(bancos, monkeypatch):
    """O chip e o do grupo: o privado dele recebe de gente que nao esta no teste."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSISTENTE_WEBHOOK_TOKEN", "segredo")
    monkeypatch.setenv("ASSISTENTE_NUMEROS", "5581977776666")
    corpo = evento(jid="5581911112222@s.whatsapp.net")
    corpo["data"]["messageTimestamp"] = int(datetime.now(UTC).timestamp())

    resposta = TestClient(criar_app()).post("/webhook/segredo", json=corpo)

    assert resposta.json() == {"novas": 0}
    with banco.conectar() as conn:
        assert conn.execute("SELECT COUNT(*) FROM mensagens").fetchone()[0] == 0


def test_sem_token_configurado_o_webhook_recusa_tudo(bancos, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.delenv("ASSISTENTE_WEBHOOK_TOKEN", raising=False)

    resposta = TestClient(criar_app()).post("/webhook/qualquer", json=evento())

    assert resposta.status_code == 404


# ---------- a subida do servico ----------


def test_o_banco_do_assistente_nasce_em_wal_antes_das_threads(bancos):
    """Na 1a subida local as duas threads abriram o arquivo novo juntas, e a
    troca para WAL deu "database is locked" na hora, sem esperar o timeout."""
    import sqlite3

    from fastapi.testclient import TestClient

    caminho = bancos / "assistente.db"
    assert not caminho.exists()

    with TestClient(criar_app()):
        assert caminho.exists()
        with sqlite3.connect(caminho) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_falha_na_thread_tenta_de_novo_em_um_minuto_e_nao_na_pausa_cheia():
    import sqlite3
    import threading

    esperas = []

    class Parada(threading.Event):
        def wait(self, timeout=None):
            esperas.append(timeout)
            self.set()
            return True

    def indice():
        raise sqlite3.OperationalError("database is locked")

    _laco(Parada(), indice, 1800.0)

    assert esperas == [ESPERA_APOS_FALHA]


# ---------- o teste de verdade, 18/11/2026: "me mande promocoes de PS5" ----------


def saiu_no_grupo(oferta: Offer, preco: float, horas: int = 1) -> None:
    """Um post enviado ao grupo ha `horas` horas, pelo `preco` dado."""
    from promo.db import connect

    enviado = (datetime.now(UTC) - timedelta(hours=horas)).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO posts (product_id, price, baseline, discount_pct, copy, "
            "status, created_at, sent_at) VALUES (?, ?, ?, ?, ?, 'sent', ?, ?)",
            (oferta.product_id, preco, preco * 1.3, 23.0, "post", enviado, enviado),
        )


def test_o_pedido_em_si_nao_vira_palavra_de_busca():
    """O "mande" sobrou e trouxe um gel de acido mandelico."""
    assert pedido_simples("me mande promoções de PS5").consulta == "ps5"


@pytest.mark.parametrize(
    "pergunta, titulo",
    [
        ("ps5", "Console PlayStation® 5 Slim Edição Digital 825 GB"),
        ("playstation 5", "Controle DualSense PS5 Branco"),
        ("airfryer", "Fritadeira Air Fryer Mondial 4L"),
        ("air fryer", "Airfryer Philips Walita 6L"),
        ("gta 6", "Jogo Grand Theft Auto VI PS5"),
        ("gta vi", "Pré-venda GTA 6 PS5"),
    ],
)
def test_sigla_e_nome_por_extenso_se_acham(bancos, pergunta, titulo):
    produto(titulo, 100.0, "MLB1")

    assert [r.titulo for r in indexado().buscar(pergunta)] == [titulo]


def test_com_todos_os_termos_achados_a_busca_nao_completa_com_qualquer_um(bancos):
    """ "notebook gamer ate 4000" voltava com fonte de notebook e mochila."""
    produto("Notebook Gamer Acer Nitro V15 RTX 4050", 5999.0, "MLB1")
    produto("Fonte Notebook Hoopson 19V", 48.0, "MLB2")
    produto("Mochila Notebook Rosa", 206.0, "MLB3")

    assert indexado().buscar("notebook gamer", preco_max=4000) == []


def test_sem_nenhum_item_com_todos_os_termos_vale_o_que_tiver_parte(bancos):
    produto("Geladeira Consul Frost Free 340L Branca", 2389.0, "MLB1")

    achados = indexado().buscar("geladeira frost free 340 litros")

    assert [r.external_id for r in achados] == ["MLB1"]


def test_a_oferta_que_saiu_no_grupo_vem_primeiro(bancos):
    produto("Portal PS5 Jogue com seu console PS5", 1893.0, "MLB1")
    console = produto("Console PlayStation 5 Slim Digital", 4259.0, "MLB2")
    saiu_no_grupo(console, 4259.0)

    achados = indexado().buscar("ps5")

    assert [r.external_id for r in achados] == ["MLB2", "MLB1"]
    assert achados[0].no_grupo is not None and achados[1].no_grupo is None


def test_oferta_do_grupo_cujo_preco_subiu_nao_passa_na_frente(bancos):
    produto("Portal PS5 Jogue com seu console PS5", 1893.0, "MLB1")
    console = produto("Console PlayStation 5 Slim Digital", 4599.0, "MLB2")
    saiu_no_grupo(console, 4259.0)  # hoje custa 8% mais que no post

    achados = indexado().buscar("ps5")

    assert [r.external_id for r in achados] == ["MLB1", "MLB2"]
    assert achados[1].no_grupo is None


def test_abaixo_da_mediana_passa_na_frente_de_quem_nao_tem_historico(bancos):
    produto("Controle DualSense PS5 Branco", 399.0, "MLB1")
    produto("Controle Sem Fio Gamesir G7 PS5", 329.0, "MLB2", historico=[449.0] * 10)

    achados = indexado().buscar("controle ps5")

    assert [r.external_id for r in achados] == ["MLB2", "MLB1"]


def test_a_resposta_conta_que_a_oferta_saiu_no_grupo(bancos):
    console = produto("Console PlayStation 5 Slim Digital - R$ 4.650", 4259.0, "MLB1")
    saiu_no_grupo(console, 4259.0, horas=0)

    resposta = Assistente(indexado(), client=GeminiFalso(falhar=True)).responder(
        "me mande promoções de PS5"
    )

    assert "*Console PlayStation 5 Slim Digital*" in resposta.texto  # sem o preco
    assert "Saiu no grupo hoje às" in resposta.texto


def test_preco_parado_nao_e_o_menor_dos_ultimos_60_dias(bancos):
    """Gel em R$ 69,90 todo dia saiu com selo de menor preco."""
    produto("Gel Limpeza Facial Botik 150g", 69.90, "MLB1", historico=[69.90] * 10)

    [r] = indexado().buscar("gel limpeza")

    assert veredito(r) == "Mesmo preço dos últimos 60 dias (R$ 69,90)."


def test_queda_depois_de_preco_parado_e_o_menor_dos_ultimos_60_dias(bancos):
    produto("Gel Limpeza Facial Botik 150g", 59.90, "MLB1", historico=[69.90] * 10)

    [r] = indexado().buscar("gel limpeza")

    assert veredito(r).startswith("Menor preço dos últimos 60 dias")


def test_o_nome_sai_sem_o_preco_colado_e_sem_o_simbolo_de_marca():
    from promo.assistente.conversa import nome_de_vitrine

    assert nome_de_vitrine("Console PlayStation® 5 Slim - R$ 4.650") == (
        "Console PlayStation 5 Slim"
    )
    assert nome_de_vitrine("Resident Evil Requiem - Ps5 - R$ 279,91") == (
        "Resident Evil Requiem - Ps5"
    )


def test_vetor_de_parte_do_catalogo_nao_entra_na_busca(bancos):
    """O 429 parou a carga em 250 de 12 mil, e o vizinho de "PS5" foi um Papai
    Noel. Abaixo de `COBERTURA_MINIMA`, a busca fica so com o texto."""
    from promo.assistente.indice import LOTE_DE_EMBEDDING
    from promo.assistente.servico import indexar

    for n in range(LOTE_DE_EMBEDDING + 10):
        produto(f"Produto numero {n}", 10.0, f"MLB{n}")
    busca = Busca(embeddings=EmbeddingsQueCaem())

    resumo = indexar(busca, busca._embeddings)

    assert resumo["vetorizados"] == LOTE_DE_EMBEDDING
    assert resumo["cobertura_dos_vetores"] < 0.9
    assert busca._vetores is None


def test_com_o_catalogo_todo_vetorizado_a_busca_usa_o_vetor(bancos):
    from promo.assistente.servico import indexar

    produto("Console Xbox Series S 512GB", 2299.0, "MLB1")
    busca = Busca(embeddings=EmbeddingsFalsos())

    resumo = indexar(busca, busca._embeddings)

    assert resumo["cobertura_dos_vetores"] == 1.0
    assert [r.external_id for r in busca.buscar("videogame")] == ["MLB1"]


def test_o_produto_do_grupo_segue_na_frente_no_anuncio_mais_barato(bancos):
    """O console do grupo (R$ 4.259) tinha outro anuncio a R$ 3.844: ficava o
    mais barato, sem a marca do grupo, e os acessorios passavam na frente."""
    do_grupo = produto("Console PlayStation 5 Slim Digital 825GB", 4259.0, "MLB1")
    saiu_no_grupo(do_grupo, 4259.0)
    produto("Console PlayStation 5 Slim Digital 825GB", 3844.0, "MLB2")
    produto("Base De Carregamento DualSense PS5", 165.0, "MLB3", historico=[219.0] * 9)

    achados = indexado().buscar("ps5")

    assert [(r.external_id, r.preco) for r in achados] == [
        ("MLB2", 3844.0),
        ("MLB3", 165.0),
    ]
    assert achados[0].no_grupo is not None


def test_preco_com_cara_de_erro_de_leitura_fica_de_fora(bancos):
    """Geladeira de R$ 2.258,86 medida um dia por R$ 200,00."""
    produto("Geladeira Midea Frost Free 491L", 200.0, "MLB1", historico=[2258.86] * 9)

    assert indexado().buscar("geladeira") == []


# ---------- 19/11/2026: "Em que voce pode me ajudar?" virou busca ----------


@pytest.mark.parametrize(
    "texto",
    [
        "Em que você pode me ajudar?",
        "o que você faz?",
        "como funciona?",
        "Olá, tudo bem?",
        "bom dia!",
        "obrigado!",
    ],
)
def test_pergunta_sobre_o_assistente_e_conversa_sem_gemini(texto):
    assert pedido_simples(texto).tipo == "conversa"


@pytest.mark.parametrize(
    "texto, consulta",
    [
        ("bom dia, tem ps5?", "ps5"),
        ("quanto tá o ps5?", "ps5"),
        ("vale a pena comprar o ps5 agora?", "ps5"),
        ("você pode me mandar promoção de air fryer?", "air fryer"),
        ("quero comprar uma tv 50 polegadas", "tv 50 polegadas"),
    ],
)
def test_o_que_sobra_do_pedido_e_so_o_produto(texto, consulta):
    pedido = pedido_simples(texto)

    assert (pedido.tipo, pedido.consulta) == ("produto", consulta)


def test_sem_gemini_a_pergunta_de_ajuda_recebe_as_boas_vindas(bancos):
    produto("Shampoo Head & Shoulders Fragrância Poderosa 650ml", 26.0, "MLB1")

    resposta = Assistente(indexado(), client=GeminiFalso(falhar=True)).responder(
        "Em que você pode me ajudar?", AGORA
    )

    assert resposta.texto.startswith("Oi! Eu procuro preço")
    assert "Shampoo" not in resposta.texto

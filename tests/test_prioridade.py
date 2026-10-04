"""Vaga garantida para os temas que o grupo pediu.

Muitas mulheres entraram no grupo e pediram cabelo, pele e suplemento. A
watchlist cresceu de 104 para 132 termos e trouxe 252 produtos novos de
beleza para a carteira -- e a primeira rodada depois disso nao postou
nenhum deles. As cinco vagas ficaram com -72%, -67% e -54% de outras
categorias.

Nao havia nada errado com aquelas ofertas. O ranking decide por numero, e o
numero maior mora onde estiver; o que faltava era publico, e publico nao
aparece na ordenacao. Dai a reserva.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.pipeline import (  # noqa: E402
    _priorizar_temas,
    e_prioritaria,
    load_priority,
)

TEMAS = ["wella", "protetor solar", "melatonina"]


@pytest.fixture(autouse=True)
def reserva_de_duas(monkeypatch):
    """A reserva vem do .env e e 0 por padrao (desligada)."""
    monkeypatch.setenv("PRIORITY_RESERVE", "2")
    monkeypatch.setenv("PRICE_FOCUS_MAX", "0")


def oferta(titulo: str, preco: float, desconto: float, fonte="mercadolivre"):
    return ScoredOffer(
        offer=Offer(
            source=fonte,
            external_id=f"MLB{abs(hash(titulo)) % 10**6}",
            title=titulo,
            price=preco,
            url="https://mercadolivre.com.br/p/MLB1",
        ),
        baseline=preco / (1 - desconto / 100),
        discount_pct=desconto,
        observations=9,
        lowest_ever=False,
        verified=False,
    )


# ---------- reconhecer o tema ----------


def test_reconhece_o_tema_no_titulo():
    assert e_prioritaria(
        oferta("Máscara Wella Blondorplex 150ml", 124.0, 48).offer, TEMAS
    )


def test_acento_e_caixa_nao_atrapalham():
    """O vendedor digita o titulo; o tema e escrito a mao no watchlist.json."""
    temas = ["mascara capilar"]
    assert e_prioritaria(oferta("MÁSCARA CAPILAR Truss", 90.0, 20).offer, temas)


def test_titulo_sem_tema_nao_e_prioritario():
    assert not e_prioritaria(
        oferta("Smart Tv 43 Samsung Crystal", 1500.0, 30).offer, TEMAS
    )


def test_sem_temas_nada_e_prioritario():
    assert not e_prioritaria(oferta("Shampoo Wella", 80.0, 24).offer, [])


# ---------- a reserva ----------


def test_prioritaria_entra_mesmo_com_desconto_menor():
    """O caso que motivou tudo: -29% de pele contra -72% de outra coisa."""
    candidatas = [
        oferta("Ventilador Portatil Mini", 58.80, 72),
        oferta("Escova Limpeza Mamadeira", 25.74, 67),
        oferta("Air Fryer WAP Barbecue", 846.0, 54),
        oferta("Protetor Solar Anthelios FPS 80", 79.90, 33),
    ]

    titulos = [s.offer.title for s in _priorizar_temas(candidatas, 3, TEMAS)]

    assert "Protetor Solar Anthelios FPS 80" in titulos


def test_a_reserva_e_teto_e_nao_toma_a_rodada():
    """Rodada so de tema prioritario nao pode virar rodada so de shampoo."""
    candidatas = [oferta(f"Shampoo Wella {i}", 80.0 + i, 40) for i in range(5)]
    candidatas += [oferta("Smart Tv Philco", 1200.0, 50)]

    saida = _priorizar_temas(candidatas, 3, TEMAS)
    prioritarias = [s for s in saida if e_prioritaria(s.offer, TEMAS)]

    assert len(saida) == 3
    assert len(prioritarias) == 2


def test_sem_candidato_prioritario_nenhuma_vaga_se_perde():
    """Reserva vazia nao pode encolher a rodada."""
    candidatas = [oferta(f"Smart Tv {i}", 1000.0 + i, 50) for i in range(4)]

    assert len(_priorizar_temas(candidatas, 3, TEMAS)) == 3


def test_reserva_sobrando_volta_para_o_prioritario():
    """Se o resto da fila nao encheu, o prioritario que ficou de fora concorre
    de novo -- senao a reserva viraria vaga perdida no outro sentido."""
    candidatas = [oferta(f"Shampoo Wella {i}", 80.0 + i, 40) for i in range(4)]

    assert len(_priorizar_temas(candidatas, 3, TEMAS)) == 3


def test_reserva_zero_desliga(monkeypatch):
    """Padrao do .env e 0: quem nao configurou nao muda de comportamento."""
    monkeypatch.setenv("PRIORITY_RESERVE", "0")
    candidatas = [
        oferta("Ventilador Portatil", 58.80, 72),
        oferta("Escova Mamadeira", 25.74, 67),
        oferta("Protetor Solar Anthelios", 79.90, 33),
    ]

    saida = _priorizar_temas(candidatas, 2, TEMAS)

    assert [s.discount_pct for s in saida] == [72, 67]


def test_lista_de_temas_vazia_nao_muda_nada():
    candidatas = [
        oferta("Ventilador Portatil", 58.80, 72),
        oferta("Protetor Solar Anthelios", 79.90, 33),
    ]

    saida = _priorizar_temas(candidatas, 1, [])

    assert saida[0].discount_pct == 72


def test_reserva_maior_que_a_cota_nao_estoura(monkeypatch):
    monkeypatch.setenv("PRIORITY_RESERVE", "9")
    candidatas = [oferta("Shampoo Wella", 80.0, 40), oferta("Smart Tv", 900.0, 60)]

    assert len(_priorizar_temas(candidatas, 1, TEMAS)) == 1


def test_lista_vazia_nao_estoura():
    assert _priorizar_temas([], 3, TEMAS) == []


def test_o_foco_em_barato_continua_valendo_dentro_da_reserva(monkeypatch):
    """A reserva muda quem concorre pela vaga, nao o criterio que decide."""
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "0")
    # Fixar tambem esta. `config.load_dotenv()` carrega o .env da maquina, e
    # quem tiver PRIORITY_IGNORES_PRICE_FOCUS=1 la ve este teste falhar sem ter
    # mudado uma linha de codigo. Teste descreve o comportamento padrao, nao a
    # configuracao de quem roda.
    monkeypatch.setenv("PRIORITY_IGNORES_PRICE_FOCUS", "0")
    candidatas = [
        oferta("Kit Wella Oil Reflections Profissional", 612.0, 55),
        oferta("Máscara Wella Oil Reflections 150ml", 116.0, 47),
        oferta("Smart Tv Philco", 1200.0, 60),
    ]

    saida = _priorizar_temas(candidatas, 1, TEMAS)

    assert saida[0].offer.price == 116.0


# ---------- o arquivo de verdade ----------


# Os temas chegaram em duas levas e a segunda nao pode custar a primeira: sao
# publicos diferentes no mesmo grupo. Um caso por leva, para o teste dizer QUAL
# some em vez de so acusar que a lista encolheu.


def test_os_temas_de_cabelo_pele_e_suplemento_continuam():
    """Primeira leva, 30/08/2026: o que as mulheres que entraram pediram."""
    faltando = {
        "wella",
        "truss",
        "cerave",
        "cicaplast",
        "protetor solar",
        "melatonina",
        "shampoo",
        "oleo capilar",
        "hidratante",
    } - set(load_priority())

    assert faltando == set()


def test_progressiva_nao_e_mais_tema():
    """Saiu em 02/09/2026, junto com a entrada das marcas de salao. O termo
    continua na watchlist com teto de R$ 300 -- deixou de ter vaga reservada,
    nao deixou de ser procurado."""
    assert "progressiva" not in load_priority()


def test_as_marcas_de_salao_continuam():
    """Terceira leva, 02/09/2026. Medidas contra a API antes de entrar:

        "widi care"       8 produtos, 3 com desconto (ate -46%)
        "lola cosmetics"  6 produtos
        "brae"            5 produtos, 2 com desconto (-35% e -53%)
        "kerastase"       3 produtos, 1 com desconto (-18%)

    "haskell" devolveu ZERO e nao entrou como termo -- so como tema, igual a
    Nike e adidas: a marca ainda chega pela vitrine, e la quem decide a vaga
    e o tema.
    """
    faltando = {
        "kerastase",
        "loreal professionnel",
        "redken",
        "cadiveu",
        "brae",
        "lola cosmetics",
        "widi care",
        "haskell",
        "salon line",
    } - set(load_priority())

    assert faltando == set()


def test_os_temas_de_moda_e_corrida_continuam():
    """Segunda leva, 31/08/2026. Nike, adidas, Hering, Osklen e Vivara nao tem
    termo de busca correspondente -- o catalogo do ML nao os tem, e o tema e a
    UNICA via deles ate o grupo. Tirar daqui e apaga-los."""
    faltando = {
        "nike",
        "adidas",
        "puma",
        "hering",
        "osklen",
        "casio",
        "vivara",
        "occitane",
        "tree hut",
        "body splash",
        "pistola de massagem",
        "cadeira gamer",
        "yopro",
        "isotonico",
        "barra de proteina",
        "carboidrato",
        "new balance",
        "dux nutrition",
        "dux human",
        "omega 3",
        "lupo",
        "legging",
        "supercoffee",
        "caffeine army",
        "perfume",
        "calcinha",
        "cueca",
        "principia",
        "pele sensivel",
        "nespresso",
        "dolce gusto",
        "whey",
        "cafe",
        "capsula",
    } - set(load_priority())

    assert faltando == set()


def test_mochila_nao_e_mais_tema():
    """Saiu em 01/09/2026: o grupo estava recebendo mochila demais. Sao 87
    produtos na carteira casando em "mochila", todos disputando as vagas
    reservadas -- e o tema existe para dar vaga a quem NAO ganharia sozinho,
    nao para quem ja ganha.

    Nao resolve sozinho: 8 das 10 bolsas dos ultimos 80 posts vieram da
    vitrine crua, onde nao ha filtro. Tirar do `priority` corta a vaga
    garantida, nao a fonte.
    """
    assert "mochila" not in load_priority()


def test_capsula_pega_suplemento_e_esta_certo_assim():
    """Eu tinha tirado "capsula" por achar que era tema de cafe mal escolhido:
    medido, 84 produtos, e os primeiros eram Testo Essencial, Vitamina D3+K2,
    Omega 3 e Curcuma. Errei o julgamento, nao a medicao -- capsula de
    suplemento converte, e o tema existe para isso. Cafe em capsula continua
    entrando pelos termos proprios e por nespresso/dolce gusto."""
    assert "capsula" in load_priority()


def test_os_temas_ja_vem_normalizados():
    """`e_prioritaria` normaliza so o titulo; o tema tem que chegar pronto."""
    assert all(t == t.lower() and t.isascii() for t in load_priority())


def test_tema_curto_demais_nao_entra():
    """ "oleo" sozinho pegaria oleo de motor, "kit" pegaria a watchlist inteira.

    Quatro e o piso porque "nike" e "puma" tem quatro letras e sao marcas que
    o grupo pediu pelo nome. Nao ha palavra comum de produto em portugues que
    as contenha, entao o falso positivo que o piso de cinco evitava nao existe
    para essas duas.
    """
    assert all(len(t) >= 4 for t in load_priority())


def test_tema_vazio_e_ignorado(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(
        json.dumps({"keywords": [], "priority": ["wella", "", "   "]}), encoding="utf-8"
    )

    assert load_priority(caminho) == ["wella"]


def test_priority_ausente_e_o_normal(tmp_path):
    """Campo opcional: watchlist antiga nao pode quebrar."""
    caminho = tmp_path / "w.json"
    caminho.write_text(json.dumps({"keywords": [{"term": "x"}]}), encoding="utf-8")

    assert load_priority(caminho) == []


# ---------- preco e desconto nao barram o tema ----------
#
# "esses produtos podemos deixar com preco alto, nao tem nenhum problema. O
# ideal e que sempre que eles aparecerem, independente de preco ou desconto,
# eles tem que ser enviados no grupo -- esses sao os que mais convertem."
#
# A conta e outra nesses temas: -9% num shampoo de salao de R$ 400 e mais
# dinheiro que -60% num item de R$ 25, e a pessoa que entrou no grupo pelo
# nome do produto ja quer o produto.


def test_o_foco_em_barato_pode_ser_desligado_dentro_da_reserva(monkeypatch):
    monkeypatch.setenv("PRIORITY_IGNORES_PRICE_FOCUS", "1")
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "0")
    candidatas = [
        oferta("Kit Wella Oil Reflections Profissional", 612.0, 55),
        oferta("Máscara Wella Oil Reflections 150ml", 116.0, 47),
    ]

    saida = _priorizar_temas(candidatas, 1, TEMAS)

    assert saida[0].offer.price == 612.0  # o caro nao perde mais a vaga


def test_desligar_o_foco_nao_afeta_o_resto_da_fila(monkeypatch):
    """Fora da reserva, produto caro continua cedendo vez ao barato."""
    monkeypatch.setenv("PRIORITY_IGNORES_PRICE_FOCUS", "1")
    monkeypatch.setenv("PRICE_FOCUS_MAX", "300")
    monkeypatch.setenv("PRICE_FOCUS_RESERVE", "0")
    monkeypatch.setenv("PRIORITY_RESERVE", "0")
    candidatas = [
        oferta("Smart Tv Philco 50", 1200.0, 60),
        oferta("Fone Bluetooth JBL", 99.0, 20),
    ]

    saida = _priorizar_temas(candidatas, 1, TEMAS)

    assert saida[0].offer.price == 99.0


# ---------- piso de desconto proprio ----------


def regras(min_discount: float = 5.0):
    from promo.config import Rules

    return Rules(
        min_discount_pct=min_discount,
        baseline_window_days=60,
        min_observations=4,
        repost_cooldown_days=3,
        max_offers_per_run=5,
    )


def anuncio(titulo: str, preco: float, riscado: float | None = None) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB1",
        title=titulo,
        price=preco,
        url="https://mercadolivre.com.br/p/MLB1",
        original_price=riscado,
    )


def test_o_tema_usa_o_piso_proprio(monkeypatch):
    from promo.pipeline import regras_do_tema

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    wella = anuncio("Shampoo Wella Invigo Nutri-Enrich", 399.90, 441.80)

    assert regras_do_tema(wella, regras(), TEMAS).min_discount_pct == 0


def test_fora_do_tema_o_piso_geral_continua(monkeypatch):
    from promo.pipeline import regras_do_tema

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    tv = anuncio("Smart Tv Philco 50", 1200.0, 1300.0)

    assert regras_do_tema(tv, regras(), TEMAS).min_discount_pct == 5.0


def test_sem_piso_configurado_nada_muda(monkeypatch):
    """Vazio e o padrao: quem nao configurou usa o piso geral nos dois casos."""
    from promo.pipeline import regras_do_tema

    monkeypatch.delenv("PRIORITY_MIN_DISCOUNT_PCT", raising=False)
    wella = anuncio("Shampoo Wella Invigo", 399.90, 441.80)

    assert regras_do_tema(wella, regras(), TEMAS).min_discount_pct == 5.0


def test_o_piso_zero_deixa_passar_o_desconto_de_9_por_cento(monkeypatch, tmp_path):
    """O caso real: -9% num Wella de R$ 400, recusado pelo piso de 5%... nao,
    aceito -- mas -2% seria recusado, e e esse que o piso do tema libera."""
    import sqlite3

    from promo.db import SCHEMA
    from promo.pipeline import regras_do_tema
    from promo.scoring import score_campaign

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    quase_nada = anuncio("Shampoo Wella Invigo", 435.00, 441.80)  # -1,5%

    geral = score_campaign(conn, quase_nada, regras())
    tema = score_campaign(conn, quase_nada, regras_do_tema(quase_nada, regras(), TEMAS))

    assert geral is None
    assert tema is not None


def test_o_piso_do_tema_nao_inventa_desconto(monkeypatch):
    """Piso 0 nao e "sem desconto": sem preco riscado maior que o atual nao ha
    o que anunciar, e o post nunca afirma uma queda que nao existe."""
    import sqlite3

    from promo.db import SCHEMA
    from promo.pipeline import regras_do_tema
    from promo.scoring import score_campaign

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    sem_queda = anuncio("Shampoo Wella Invigo", 441.80, None)

    assert (
        score_campaign(conn, sem_queda, regras_do_tema(sem_queda, regras(), TEMAS))
        is None
    )


def test_o_cooldown_de_repeticao_continua_valendo_no_tema(monkeypatch):
    """ "Sempre que aparecer" nao pode virar o mesmo shampoo a cada rodada --
    e isso que faz sair do grupo justamente quem o tema queria trazer."""
    from promo.pipeline import regras_do_tema

    monkeypatch.setenv("PRIORITY_MIN_DISCOUNT_PCT", "0")
    wella = anuncio("Shampoo Wella Invigo", 399.90, 441.80)

    assert regras_do_tema(wella, regras(), TEMAS).repost_cooldown_days == 3


# ---------- watchlist: teto de preco dos termos do tema ----------


def test_os_termos_do_tema_nao_tem_teto_de_preco():
    """Teto de R$ 400 no termo cortava o kit Wella de R$ 612 antes de ele
    chegar a pontuacao -- o filtro corria na coleta, nao na selecao."""
    from promo.pipeline import load_priority, load_watchlist

    temas = load_priority()
    com_teto = [
        w.term
        for w in load_watchlist()
        if w.max_price is not None and any(tema in w.term for tema in temas)
    ]

    assert com_teto == []


def test_os_termos_fora_do_tema_mantem_teto():
    """Soltar o teto e para o tema, nao para a watchlist inteira: sem teto,
    'notebook' traz servidor de R$ 40 mil."""
    from promo.pipeline import load_watchlist

    tetos = {w.term: w.max_price for w in load_watchlist()}

    assert tetos["notebook"] == 5000
    assert tetos["monitor gamer"] == 3000


# ---------- os temas que o grupo pediu ----------


def test_stanley_e_tema_e_os_termos_dele_existem():
    """Garrafa e copo Stanley entraram a pedido do grupo em 04/09/2026.

    Os termos sao compostos de proposito. Medido na API do ML no mesmo dia, o
    termo `stanley` sozinho devolve o livro do Paul Stanley, um filme do
    Kubrick e uma furadeira de 550W -- a marca de ferramenta e a de garrafa
    tem o mesmo nome. Os compostos devolvem so o termico:

        quencher stanley       11 produtos, 3 com desconto (ate 40%)
        stanley copo termico   12 produtos
        copo stanley           11 produtos
        stanley termica         9 produtos
        caneca stanley          4 produtos
        stanley aerolight       3 produtos, 2 com desconto
        garrafa stanley         2 produtos

    Ficaram de fora `stanley` puro, pelo motivo acima, e `garrafa termica
    stanley`, que devolve 7 garrafas genericas e nenhuma Stanley.
    """
    from promo.pipeline import load_priority, load_watchlist

    termos = {w.term for w in load_watchlist()}

    assert "stanley" in load_priority()
    assert "quencher stanley" in termos
    assert "garrafa stanley" in termos
    assert "stanley" not in termos
    assert "garrafa termica stanley" not in termos


def test_o_tema_stanley_pega_a_garrafa_cara():
    """O ponto de por Stanley no tema: a Aerolight de R$ 275 passa do teto de
    R$ 150 que `garrafa termica` carrega, e so escapa dele por termo proprio
    sem teto."""
    from promo.pipeline import e_prioritaria, load_priority, load_watchlist

    tetos = {w.term: w.max_price for w in load_watchlist()}
    garrafa = anuncio(
        "Garrafa Termica Stanley Aerolight Fast Flow Polar 710ml", 275.74, 309.90
    )

    assert tetos["garrafa termica"] == 150  # o termo generico segue com teto
    assert tetos["stanley aerolight"] is None
    assert e_prioritaria(garrafa, load_priority())


def test_kit_body_splash_cai_no_tema_que_ja_existia():
    """`body splash` ja era tema, e tema casa por substring do titulo -- o kit
    entra sem tema novo. O termo e para a DESCOBERTA achar o kit, que a busca
    por `body splash` sozinha nao trazia.

    Eram dois termos ate 18/09/2026. "kit presente body splash" caiu na poda:
    desde que entrou nao trouxe um produto sequer para o catalogo nem gerou um
    post, e perguntado ao vivo devolveu 5 resultados, nenhum casando o termo.
    Quem sustenta o caso e "kit body splash".
    """
    from promo.pipeline import e_prioritaria, load_priority, load_watchlist

    temas = load_priority()
    termos = {w.term for w in load_watchlist()}
    kit = anuncio("Kit Body Splash + Creme Victoria's Secret Rush", 296.00, 349.00)

    assert e_prioritaria(kit, temas)
    assert "kit body splash" in termos


def test_perfume_ja_era_tema_e_continua():
    """Pedido de novo em 04/09/2026. Ja estava: 8 termos sem teto e 112
    produtos com `perfume` no titulo na carteira."""
    from promo.pipeline import load_priority, load_watchlist

    assert "perfume" in load_priority()
    perfumes = [w for w in load_watchlist() if "perfume" in w.term]
    assert len(perfumes) >= 8
    assert all(w.max_price is None for w in perfumes)


# ---------- prioridade pela pagina de origem, 18/09/2026 ----------


def test_o_que_vem_da_pagina_de_perfumes_e_prioritario():
    """ "lembrando de prioridade aos produtos daqueles links que te mandei,
    principalmente os perfumes e as coisas para mulheres".

    O caso real: "Perfume Sedutor Arabe Sabah 100ml" e o primeiro item da
    pagina de Perfumes e nao cita marca nenhuma da lista. Pelo titulo ele nunca
    seria prioritario; pela origem, e.
    """
    from promo.models import Offer
    from promo.pipeline import e_prioritaria, load_priority

    temas = load_priority()
    generico = Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title="Perfume Sedutor Arabe Sabah 100ml Original Feminino",
        price=99.0,
        url="https://www.mercadolivre.com.br/p/MLB1",
        vitrine_categoria="MLB6284",
    )

    assert e_prioritaria(generico, temas)


def test_sem_a_origem_o_mesmo_titulo_nao_seria_prioritario():
    """Guarda o teste acima de passar por acaso: se "perfume" entrar na lista
    de temas algum dia, o caso acima verdeja sozinho e para de provar nada."""
    from promo.models import Offer
    from promo.pipeline import e_prioritaria, load_priority

    sem_origem = Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title="Sedutor Arabe Sabah 100ml Original",
        price=99.0,
        url="https://www.mercadolivre.com.br/p/MLB1",
    )

    assert not e_prioritaria(sem_origem, load_priority())


def test_pagina_nao_marcada_nao_da_prioridade():
    """So as quatro paginas que o dono nomeou. Calcados vem da vitrine tambem e
    nao vira prioridade por isso."""
    from promo.models import Offer
    from promo.pipeline import e_prioritaria, load_priority

    calcado = Offer(
        source="ml_ofertas",
        external_id="MLB2",
        title="Chinelo Rider Masculino Preto",
        price=49.0,
        url="https://www.mercadolivre.com.br/p/MLB2",
        vitrine_categoria="MLB23262",
    )

    assert not e_prioritaria(calcado, load_priority())


def test_as_quatro_paginas_do_dono_estao_marcadas():
    """Invariante de configuracao: perfume, maquiagem, pele e cabelo."""
    from promo.pipeline import load_vitrine_prioritarias

    assert load_vitrine_prioritarias() == {
        "MLB6284",
        "MLB1248",
        "MLB199407",
        "MLB1263",
    }


def test_a_vitrine_carimba_a_pagina_de_origem(monkeypatch):
    """Sem o carimbo a prioridade por origem nao teria como existir: a vitrine
    nao devolve categoria em campo nenhum.

    `monkeypatch` e nao atribuicao direta: `_payload` e do modulo, e trocar sem
    restaurar derruba os testes de parsing da vitrine que rodam depois.
    """
    from promo.sources import ml_ofertas

    fonte = ml_ofertas.MLOfertas.__new__(ml_ofertas.MLOfertas)
    card = {
        "metadata": {"id": "MLB9", "url": "www.mercadolivre.com.br/p/MLB9"},
        "components": [
            {"type": "title", "title": {"text": "Perfume Qualquer 100ml"}},
            {"type": "price", "price": {"current_price": {"value": 50.0}}},
        ],
    }

    class Resposta:
        text = ""

        def raise_for_status(self):
            pass

    monkeypatch.setattr(
        fonte,
        "_client",
        type("C", (), {"get": staticmethod(lambda *a, **k: Resposta())})(),
        raising=False,
    )
    monkeypatch.setattr(
        ml_ofertas,
        "_payload",
        lambda _: {"appProps": {"pageProps": {"data": {"items": [{"card": card}]}}}},
    )

    achadas = fonte._pagina({"category": "MLB6284"}, "MLB6284")

    assert achadas[0].vitrine_categoria == "MLB6284"

    sem_categoria = fonte._pagina(None, None)

    assert sem_categoria[0].vitrine_categoria == ""


def test_o_teto_da_categoria_nao_corta_o_que_o_tema_resgata():
    """Mesmo defeito de `test_os_termos_do_tema_nao_tem_teto_de_preco`, um nivel
    acima: na categoria em vez da keyword.

    `collect_categories` aplica `category.max_price` na COLETA, antes de
    qualquer pontuacao -- `found = [o for o in found if o.price <=
    category.max_price]`. Um produto cortado ali nunca chega a `score_campaign`,
    entao a prioridade nao tem o que resgatar. Foi o que o kit Wella de R$ 612
    ensinou sobre o teto do termo, e MLB1132 repetia com carta de Pokemon.

    Medido no catalogo de producao em 29/09/2026: dos 104 titulos Pokemon, 41
    estavam acima do teto de R$250 e 28 desses eram prioritarios pelos temas que
    entraram em 28/09. Os dois maiores descontos do dia estavam entre os
    cortados -- box de R$399,99 (de R$979,99, -59,2%) e display de R$327,92 (de
    R$649,98, -49,5%).
    """
    from promo.pipeline import load_categories

    brinquedos = next(c for c in load_categories() if c.id == "MLB1132")

    assert brinquedos.max_price is None or brinquedos.max_price >= 400, (
        "com teto abaixo de 400 o box de R$399,99 com 59,2% de desconto e "
        "cortado na coleta, antes de a prioridade valer"
    )

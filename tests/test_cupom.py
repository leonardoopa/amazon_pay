"""Cupom so sai onde ele de fato vale.

Levantado em 08/09/2026 a partir de sete posts de um grupo concorrente. O que
eles fazem: repassam cupons de campanha do proprio ML -- VALEMAIS,
OFERTASEMPRE, PAGUEMENOS, VALEAGORA aparecem em perfume, secador, terno e
cueca, ou seja, nao sao do produto nem exclusivos do grupo. E quando um pode
falhar, listam alternativas:

    Use o Cupom: PAGUEMENOS ou VALEAGORA ou OFERTASEMPRE

O motivo da alternativa e real: cupom do ML tem limite de uso, e quando o
primeiro estoura o segundo ainda pega. Mas eles listam e torcem -- por isso o
post seguinte precisa avisar "se o cupom der erro, tente pela aba anonima".

Cupom do ML tem regra. Medido no mesmo dia: o PAGUEMENOS publicado dava 18% so
em roupas e acessorios, com minimo de R$ 29. Citar isso num secador de cabelo
e o caminho mais curto para a pessoa clicar, falhar e desconfiar do grupo
inteiro -- que e exatamente o que o cupom deveria evitar.

Entao aqui a alternativa so aparece quando ela se aplica: validade, tema e
valor minimo conferidos antes, por produto.
"""

from __future__ import annotations

import json
import sqlite3
import sys

import pytest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer  # noqa: E402
from promo.pipeline import cupom_para, load_coupon, load_coupons  # noqa: E402

ONTEM = (date.today() - timedelta(days=1)).isoformat()
AMANHA = (date.today() + timedelta(days=30)).isoformat()


def escrever(tmp_path: Path, bloco: dict) -> Path:
    caminho = tmp_path / "watchlist.json"
    caminho.write_text(json.dumps({"keywords": [], "coupon": bloco}), encoding="utf-8")
    return caminho


@pytest.fixture
def com_preco_de_cupom(monkeypatch):
    """Liga `PRECO_LEVA_O_CUPOM` para exercitar o CALCULO.

    O calculo continua correto e testado; o que mudou em 09/09/2026 foi a
    decisao de nao usa-lo, porque cupom do ML morre por consumo e o preco
    ficava errado na maior parte do tempo. Manter estes testes verdes e o que
    permite religar a chave sem reescrever nada.
    """
    from promo import pipeline

    monkeypatch.setattr(pipeline, "PRECO_LEVA_O_CUPOM", True)


def oferta(titulo: str, preco: float = 100.0, fonte: str = "mercadolivre") -> Offer:
    return Offer(
        source=fonte,
        external_id="X1",
        title=titulo,
        price=preco,
        url="https://exemplo/x1",
    )


# ---------- validade ----------


def test_cupom_no_prazo_entra(tmp_path):
    caminho = escrever(
        tmp_path, {"codes": [{"code": "VALEMAIS", "ate": AMANHA, "geral": True}]}
    )

    assert [c["code"] for c in load_coupons(caminho)] == ["VALEMAIS"]


def test_cupom_vencido_fica_de_fora(tmp_path):
    """Vencido e pior que ausente: a pessoa clica, tenta, falha."""
    caminho = escrever(tmp_path, {"codes": [{"code": "VELHO", "ate": ONTEM}]})

    assert load_coupons(caminho) == []


def test_cupom_sem_validade_nao_entra(tmp_path):
    """Cupom do ML sempre vence, e alguns em horas -- o VALEMAIS publicado em
    08/09/2026 dizia "disponivel apenas em 08/09/26". Sem data nao da para
    saber se ainda vale, e a duvida nao pode virar post."""
    caminho = escrever(tmp_path, {"codes": [{"code": "SEMPRE", "geral": True}]})

    assert load_coupons(caminho) == []


def test_lista_vazia_nao_estoura(tmp_path):
    assert load_coupons(escrever(tmp_path, {"codes": []})) == []


def test_bloco_ausente_nao_estoura(tmp_path):
    caminho = tmp_path / "watchlist.json"
    caminho.write_text(json.dumps({"keywords": []}), encoding="utf-8")

    assert load_coupons(caminho) == []


def test_codigo_vazio_e_ignorado(tmp_path):
    caminho = escrever(tmp_path, {"codes": [{"code": "  ", "ate": AMANHA}]})

    assert load_coupons(caminho) == []


# ---------- o formato antigo continua valendo ----------


def test_formato_antigo_de_um_cupom_so(tmp_path):
    caminho = escrever(tmp_path, {"code": "VALEMAIS", "ate": AMANHA, "geral": True})

    assert [c["code"] for c in load_coupons(caminho)] == ["VALEMAIS"]


def test_formato_antigo_vencido_tambem_sai(tmp_path):
    caminho = escrever(tmp_path, {"code": "VELHO", "ate": ONTEM})

    assert load_coupons(caminho) == []


def test_load_coupon_devolve_o_primeiro(tmp_path):
    caminho = escrever(
        tmp_path,
        {"codes": [
            {"code": "UM", "ate": AMANHA, "geral": True},
            {"code": "DOIS", "ate": AMANHA, "geral": True},
        ]},
    )

    assert load_coupon(caminho) == "UM"


def test_load_coupon_sem_cupom_devolve_none(tmp_path):
    assert load_coupon(escrever(tmp_path, {"codes": []})) is None


# ---------- a regra de tema ----------


def test_cupom_de_roupa_sai_em_roupa():
    """PAGUEMENOS, medido em 08/09/2026: 18% so em roupas e acessorios."""
    cupons = [{"code": "PAGUEMENOS", "temas": ["cueca", "camiseta"], "minimo": 29.0}]

    assert cupom_para(oferta("Kit 5 Cuecas Boxer Microfibra", 40.0), cupons) == "PAGUEMENOS"


def test_cupom_de_roupa_nao_sai_em_secador():
    """O erro que faz a pessoa desconfiar do grupo."""
    cupons = [{"code": "PAGUEMENOS", "temas": ["cueca", "camiseta"], "minimo": 29.0}]

    assert cupom_para(oferta("Secador de Cabelos Taiff Easy 1700w", 133.0), cupons) is None


def test_cupom_sem_tema_vale_para_tudo():
    cupons = [{"code": "VALEMAIS", "temas": [], "minimo": 1.0}]

    assert cupom_para(oferta("Secador de Cabelos Taiff", 133.0), cupons) == "VALEMAIS"


def test_o_tema_ignora_acento_e_caixa():
    cupons = [{"code": "X", "temas": ["oleo capilar"], "minimo": 1.0}]

    assert cupom_para(oferta("ÓLEO CAPILAR Extraordinário", 30.0), cupons) == "X"


# ---------- a regra de valor minimo ----------


def test_abaixo_do_minimo_nao_cita_o_cupom():
    cupons = [{"code": "PAGUEMENOS", "temas": [], "minimo": 29.0}]

    assert cupom_para(oferta("Meia Kit 3 Pares", 19.90), cupons) is None


def test_no_minimo_exato_vale():
    cupons = [{"code": "PAGUEMENOS", "temas": [], "minimo": 29.0}]

    assert cupom_para(oferta("Camiseta Basica", 29.0), cupons) == "PAGUEMENOS"


# ---------- alternativas ----------


def test_dois_cupons_validos_saem_como_alternativa():
    """Cupom do ML tem limite de uso: quando um estoura, o outro ainda pega."""
    cupons = [
        {"code": "OFERTASEMPRE", "temas": [], "minimo": 1.0},
        {"code": "VALEMAIS", "temas": [], "minimo": 1.0},
    ]

    assert cupom_para(oferta("Terno Italiano Slim", 123.0), cupons) == (
        "OFERTASEMPRE ou VALEMAIS"
    )


def test_a_alternativa_que_nao_serve_fica_de_fora():
    """A diferenca para o concorrente: eles listam tres e torcem, e por isso
    precisam avisar 'se der erro, tente pela aba anonima'."""
    cupons = [
        {"code": "OFERTASEMPRE", "temas": [], "minimo": 1.0},
        {"code": "PAGUEMENOS", "temas": ["cueca"], "minimo": 1.0},
    ]

    assert cupom_para(oferta("Secador Taiff", 133.0), cupons) == "OFERTASEMPRE"


# ---------- a loja de origem ----------


def test_cupom_do_ml_nunca_sai_em_oferta_da_amazon():
    """Sao cupons de campanha do ML. O checkout da Amazon nao aceita, e o post
    mandaria a pessoa tentar um codigo que nunca vai funcionar."""
    cupons = [{"code": "VALEMAIS", "temas": [], "minimo": 1.0}]

    assert cupom_para(oferta("Creatina 300g", 40.85, fonte="amazon"), cupons) is None


def test_a_mesma_oferta_no_ml_leva_o_cupom():
    cupons = [{"code": "VALEMAIS", "temas": [], "minimo": 1.0}]

    assert cupom_para(oferta("Creatina 300g", 40.85), cupons) == "VALEMAIS"


# ---------- a watchlist de produção ----------


def test_a_watchlist_real_tem_o_bloco_de_cupom():
    """Sem o bloco, `load_coupons` devolveria vazio em silêncio e ninguém
    perceberia que a campanha parou de sair."""
    dados = json.loads(
        (Path(__file__).resolve().parents[1] / "watchlist.json").read_text("utf-8")
    )

    assert "codes" in (dados.get("coupon") or {})


# ---------- falha fechado ----------
#
# Medido em 08/09/2026 sobre os 37 cupons do ML publicados no Promobit: 12
# (32%) vinham com o campo de instrucoes literalmente "-" -- sem regra, sem
# data, sem minimo. Um deles era o OFERTASEMPRE, um dos que o grupo
# concorrente postou. Tres ja estavam VENCIDOS e continuavam listados como
# validos, e seis venciam no mesmo dia.
#
# A primeira versao tratava ausencia como permissao: sem `ate` nunca vencia,
# sem `temas` valia para tudo. Com um terco da fonte sem regra, isso produzia
# exatamente o post que o cupom deveria evitar.


def test_cupom_sem_data_nao_entra(tmp_path):
    """Cupom do ML vence, e alguns em horas: o VALEMAIS publicado em 08/09
    dizia "disponivel apenas em 08/09/26"."""
    caminho = escrever(tmp_path, {"codes": [{"code": "SEMDATA", "geral": True}]})

    assert load_coupons(caminho) == []


def test_cupom_sem_tema_e_sem_geral_nao_entra(tmp_path):
    """Ausencia de regra nao e permissao. A fonte publica quase nunca diz a
    categoria -- 20 dos 25 cupons com texto apontavam a elegibilidade para um
    container do ML que responde 403."""
    caminho = escrever(tmp_path, {"codes": [{"code": "SEMREGRA", "ate": AMANHA}]})

    assert load_coupons(caminho) == []


def test_cupom_geral_declarado_entra(tmp_path):
    """`geral: true` e a afirmacao explicita de quem cadastrou: eu conferi, vale
    para tudo. Diferente de nao ter escrito nada."""
    caminho = escrever(
        tmp_path, {"codes": [{"code": "VALEMAIS", "ate": AMANHA, "geral": True}]}
    )

    assert [c["code"] for c in load_coupons(caminho)] == ["VALEMAIS"]


def test_formato_antigo_sem_data_tambem_nao_entra(tmp_path):
    """O formato antigo passa pelo mesmo funil -- senao a compatibilidade
    viraria a porta dos fundos do fail-open."""
    caminho = escrever(tmp_path, {"code": "VALEMAIS"})

    assert load_coupons(caminho) == []


# ---------- casamento por ID de categoria ----------
#
# O cupom lido do painel traz a regra como ID do ML (MLB1430 e "Calcados,
# Roupas e Bolsas"), que e o mesmo vocabulario de `products.category`. Isso e
# a regra oficial da campanha, nao uma heuristica de titulo -- e por isso ela
# manda quando existe.


def com_categoria(titulo: str, categoria: str | None, preco: float = 100.0) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="X1",
        title=titulo,
        price=preco,
        url="https://exemplo/x1",
        category=categoria,
    )


OFERTASEMPRE = {
    "code": "OFERTASEMPRE",
    "minimo": 79.0,
    "categorias": ["MLB1430", "MLB107292", "MLB188064"],
    "temas": [],
}


def test_produto_na_categoria_do_cupom_recebe():
    """MLB1430 e Calcados, Roupas e Bolsas -- uma das vinte do OFERTASEMPRE."""
    tenis = com_categoria("Tenis adidas Runfalcon", "MLB1430", 129.0)

    assert cupom_para(tenis, [OFERTASEMPRE]) == "OFERTASEMPRE"


def test_produto_de_outra_categoria_nao_recebe():
    secador = com_categoria("Secador Taiff Easy 1700w", "MLB1276", 133.0)

    assert cupom_para(secador, [OFERTASEMPRE]) is None


def test_produto_sem_categoria_nao_recebe_cupom_de_categoria():
    """Falha fechado, e isso pesa: so 24,8% da carteira tem `category`, porque
    a vitrine -- 89% do que sai -- nao devolve o campo. Adivinhar pelo titulo
    aqui mandaria a pessoa tentar um codigo que nao aplica."""
    sem = com_categoria("Tenis adidas Runfalcon", None, 129.0)

    assert cupom_para(sem, [OFERTASEMPRE]) is None


def test_categoria_certa_mas_abaixo_do_minimo_nao_recebe():
    """O minimo do OFERTASEMPRE e R$ 79."""
    meia = com_categoria("Kit 3 Meias", "MLB1430", 39.90)

    assert cupom_para(meia, [OFERTASEMPRE]) is None


def test_a_categoria_manda_quando_existe():
    """Com `categorias` preenchido, `temas` nao e consultado -- senao a regra
    oficial da campanha perderia para o nosso palpite."""
    cupom = dict(OFERTASEMPRE, temas=["secador"])
    secador = com_categoria("Secador Taiff", "MLB1276", 133.0)

    assert cupom_para(secador, [cupom]) is None


def test_cupom_do_watchlist_sem_categoria_continua_por_tema():
    """Cupom cadastrado a mao nao tem ID: a regra dele e o tema."""
    manual = {"code": "PAGUEMENOS", "minimo": 29.0, "categorias": [], "temas": ["cueca"]}
    cueca = com_categoria("Kit 5 Cuecas Boxer", None, 40.0)

    assert cupom_para(cueca, [manual]) == "PAGUEMENOS"


def test_painel_e_watchlist_convivem_na_mesma_lista():
    manual = {"code": "MANUAL", "minimo": 1.0, "categorias": [], "temas": []}
    tenis = com_categoria("Tenis adidas", "MLB1430", 129.0)

    assert cupom_para(tenis, [OFERTASEMPRE, manual]) == "OFERTASEMPRE ou MANUAL"


def test_cupom_do_painel_tambem_nao_sai_na_amazon():
    creatina = Offer(
        source="amazon",
        external_id="B07DVJC66X",
        title="Creatina 300g",
        price=129.0,
        url="https://exemplo",
        category="MLB1430",
    )

    assert cupom_para(creatina, [OFERTASEMPRE]) is None


# ---------- prioritario com cupom vai na frente ----------
#
# Pedido em 08/09/2026. A justificativa esta na raridade: o cupom cobre 20
# categorias, exige minimo, e so 24,8% da carteira tem `category` -- a
# vitrine, que e 89% do que sai, nao devolve esse campo. Medido no historico,
# 34 de 2.964 posts enviados teriam recebido cupom. Quando um desses aparece,
# ele nao pode perder a vaga para mais um desconto grande qualquer.


def ordenar_como_a_rodada(ofertas, cupons, temas):
    """Ordena pela chave REAL de `run`, e nao por uma copia.

    A copia existiu e ficou defasada: quando o cupom virou criterio da fila, o
    teste continuou ordenando pela regra antiga e passou a afirmar o que o
    codigo nao fazia mais."""
    from promo.pipeline import chave_da_fila

    ordenadas = sorted(
        ofertas, key=lambda s: chave_da_fila(s, cupons, temas), reverse=True
    )
    return [s.offer.external_id for s in ordenadas]


def pontuada(external_id, titulo, desconto, categoria=None, preco=100.0):
    from promo.models import ScoredOffer

    return ScoredOffer(
        offer=Offer(
            source="mercadolivre",
            external_id=external_id,
            title=titulo,
            price=preco,
            url="https://exemplo",
            category=categoria,
        ),
        baseline=preco * 2,
        discount_pct=desconto,
        observations=9,
        lowest_ever=False,
        verified=True,
    )


TEMAS = ["nike", "adidas", "whey"]
CUPOM = {"code": "OFERTASEMPRE", "minimo": 79.0, "categorias": ["MLB1430"], "temas": []}


def test_prioritario_com_cupom_passa_na_frente_de_desconto_maior():
    """40% num produto qualquer perde para 20% num tema com cupom."""
    ofertas = [
        pontuada("A", "Cafeteira Eletrica", 40.0, "MLB1276", 200.0),
        pontuada("B", "Tenis adidas Runfalcon", 20.0, "MLB1430", 129.0),
    ]

    assert ordenar_como_a_rodada(ofertas, [CUPOM], TEMAS)[0] == "B"


def test_prioritario_sem_cupom_nao_ganha_o_topo():
    """Nao e o tema sozinho que sobe: e a soma das duas coisas."""
    ofertas = [
        pontuada("A", "Cafeteira Eletrica", 40.0, "MLB1276", 200.0),
        pontuada("B", "Tenis adidas Runfalcon", 20.0, "MLB1276", 129.0),
    ]

    assert ordenar_como_a_rodada(ofertas, [CUPOM], TEMAS)[0] == "A"


def test_cupom_sem_tema_prioritario_ganha_de_quem_nao_tem_cupom():
    """Desde 08/09/2026 o cupom sozinho ja sobe: e desconto que nao aparece no
    anuncio. Antes disso so a dupla tema+cupom subia, e a cafeteira com 40%
    passava na frente da camisa com cupom."""
    ofertas = [
        pontuada("A", "Cafeteira Eletrica", 40.0, "MLB1276", 200.0),
        pontuada("B", "Camisa Social Lisa", 20.0, "MLB1430", 129.0),
    ]

    assert ordenar_como_a_rodada(ofertas, [CUPOM], TEMAS)[0] == "B"


def test_entre_dois_com_cupom_o_desconto_decide():
    """O cupom ordena contra quem NAO tem; entre iguais, o desconto volta a
    mandar."""
    ofertas = [
        pontuada("A", "Camisa Social Lisa", 15.0, "MLB1430", 129.0),
        pontuada("B", "Calca Jeans Reta", 35.0, "MLB1430", 129.0),
    ]

    assert ordenar_como_a_rodada(ofertas, [CUPOM], TEMAS) == ["B", "A"]


def test_entre_duas_premiadas_o_desconto_decide():
    ofertas = [
        pontuada("A", "Tenis nike Revolution", 15.0, "MLB1430", 129.0),
        pontuada("B", "Tenis adidas Runfalcon", 35.0, "MLB1430", 129.0),
    ]

    assert ordenar_como_a_rodada(ofertas, [CUPOM], TEMAS) == ["B", "A"]


def test_sem_cupom_nenhum_a_ordem_e_a_de_antes():
    """Desligar cupom nao pode reordenar a fila."""
    ofertas = [
        pontuada("A", "Tenis adidas Runfalcon", 20.0, "MLB1430", 129.0),
        pontuada("B", "Cafeteira Eletrica", 40.0, "MLB1276", 200.0),
    ]

    assert ordenar_como_a_rodada(ofertas, [], TEMAS) == ["B", "A"]


def test_produto_sem_categoria_recebe_pelo_tema_derivado():
    """A segunda via que faz o cupom alcancar a vitrine, que e 89% do que sai
    e nao devolve categoria. O tema saiu do nome oficial da categoria da
    campanha, nao de palpite."""
    cupom = dict(OFERTASEMPRE, temas=["camiseta", "legging", "short"])
    da_vitrine = com_categoria("Kit 5 Camisetas Hering Basicas", None, 129.0)

    assert cupom_para(da_vitrine, [cupom]) == "OFERTASEMPRE"


def test_produto_sem_categoria_e_fora_do_tema_nao_recebe():
    cupom = dict(OFERTASEMPRE, temas=["camiseta", "legging"])
    da_vitrine = com_categoria("Cafeteira Eletrica Mondial", None, 129.0)

    assert cupom_para(da_vitrine, [cupom]) is None


def test_com_categoria_o_id_decide_e_o_tema_nao_e_consultado():
    """O ID e a regra oficial: se o produto tem categoria e ela nao esta na
    campanha, nao adianta o titulo casar um tema."""
    cupom = dict(OFERTASEMPRE, temas=["camiseta"])
    camiseta_fora = com_categoria("Camiseta Tecnica", "MLB1276", 129.0)

    assert cupom_para(camiseta_fora, [cupom]) is None


def test_cupom_de_categoria_sem_tema_nao_alcanca_produto_sem_categoria():
    """Quando a API de categorias esta fora e nao ha tema derivado, falha
    fechado em vez de publicar codigo que talvez nao aplique."""
    cupom = dict(OFERTASEMPRE, temas=[])
    da_vitrine = com_categoria("Kit 5 Camisetas Hering", None, 129.0)

    assert cupom_para(da_vitrine, [cupom]) is None


# ---------- o cache guarda dado e idade em chaves separadas ----------
#
# Medido em producao em 08/09/2026, na primeira leitura depois do deploy:
#
#     WARNING Nao consegui ler os cupons guardados:
#             Invalid isoformat string: '[{"code": "OFERTASEMPRE", ...
#
# `hours_since` faz `datetime.fromisoformat` no valor da chave que recebe.
# Guardar o JSON dos cupons na MESMA chave da idade fazia toda leitura
# estourar, e a excecao zerava a lista inteira. O sintoma foi o pior
# possivel: zero posts com cupom, e um aviso que so aparecia no log.
#
# Nenhum teste pegou porque nenhum exercitava gravar e reler.


def test_a_idade_e_os_dados_nao_dividem_chave():
    from promo.pipeline import CUPONS_LIDOS_EM, CUPONS_META

    assert CUPONS_META != CUPONS_LIDOS_EM


def test_o_cache_sobrevive_a_um_ciclo_de_gravar_e_reler(tmp_path, monkeypatch):
    """O ciclo inteiro: grava, carimba a idade, e le de volta sem estourar."""
    import json as _json

    from promo import pipeline
    from promo.db import SCHEMA, get_meta, hours_since, now, set_meta

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    cupons = [{"code": "VALEMAIS", "ate": AMANHA, "minimo": 1.0, "temas": []}]
    set_meta(conn, pipeline.CUPONS_META, _json.dumps(cupons))
    set_meta(conn, pipeline.CUPONS_LIDOS_EM, now().isoformat())

    # A leitura da idade nao pode estourar por causa do que foi guardado.
    assert hours_since(conn, pipeline.CUPONS_LIDOS_EM) < 1
    assert _json.loads(get_meta(conn, pipeline.CUPONS_META))[0]["code"] == "VALEMAIS"


def test_qualquer_oferta_com_cupom_vem_antes_das_sem():
    """Pedido em 08/09/2026: produto com cupom e mais atraente. Cupom e
    desconto que nao aparece no anuncio -- quem ve o produto no ML paga o
    preco cheio, e so quem esta no grupo sabe do codigo."""
    ofertas = [
        pontuada("A", "Cafeteira Eletrica", 40.0, "MLB1276", 200.0),
        pontuada("B", "Panela de Pressao", 25.0, "MLB1276", 129.0),
    ]
    geral = {"code": "VALEMAIS", "minimo": 100.0, "temas": [], "categorias": []}

    # B tem cupom (preco 129 >= minimo 100), A tambem (200 >= 100).
    # Com os dois elegiveis, o desconto decide.
    assert ordenar_como_a_rodada(ofertas, [geral], TEMAS) == ["A", "B"]

    # Com minimo alto, so A alcanca -- e sobe mesmo se tivesse menos desconto.
    caro = {"code": "VALEMAIS", "minimo": 150.0, "temas": [], "categorias": []}
    invertidas = [
        pontuada("A", "Cafeteira Eletrica", 10.0, "MLB1276", 200.0),
        pontuada("B", "Panela de Pressao", 40.0, "MLB1276", 129.0),
    ]

    assert ordenar_como_a_rodada(invertidas, [caro], TEMAS)[0] == "A"


def test_prioritario_com_cupom_ainda_manda_no_topo():
    """A ordem dos criterios: prioritario-com-cupom, depois qualquer-cupom."""
    ofertas = [
        pontuada("A", "Cafeteira Eletrica", 40.0, "MLB1276", 200.0),
        pontuada("B", "Tenis adidas Runfalcon", 10.0, "MLB1276", 129.0),
    ]
    geral = {"code": "VALEMAIS", "minimo": 1.0, "temas": [], "categorias": []}

    assert ordenar_como_a_rodada(ofertas, [geral], TEMAS)[0] == "B"


# ---------- no maximo dois, e o que rende mais ----------
#
# Medido em producao em 09/09/2026, na primeira rodada com o Pelando ligado:
# havia seis cupons gerais no ar, todos serviam para todo produto, e o post
# saia com os seis numa linha:
#
#   TORCIDA ou GLORIA ou PREDATA0809 ou SITETOD0809 ou TUDODEBOM ou VALEMAIS
#
# E o defeito que eu apontei no grupo concorrente, que lista tres e torce.
# Ninguem digita seis codigos: tenta o primeiro, falha, desiste.


def geral(code, desconto, tipo="PERCENT", teto=0.0, minimo=1.0):
    return {
        "code": code, "desconto": desconto, "tipo": tipo,
        "teto": teto, "minimo": minimo, "temas": [], "categorias": [],
    }


def test_no_maximo_dois_cupons_por_post():
    cupons = [geral("A", 10), geral("B", 12), geral("C", 15), geral("D", 20)]

    saida = cupom_para(oferta("Produto Qualquer", 200.0), cupons)

    assert saida.count(" ou ") == 1


def test_o_que_economiza_mais_vem_primeiro():
    """Dois, e nao um: cupom do ML tem limite de uso, e quando o primeiro
    estoura o segundo ainda pega."""
    cupons = [geral("FRACO", 5), geral("FORTE", 25)]

    assert cupom_para(oferta("Produto", 200.0), cupons) == "FORTE ou FRACO"


def test_o_teto_decide_e_nao_a_porcentagem():
    """25% com teto de R$ 500 vale mais que 30% com teto de R$ 20 em quase
    tudo que o grupo posta. Ordenar por porcentagem premiaria o pior."""
    cupons = [
        geral("TETOBAIXO", 30, teto=20.0),
        geral("TETOALTO", 25, teto=500.0),
    ]

    assert cupom_para(oferta("Produto", 200.0), cupons).startswith("TETOALTO")


def test_valor_fixo_compete_com_porcentagem():
    """R$ 60 fixo bate 10% de R$ 200. O minimo e obrigatorio no fixo desde
    09/09/2026 -- ver `_regra_incompleta`."""
    cupons = [geral("PCT", 10), geral("FIXO", 60, tipo="FIXED", minimo=50.0)]

    assert cupom_para(oferta("Produto", 200.0), cupons).startswith("FIXO")


def test_um_cupom_so_sai_sem_o_ou():
    assert cupom_para(oferta("Produto", 200.0), [geral("UNICO", 10)]) == "UNICO"


def test_a_escolha_respeita_o_preco_do_produto():
    """10% de R$ 1000 (R$ 100) bate R$ 60 fixo; em R$ 100 e o contrario."""
    cupons = [geral("PCT", 10), geral("FIXO", 60, tipo="FIXED", minimo=50.0)]

    assert cupom_para(oferta("Caro", 1000.0), cupons).startswith("PCT")
    assert cupom_para(oferta("Barato", 100.0), cupons).startswith("FIXO")


# ---------- o preco anunciado ja e o do cupom ----------
#
# Pedido em 09/09/2026: "quando coloque que e cupom, coloque o valor do
# produto ja com o desconto do cupom". O post saia assim --
#
#     De R$ 189,00 por R$ 117,35
#     Use o cupom: TORCIDA
#
# -- e o numero que interessa (R$ 105,61) ficava escondido numa conta que o
# leitor tinha que fazer.
#
# O numero fica ATRELADO ao cupom no texto, e essa amarra e o que o torna
# verdadeiro: cupom "em itens selecionados" as vezes nao aplica -- o TORCIDA
# passou num pre-treino e numa progressiva e falhou num whey. Como preco do
# anuncio seria falso; como preco COM o cupom, descreve a condicao que o
# proprio post manda cumprir.


def test_o_preco_anunciado_nao_leva_o_cupom():
    """Mudou em 09/09/2026, depois que os cinco codigos testados no carrinho
    morreram -- dois deles no mesmo dia em que funcionaram. Cupom do ML morre
    por CONSUMO, dentro da validade, e nenhuma fonte publica o contador. Um
    preco que depende do cupom fica errado na maior parte do tempo, e preco
    falso e o unico erro que gasta a confianca do grupo de vez."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Dux Human Energy Kick Caffeine 1000g", 117.35)
    torcida = geral("TORCIDA", 10, teto=30.0, minimo=79.0)

    assert preco_com_cupom(o, [torcida]) is None
    # O cupom continua sendo citado: ele e uma chance a mais, dita como chance.
    assert cupom_para(o, [torcida]) == "TORCIDA"


def test_o_teto_limita_o_desconto_do_preco(com_preco_de_cupom):
    """10% de R$ 500 sao R$ 50, mas o teto e R$ 30."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Produto Caro", 500.0)

    assert preco_com_cupom(o, [geral("T", 10, teto=30.0)]) == 470.0


def test_sem_cupom_nao_ha_preco_final():
    from promo.pipeline import preco_com_cupom

    assert preco_com_cupom(oferta("Produto", 100.0), []) is None


def test_cupom_abaixo_do_minimo_nao_muda_o_preco():
    from promo.pipeline import preco_com_cupom

    o = oferta("Barato", 50.0)

    assert preco_com_cupom(o, [geral("T", 10, minimo=79.0)]) is None


def test_o_preco_final_usa_o_cupom_que_rende_mais(com_preco_de_cupom):
    """O mesmo criterio da escolha do codigo."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Produto", 200.0)
    cupons = [geral("FRACO", 5), geral("FORTE", 25)]

    assert preco_com_cupom(o, cupons) == 150.0


def test_cupom_restrito_entra_no_preco_quando_o_codigo_diz_a_marca(
    com_preco_de_cupom,
):
    """Restrito nao e mais sinonimo de "vale para tudo".

    Este teste nasceu quando restrito servia para qualquer produto. O dono
    testou os codigos no carrinho e nenhum pegava -- TORCIDA inclusive --, e a
    medicao explicou: "Em itens Selecionados", sem o ML dizer quais. Hoje o
    recorte sai do codigo, e so quando ele carrega uma marca.
    """
    from promo.pipeline import preco_com_cupom

    o = oferta("Avene Cicalfate Creme Reparador 100g", 149.99)
    avene = dict(geral("AVENE15", 10, teto=30.0, minimo=79.0), restrito=True)

    assert preco_com_cupom(o, [avene]) == 134.99


def test_cupom_de_campanha_entra_no_preco_de_qualquer_produto(com_preco_de_cupom):
    """"TORCIDA" e campanha: nao recorta por marca, entao vale aqui tambem."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Kit Progressiva Titanium Liss", 149.99)
    torcida = dict(geral("TORCIDA", 10, teto=30.0, minimo=79.0), restrito=True)

    assert preco_com_cupom(o, [torcida]) == 134.99


def test_cupom_de_marca_nao_mexe_no_preco_de_outra_marca(com_preco_de_cupom):
    """Aqui o recorte vale: AVENE15 nao desconta um kit de progressiva."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Kit Progressiva Titanium Liss", 149.99)
    avene = dict(geral("AVENE15", 10, teto=30.0, minimo=79.0), restrito=True)

    assert preco_com_cupom(o, [avene]) is None


def test_amazon_nao_ganha_preco_de_cupom_do_ml():
    from promo.pipeline import preco_com_cupom

    o = oferta("Creatina 300g", 100.0, fonte="amazon")

    assert preco_com_cupom(o, [geral("T", 10)]) is None


# ---------- preco negativo nunca ----------
#
# Foi para o grupo em 09/09/2026:
#
#     Protetor solar em bastao antimanchas 90FPS Sallve
#     De R$ 100,90 por *R$ -180,11* com o cupom
#     Use o cupom: TUDODEBOM ou SITETOD0809
#
# Cinco posts assim, dois entregues. A causa esta na fonte: o slug do
# TUDODEBOM nao trazia a compra minima, entao ele entrou com `minimo: 0` e
# passou a servir para qualquer preco -- R$ 250 fixos num produto de R$ 69,89.


def fixo(code, valor, minimo=0.0):
    """Minimo 0 por padrao de proposito: e o formato que a fonte entrega
    incompleto, e o que os testes desta secao exercitam."""
    return {
        "code": code, "desconto": valor, "tipo": "FIXED",
        "teto": 0.0, "minimo": minimo, "temas": [], "categorias": [],
    }


def test_cupom_sem_minimo_nao_serve():
    """Campanha do ML tem minimo. Sem ele, o slug veio incompleto -- e o
    dono confirmou no carrinho que os sem minimo estao mortos."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Protetor Solar Sallve", 69.89)

    assert cupom_para(o, [fixo("TUDODEBOM", 250.0)]) is None
    assert preco_com_cupom(o, [fixo("TUDODEBOM", 250.0)]) is None


def test_cupom_fixo_com_minimo_serve(com_preco_de_cupom):
    from promo.pipeline import preco_com_cupom

    o = oferta("Produto Caro", 2500.0)

    assert preco_com_cupom(o, [fixo("MELIMAISALLSITE", 250.0, minimo=2099.0)]) == 2250.0


def test_percentual_sem_minimo_continua_valendo(com_preco_de_cupom):
    """10% de qualquer preco e proporcional ao produto; o problema era so do
    valor fixo."""
    from promo.pipeline import preco_com_cupom

    assert preco_com_cupom(oferta("Produto", 100.0), [geral("VALEMAIS", 10)]) == 90.0


def test_o_preco_final_nunca_e_negativo():
    """Guarda sobre o RESULTADO, e nao sobre a causa: qualquer cupom que
    desconte mais do que o produto custa esta descrito errado, venha o erro de
    onde vier."""
    from promo.pipeline import preco_com_cupom

    absurdo = fixo("X", 500.0, minimo=1.0)

    assert preco_com_cupom(oferta("Barato", 50.0), [absurdo]) is None


def test_o_preco_final_nunca_e_zero():
    from promo.pipeline import preco_com_cupom

    exato = fixo("X", 50.0, minimo=1.0)

    assert preco_com_cupom(oferta("Barato", 50.0), [exato]) is None


def test_cai_para_o_proximo_cupom_quando_o_melhor_estoura(com_preco_de_cupom):
    """Escolher so o melhor e desistir jogaria fora o segundo, que serve."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Produto", 100.0)
    cupons = [fixo("GRANDE", 500.0, minimo=1.0), geral("PEQUENO", 10)]

    assert preco_com_cupom(o, cupons) == 90.0


def test_o_caso_real_do_protetor_solar(com_preco_de_cupom):
    """Os cinco cupons que estavam no ar no dia, e o produto que quebrou."""
    from promo.pipeline import preco_com_cupom

    o = oferta("Protetor solar em bastao antimanchas 90FPS Sallve", 69.89)
    cupons = [
        fixo("TUDODEBOM", 250.0),
        fixo("SITETOD0809", 60.0),
        fixo("MELIMAISALLSITE", 250.0, minimo=2099.0),
        geral("TORCIDA", 10, teto=30.0, minimo=79.0),
        geral("VALEMAIS", 10),
    ]

    assert cupom_para(o, cupons) == "VALEMAIS"
    assert preco_com_cupom(o, cupons) == 62.9


def test_percentual_sem_minimo_tambem_e_recusado():
    """O VALEMAIS respondeu "O cupom esgotou" no checkout em 09/09/2026. Ele e
    percentual, entao nunca produziu preco absurdo -- 10% e proporcional ao
    produto --, mas estava morto igual aos fixos. O minimo previu os cinco
    casos testados; o tipo nao previu nenhum."""
    from promo.pipeline import preco_com_cupom

    valemais = {
        "code": "VALEMAIS", "desconto": 10.0, "tipo": "PERCENT",
        "teto": 0.0, "minimo": 0.0, "temas": [], "categorias": [],
    }
    o = oferta("Relogio Casio Vintage", 382.57)

    assert cupom_para(o, [valemais]) is None
    assert preco_com_cupom(o, [valemais]) is None


def test_o_caso_real_do_relogio_casio(com_preco_de_cupom):
    """Os cupons no ar em 09/09/2026, e o post que citou VALEMAIS esgotado."""
    from promo.pipeline import preco_com_cupom

    cupons = [
        geral("OFERTASEMPRE", 18, teto=50.0, minimo=79.0),
        geral("TORCIDA", 10, teto=30.0, minimo=79.0),
        geral("GLORIA", 10, teto=30.0, minimo=79.0),
        geral("VALEMAIS", 10, minimo=0.0),
        fixo("TUDODEBOM", 250.0),
    ]
    o = oferta("Relogio Casio Vintage Digital B640wc-5adf Rose", 382.57)

    assert cupom_para(o, cupons) == "OFERTASEMPRE ou TORCIDA"
    assert preco_com_cupom(o, cupons) == 332.57


def test_produto_abaixo_de_todos_os_minimos_nao_leva_cupom():
    """Com todo cupom exigindo minimo, produto barato fica sem -- e e o certo:
    o cupom nao aplicaria mesmo."""
    cupons = [geral("TORCIDA", 10, teto=30.0, minimo=79.0)]

    assert cupom_para(oferta("Meia Kit 3 Pares", 19.90), cupons) is None


def test_em_producao_o_preco_nao_leva_o_cupom():
    """A chave fica desligada. Liga-la de novo exige medir antes se cupom do
    ML voltou a ser confiavel -- em 09/09/2026 cinco de cinco testados
    responderam "esgotou" no carrinho."""
    from promo.pipeline import PRECO_LEVA_O_CUPOM

    assert PRECO_LEVA_O_CUPOM is False


# ---------- cupom restrito sem recorte publicado ----------
#
# Medido em 09/09/2026: os nove cupons no ar tinham `restrito: True`, nenhum
# tema e nenhuma categoria. "Em itens Selecionados", sem dizer quais. O
# `_serve` tratava ausencia de tema como "vale para tudo", e 300 de 300 posts
# recentes sairam com cupom -- o grupo recebeu "Aparador de Pelos Kemei, use o
# cupom AVENE15 ou MANTECORP14".


def oferta_ml(titulo: str, preco: float = 100.0):
    from promo.models import Offer

    return Offer(
        source="mercadolivre", external_id="MLB1", title=titulo, price=preco,
        url="https://mercadolivre.com.br/MLB1",
    )


def cupom_restrito(code: str, minimo: float = 5.0) -> dict:
    return {
        "code": code, "minimo": minimo, "desconto": 15.0, "tipo": "PERCENT",
        "teto": 150.0, "categorias": [], "temas": [], "restrito": True,
        "ate": "2030-01-01",
    }


def test_o_codigo_da_marca_vira_o_recorte_do_cupom():
    """O ML nao publica quais itens sao "selecionados", mas o codigo entrega a
    marca. AVENE15 passa a valer so no que cita Avene."""
    from promo.pipeline import cupom_para

    cupons = [cupom_restrito("AVENE15")]

    assert cupom_para(oferta_ml("Avene Hydrance Creme Hidratante 40g"), cupons) == "AVENE15"
    assert cupom_para(oferta_ml("Aparador de Pelos Kemei KM-6511"), cupons) is None


def test_codigo_de_campanha_vale_para_qualquer_produto():
    """Campanha do ML nao recorta por marca, e e o cupom que de fato pega.

    A primeira versao desta regra recusava tudo que nao reconhecia como marca.
    Medido em 10/09/2026 sobre os 300 posts mais recentes: os dezesseis codigos
    no ar eram TODOS campanha, e o resultado foi 0% dos posts com cupom. O
    grupo vizinho publicou `EXCLUSIVONOMELI` numa camiseta Lupo no mesmo dia, e
    funcionou -- era justamente esse cupom que estava sendo jogado fora.
    """
    from promo.pipeline import cupom_para

    for code in ("TUDOMELI", "EXCLUSIVONOMELI", "ITENSDECASA", "SAINDOBARRATO"):
        assert (
            cupom_para(oferta_ml("Qualquer Produto 500g"), [cupom_restrito(code)])
            == code
        )


def test_cupom_de_marca_nao_sai_em_produto_de_outra_marca():
    """O caso inverso, que originou a regra: o recorte da marca vale."""
    from promo.pipeline import cupom_para

    for code in ("AVENE15", "MANTECORP14", "MAYBELLINENOMELI"):
        assert (
            cupom_para(oferta_ml("Aparador de Pelos Kemei KM-6511"), [cupom_restrito(code)])
            is None
        )


def test_cupom_nao_restrito_continua_valendo_para_tudo():
    """A regra e so para `restrito`. Cupom geral do ML nao muda."""
    from promo.pipeline import cupom_para

    geral = cupom_restrito("9DO9MEIANOITE", minimo=219.0)
    geral["restrito"] = False

    assert (
        cupom_para(oferta_ml("Tenis Feminino Questar 3 adidas", 260.0), [geral])
        == "9DO9MEIANOITE"
    )


def test_o_sufixo_do_ml_sai_antes_da_marca():
    """"NOMELI" precisa sair antes de "MELI", senao MAYBELLINENOMELI viraria
    "maybellineno" e nao casaria com titulo nenhum."""
    from promo.pipeline import _marca_do_codigo

    assert _marca_do_codigo("MAYBELLINENOMELI") == "maybelline"
    assert _marca_do_codigo("AVENE15") == "avene"
    assert _marca_do_codigo("MANTECORP14") == "mantecorp"


def test_so_marca_conhecida_recorta():
    """Lista branca, e nao negra: os codigos de campanha mudam toda semana.

    Em 09/09/2026 estavam no ar TUDOMELI, OFERTAHOJE e SOHOJE; em 10/09 nenhum
    sobrevivia, e os dezesseis novos eram outros. Enumerar o generico persegue
    alvo movel; enumerar marca funciona porque marca nao se inventa.
    """
    from promo.pipeline import _marca_do_codigo

    assert _marca_do_codigo("9DO9SPORTS") == ""
    assert _marca_do_codigo("EXCLUSIVONOMELI") == ""
    assert _marca_do_codigo("MELIACHAPROMO") == ""
    assert _marca_do_codigo("LUPONOMELI") == "lupo"
    assert _marca_do_codigo("NIKENOMELI") == "nike"


def test_o_tema_declarado_vence_o_codigo():
    """Quando o cupom TEM tema, ele manda -- o codigo e so o ultimo recurso."""
    from promo.pipeline import cupom_para

    cupom = cupom_restrito("AVENE15")
    cupom["temas"] = ["shampoo"]

    assert cupom_para(oferta_ml("Shampoo Wella Invigo 1L"), [cupom]) == "AVENE15"
    assert cupom_para(oferta_ml("Avene Creme Hidratante"), [cupom]) is None

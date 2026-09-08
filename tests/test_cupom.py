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
import sys
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
    cupons = [{"code": "VALEMAIS", "temas": [], "minimo": 0.0}]

    assert cupom_para(oferta("Secador de Cabelos Taiff", 133.0), cupons) == "VALEMAIS"


def test_o_tema_ignora_acento_e_caixa():
    cupons = [{"code": "X", "temas": ["oleo capilar"], "minimo": 0.0}]

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
        {"code": "OFERTASEMPRE", "temas": [], "minimo": 0.0},
        {"code": "VALEMAIS", "temas": [], "minimo": 0.0},
    ]

    assert cupom_para(oferta("Terno Italiano Slim", 123.0), cupons) == (
        "OFERTASEMPRE ou VALEMAIS"
    )


def test_a_alternativa_que_nao_serve_fica_de_fora():
    """A diferenca para o concorrente: eles listam tres e torcem, e por isso
    precisam avisar 'se der erro, tente pela aba anonima'."""
    cupons = [
        {"code": "OFERTASEMPRE", "temas": [], "minimo": 0.0},
        {"code": "PAGUEMENOS", "temas": ["cueca"], "minimo": 0.0},
    ]

    assert cupom_para(oferta("Secador Taiff", 133.0), cupons) == "OFERTASEMPRE"


# ---------- a loja de origem ----------


def test_cupom_do_ml_nunca_sai_em_oferta_da_amazon():
    """Sao cupons de campanha do ML. O checkout da Amazon nao aceita, e o post
    mandaria a pessoa tentar um codigo que nunca vai funcionar."""
    cupons = [{"code": "VALEMAIS", "temas": [], "minimo": 0.0}]

    assert cupom_para(oferta("Creatina 300g", 40.85, fonte="amazon"), cupons) is None


def test_a_mesma_oferta_no_ml_leva_o_cupom():
    cupons = [{"code": "VALEMAIS", "temas": [], "minimo": 0.0}]

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
    manual = {"code": "MANUAL", "minimo": 0.0, "categorias": [], "temas": []}
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
    """Reproduz a chave de ordenacao de `run`."""
    from promo.pipeline import cupom_para, e_prioritaria

    def ordem(s):
        premiada = bool(cupom_para(s.offer, cupons)) and e_prioritaria(s.offer, temas)
        return (premiada, s.discount_pct, s.offer.free_shipping)

    return [s.offer.external_id for s in sorted(ofertas, key=ordem, reverse=True)]


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


def test_cupom_sem_tema_prioritario_nao_ganha_o_topo():
    """Nem o cupom sozinho."""
    ofertas = [
        pontuada("A", "Cafeteira Eletrica", 40.0, "MLB1276", 200.0),
        pontuada("B", "Camisa Social Lisa", 20.0, "MLB1430", 129.0),
    ]

    assert ordenar_como_a_rodada(ofertas, [CUPOM], TEMAS)[0] == "A"


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

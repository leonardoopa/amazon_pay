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
    caminho = escrever(tmp_path, {"codes": [{"code": "VALEMAIS", "ate": AMANHA}]})

    assert [c["code"] for c in load_coupons(caminho)] == ["VALEMAIS"]


def test_cupom_vencido_fica_de_fora(tmp_path):
    """Vencido e pior que ausente: a pessoa clica, tenta, falha."""
    caminho = escrever(tmp_path, {"codes": [{"code": "VELHO", "ate": ONTEM}]})

    assert load_coupons(caminho) == []


def test_cupom_sem_validade_entra(tmp_path):
    caminho = escrever(tmp_path, {"codes": [{"code": "SEMPRE"}]})

    assert [c["code"] for c in load_coupons(caminho)] == ["SEMPRE"]


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
    caminho = escrever(tmp_path, {"code": "VALEMAIS", "ate": AMANHA})

    assert [c["code"] for c in load_coupons(caminho)] == ["VALEMAIS"]


def test_formato_antigo_vencido_tambem_sai(tmp_path):
    caminho = escrever(tmp_path, {"code": "VELHO", "ate": ONTEM})

    assert load_coupons(caminho) == []


def test_load_coupon_devolve_o_primeiro(tmp_path):
    caminho = escrever(
        tmp_path, {"codes": [{"code": "UM", "ate": AMANHA}, {"code": "DOIS"}]}
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

"""O bot le os cupons de campanha sozinho, do painel do ML.

Sondado em 08/09/2026, nesta ordem:

    17 rotas de cupom da API OAuth      404 "resource not found"
    /cupons  sem cookie                 403, 2.586 bytes, nenhum cupom
    /cupons  com o cookie do painel     200, 952 KB, 42 cupons
    /cupons/active  com o cookie        200, 13 cupons, 1 com codigo digitavel
    /cupons/filter?most_used=true       200 x3 paginas, 92 distintos, o mesmo 1

A pagina `/cupons` lista os cupons ATRIBUIDOS a conta -- os `code` dela sao
tokens de 88 caracteres, identificadores de ativacao pessoal. Publicar um
seria mandar o grupo digitar algo que so vale para a conta do dono. Por isso o
corte por tamanho.

O que sobra e o que interessa: o OFERTASEMPRE, um dos codigos que o grupo
concorrente postou, veio com regra completa -- 18%, minimo R$ 79, teto R$ 50,
validade 09/09 e vinte IDs de categoria do ML.

A regra vir como ID (MLB1430) e o que torna isso melhor que qualquer
heuristica: e o mesmo vocabulario de `products.category`, entao o casamento e
o oficial da campanha, nao um chute pelo titulo.
"""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.ml_cupons import (  # noqa: E402
    MAX_CODIGO,
    Cupom,
    _cupons_do_html,
    normalizar,
)

ONTEM = (date.today() - timedelta(days=1)).isoformat()
DEPOIS = (date.today() + timedelta(days=20)).isoformat()


def cru(**extra) -> dict:
    """Um cupom no formato exato que a pagina devolve."""
    base = {
        "campaign_id": "14167118",
        "code": "OFERTASEMPRE",
        "title": "18% OFF com OFERTASEMPRE",
        "min_amount": 79,
        "cap_amount": 50,
        "currency": "BRL",
        "status_id": "ACTIVE",
        "expiration_date": f"{DEPOIS}T02:59:00Z",
        "created_by": "meli",
        "discount_type": "PERCENT",
        "discount_value": 18,
        "segmentations": {"categories": ["MLB1430", "MLB107292"], "containers": []},
        "item_ids": ["MLB1010954920"],
    }
    base.update(extra)
    return base


# ---------- achar os cupons no HTML ----------


def test_acha_o_cupom_solto_no_html():
    """O estado nao vem num `window.__X__`: cada cupom aparece solto. Decodificar
    a partir da chave de abertura sobrevive a mudanca de layout."""
    html = "<div>lixo</div><script>var x = [%s];</script>" % json.dumps(cru())

    assert len(_cupons_do_html(html)) == 1


def test_nao_repete_a_mesma_campanha():
    """A pagina repete o mesmo cupom em varias secoes -- o OFERTASEMPRE apareceu
    duas vezes na captura real."""
    corpo = json.dumps(cru())
    html = corpo + " ... " + corpo

    assert len(_cupons_do_html(html)) == 1


def test_json_quebrado_no_meio_nao_derruba_o_resto():
    html = '{"campaign_id": QUEBRADO' + json.dumps(cru())

    assert len(_cupons_do_html(html)) == 1


def test_html_sem_cupom_devolve_vazio():
    assert _cupons_do_html("<html><body>nada aqui</body></html>") == []


# ---------- o que e publicavel ----------


def test_o_cupom_com_codigo_curto_passa():
    cupons = normalizar([cru()])

    assert len(cupons) == 1
    assert cupons[0].code == "OFERTASEMPRE"
    assert cupons[0].minimo == 79
    assert cupons[0].teto == 50
    assert cupons[0].categorias == ["MLB1430", "MLB107292"]


def test_token_de_ativacao_de_88_caracteres_nao_passa():
    """Sao os `code` da pagina /cupons: identificador da conta do dono, nao
    codigo digitavel. Publicar um seria mandar o grupo tentar o impossivel."""
    token = "zPMzdfVP4ZOtJV44zbDhYall0wIMp_TqeVNZkKOhc-fvD9S_ORLj9QiPOWVeP1ZKl_Q5N9XvK4i919arof_Wig=="
    assert len(token) > MAX_CODIGO

    assert normalizar([cru(code=token)]) == []


def test_cupom_sem_codigo_nao_passa():
    """12 dos 13 ativos medidos eram assim: aplicam sozinhos, sem nada a digitar."""
    assert normalizar([cru(code="")]) == []


def test_cupom_vencido_nao_passa():
    assert normalizar([cru(expiration_date=f"{ONTEM}T02:59:00Z")]) == []


def test_cupom_sem_data_nao_passa():
    assert normalizar([cru(expiration_date="")]) == []


def test_data_ilegivel_nao_derruba_a_leitura():
    """Formato novo de data nao pode virar rodada sem post."""
    assert normalizar([cru(expiration_date="ontem de tarde"), cru()]) != []


# ---------- normalizacao ----------


def test_a_data_vira_iso_simples():
    """O ML manda com fuso Z; o post nao tem precisao de hora."""
    cupom = normalizar([cru(expiration_date=f"{DEPOIS}T02:59:00Z")])[0]

    assert cupom.ate == DEPOIS


def test_cupom_de_valor_fixo_e_lido():
    cupom = normalizar([cru(discount_type="FIXED", discount_value=30)])[0]

    assert (cupom.tipo, cupom.desconto) == ("FIXED", 30.0)


def test_cupom_sem_categoria_fica_com_lista_vazia():
    cupom = normalizar([cru(segmentations={"categories": [], "containers": []})])[0]

    assert cupom.categorias == []


def test_o_dict_sai_no_formato_do_watchlist():
    """`cupons_vigentes` mistura painel e watchlist numa lista so -- os dois
    lados precisam falar o mesmo dialeto."""
    d = normalizar([cru()])[0].como_dict()

    assert set(d) >= {"code", "ate", "minimo", "categorias"}
    assert isinstance(d["categorias"], list)


def test_lista_vazia_nao_estoura():
    assert normalizar([]) == []


# ---------- categoria vira tema de titulo ----------
#
# A vitrine e 89% do que o grupo recebe e nao devolve categoria em campo
# nenhum. As duas vias de conserto estao fechadas: /items/{id} responde 403
# (8 de 8 testados) e a pagina do produto vem bloqueada (67 KB uniformes, sem
# breadcrumb). Sem uma segunda via, o cupom alcancaria 34 posts em 2.964.
#
# Os temas nao sao chute: saem do NOME que o proprio ML da a cada categoria da
# campanha. As 20 do OFERTASEMPRE, lidas em 08/09/2026, sao todas roupa.


def test_categoria_simples_vira_tema():
    from promo.sources.ml_cupons import temas_de_categoria

    assert temas_de_categoria("Calcados, Roupas e Bolsas > Camisas") == ["camisa"]


def test_categoria_composta_vira_dois_temas():
    from promo.sources.ml_cupons import temas_de_categoria

    assert temas_de_categoria("Calçados, Roupas e Bolsas > Camisetas e Regatas") == [
        "camiseta",
        "regata",
    ]


def test_so_a_folha_do_caminho_conta():
    """O pai e generico demais: "Calcados, Roupas e Bolsas" casaria com meio
    mundo, inclusive com bolsa, que esta na lista de exclusao."""
    from promo.sources.ml_cupons import temas_de_categoria

    temas = temas_de_categoria("Calçados, Roupas e Bolsas > Leggings")

    assert temas == ["legging"]
    assert "bolsa" not in temas


def test_conectivo_nao_vira_tema():
    """ "Bermudas e Shorts" com o "e" dentro casaria com qualquer titulo."""
    from promo.sources.ml_cupons import temas_de_categoria

    assert "e" not in temas_de_categoria("Roupas > Bermudas e Shorts")


def test_plural_em_oes_singulariza():
    from promo.sources.ml_cupons import temas_de_categoria

    assert temas_de_categoria("Roupas > Macacões") == ["macacao"]


def test_acento_some_do_tema():
    from promo.sources.ml_cupons import temas_de_categoria

    assert temas_de_categoria("Roupas > Calças") == ["calca"]


def test_palavra_generica_de_vestuario_nao_vira_tema():
    """ "Moda Fitness" nao pode virar o tema "moda", que casa com tudo."""
    from promo.sources.ml_cupons import temas_de_categoria

    assert temas_de_categoria("Calçados > Moda Fitness") == ["fitness"]


def test_categoria_desconhecida_nao_da_tema():
    from promo.sources.ml_cupons import temas_de_categoria

    assert temas_de_categoria("") == []


def test_enriquecer_junta_os_temas_de_todas_as_categorias(monkeypatch):
    from promo.sources import ml_cupons

    monkeypatch.setattr(
        ml_cupons,
        "nomes_das_categorias",
        lambda ids, **kw: {
            "MLB31447": "Calçados, Roupas e Bolsas > Camisetas e Regatas",
            "MLB278018": "Calçados, Roupas e Bolsas > Leggings",
        },
    )
    cupom = normalizar([cru(segmentations={"categories": ["MLB31447", "MLB278018"]})])

    enriquecido = ml_cupons.enriquecer_com_temas(cupom)[0]

    assert enriquecido["temas"] == ["camiseta", "regata", "legging"]
    assert enriquecido["categorias"] == ["MLB31447", "MLB278018"]


def test_api_de_categoria_fora_do_ar_nao_derruba_o_cupom(monkeypatch):
    """Sem os nomes, o cupom continua valendo pelo ID -- que e o caminho
    preciso. O tema e alcance a mais, nao a regra."""
    from promo.sources import ml_cupons

    monkeypatch.setattr(ml_cupons, "nomes_das_categorias", lambda ids, **kw: {})
    cupom = normalizar([cru()])

    enriquecido = ml_cupons.enriquecer_com_temas(cupom)[0]

    assert enriquecido["temas"] == []
    assert enriquecido["code"] == "OFERTASEMPRE"

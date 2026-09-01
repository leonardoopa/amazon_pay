"""O watchlist.json de verdade, nao um de fixture.

Este arquivo e configuracao editada a mao e o deploy o sobrescreve no servidor
(`rsync --delete` nao o exclui), entao um erro aqui vira producao sem passar
por revisao de codigo. Os defeitos possiveis sao silenciosos: um ID de
categoria errado vira 404 e um WARNING no meio da rodada, um termo duplicado
gasta 20 chamadas de API por descoberta pra devolver o que ja veio.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.pipeline import load_categories, load_watchlist  # noqa: E402

ARQUIVO = Path(__file__).resolve().parents[1] / "watchlist.json"
DADOS = json.loads(ARQUIVO.read_text(encoding="utf-8"))


# ---------- forma ----------


def test_o_arquivo_carrega_pelas_duas_vias():
    """Se `load_watchlist` ou `load_categories` recusar, a rodada fica cega."""
    assert load_watchlist(ARQUIVO)
    assert load_categories(ARQUIVO)


def test_nenhum_termo_repetido():
    termos = [k["term"] for k in DADOS["keywords"]]
    repetidos = [t for t, n in Counter(termos).items() if n > 1]

    assert repetidos == []


def test_nenhuma_categoria_repetida():
    ids = [c["id"] for c in DADOS["categories"]]
    repetidos = [i for i, n in Counter(ids).items() if n > 1]

    assert repetidos == []


def test_ids_de_categoria_tem_a_forma_do_ml():
    """MLB seguido de digitos. Qualquer outra coisa e 404 na descoberta."""
    fora = [c["id"] for c in DADOS["categories"] if not re.fullmatch(r"MLB\d+", c["id"])]

    assert fora == []


def test_todo_teto_de_preco_e_positivo():
    """`max_price` zero ou negativo descarta a categoria inteira em silencio."""
    ruins = [
        w.term
        for w in load_watchlist(ARQUIVO)
        if w.max_price is not None and w.max_price <= 0
    ]
    ruins += [
        c.id
        for c in load_categories(ARQUIVO)
        if c.max_price is not None and c.max_price <= 0
    ]

    assert ruins == []


def test_termos_sao_minusculos_e_sem_acento():
    """A busca do ML nao normaliza: "Máscara" e "mascara" trazem listas
    diferentes, e o resto do arquivo ja adotou a forma sem acento."""
    fora = [
        k["term"]
        for k in DADOS["keywords"]
        if k["term"] != k["term"].lower() or not k["term"].isascii()
    ]

    assert fora == []


# ---------- cobertura que o grupo pediu ----------
#
# As mulheres que entraram no grupo pediram cabelo, pele e suplemento. Estes
# casos existem porque a cobertura veio de uma medicao contra a API em
# 30/08/2026, e um `git revert` distraido a desfaz sem quebrar nada visivel.


def termos() -> set[str]:
    return {k["term"] for k in DADOS["keywords"]}


def test_marcas_de_cabelo_pedidas_estao_cobertas():
    assert {t for t in termos() if "wella" in t}
    assert {t for t in termos() if "truss" in t}


def test_pele_e_protetor_solar_estao_cobertos():
    assert {t for t in termos() if "cerave" in t}
    assert "cicaplast baume" in termos()
    assert {t for t in termos() if t.startswith("protetor solar")}


def test_melatonina_esta_coberta():
    assert {t for t in termos() if "melatonina" in t}


def test_as_subcategorias_de_beleza_e_saude_estao_na_lista():
    """As categorias-pai (MLB1246, MLB264586) so entregam 10 mais vendidos
    cada, e produto de cabelo ou de pele nunca subia ate la. O ranking util e
    o das filhas."""
    ids = {c["id"] for c in DADOS["categories"]}

    assert {"MLB199407", "MLB1263", "MLB264201"} <= ids


def test_nao_entrou_termo_que_a_busca_nao_responde():
    """Medidos em 30/08/2026: `/products/search` devolveu ZERO produto de
    catalogo para cada um destes. Termo generico demais nao casa no catalogo
    -- so custa uma chamada por descoberta pra voltar vazio."""
    mortos = {
        "condicionador",
        "hidratante facial",
        "serum facial",
        "acido hialuronico",
        "vitamina c facial",
        "la roche posay",
        "cicaplast",
        "melatonina 10mg",
        "mascara de hidratacao capilar",
    }

    assert termos() & mortos == set()


def test_nao_entrou_termo_de_roupa_de_marca():
    """Medidos em 31/08/2026: `/products/search` devolveu ZERO para TODA
    variante de Nike e adidas -- "tenis nike", "nike air max", "nike
    revolution", "bermuda adidas", "adidas runfalcon", entre outras.

    Nao e termo mal escolhido, e limite da via: roupa e calcado de marca no ML
    sao anuncio de vendedor, nao produto de catalogo, e `/products/{id}/items`
    nao responde por eles. As subcategorias de roupa confirmam pelo outro lado
    -- Camisas, Bermudas, Blusas, Camisetas e Leggings tem 20 destaques cada e
    ZERO de catalogo.

    Essas marcas chegam ao grupo pelo `priority`, que casa no TITULO e vale
    para a vitrine tambem. Termo de busca so gastaria chamada por rodada para
    voltar vazio.
    """
    mortos = {
        "short nike",
        "tenis nike",
        "camiseta nike",
        "camisa nike",
        "bermuda nike",
        "nike air max",
        "tenis adidas",
        "camisa adidas",
        "bermuda adidas",
        "camisa hering",
        "camiseta hering",
        "regata hering",
        "vivara",
        "relogio vivara",
    }

    assert termos() & mortos == set()


def test_celular_saiu_das_categorias():
    """58 dos 629 posts enviados eram celular (9%), e o top de mais vendidos
    de MLB1051 e literalmente Celulares e Smartphones. Convertem pior que o
    resto -- ticket alto e decisao longa."""
    ids = {c["id"] for c in DADOS["categories"]}

    assert "MLB1051" not in ids

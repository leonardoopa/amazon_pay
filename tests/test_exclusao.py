"""A lista de exclusao: o oposto de `priority`, com escape por marca.

O grupo recebeu dez bolsas em oitenta posts. Nao era defeito de ranking --
mochila e barata e tem desconto alto, entao ganha vaga por merito toda rodada.
Nao existia como dizer "esse tipo de produto nao, mesmo ganhando".

O pedido veio com a excecao junto: mochila da Nike, da adidas e da The North
Face pode; mochila de viagem, mala de viagem e mochila de menina nao. Por isso
a barra nao e absoluta -- titulo que tambem casa em `priority` passa.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer  # noqa: E402
from promo.pipeline import e_barrada, load_exclude, load_priority  # noqa: E402

BARRADOS = ["mochila", "bolsa", "mala de viagem"]
TEMAS = ["nike", "adidas", "the north face"]


def oferta(titulo: str) -> Offer:
    return Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title=titulo,
        price=99.0,
        url="https://mercadolivre.com.br/p/MLB1",
    )


# ---------- barra ----------


def test_mochila_generica_e_barrada():
    assert e_barrada(oferta("Mochila De Viagem 50L Impermeável"), BARRADOS, TEMAS)


def test_mala_de_viagem_e_barrada():
    assert e_barrada(oferta("Mala De Viagem Grande Rodinha 360"), BARRADOS, TEMAS)


def test_mochila_de_menina_e_barrada():
    assert e_barrada(oferta("Mochila Infantil Menina Escolar Unicórnio"), BARRADOS, TEMAS)


def test_acento_e_caixa_nao_escapam_da_barra():
    """O vendedor digita o titulo; a lista e escrita a mao no watchlist.json."""
    assert e_barrada(oferta("BOLSA TÉRMICA GRANDE"), ["bolsa"], TEMAS)


# ---------- escape por marca ----------


def test_mochila_da_marca_passa():
    """A excecao e o pedido inteiro: barrar tudo apagaria o que se quer manter."""
    assert not e_barrada(oferta("Mochila Essentials adidas Preta 24L"), BARRADOS, TEMAS)
    assert not e_barrada(oferta("Mochila Nike Brasilia 9.5 Treino"), BARRADOS, TEMAS)
    assert not e_barrada(
        oferta("Mochila The North Face Borealis 28L"), BARRADOS, TEMAS
    )


def test_a_marca_tem_precedencia_sobre_a_barra():
    """Ordem invertida faria "Mochila adidas" sumir junto com "Mochila de
    Viagem" -- a exclusao e sobre o TIPO de produto, nao sobre a peca."""
    assert not e_barrada(oferta("Mochila De Viagem adidas 40L"), BARRADOS, TEMAS)


# ---------- o que nao e barrado ----------


def test_produto_fora_da_lista_passa():
    assert not e_barrada(oferta("Shampoo Wella Blondorplex 250ml"), BARRADOS, TEMAS)


def test_lista_vazia_nao_barra_nada():
    assert not e_barrada(oferta("Mochila De Viagem 50L"), [], TEMAS)


def test_sem_temas_a_barra_vale_cheia():
    """Sem `priority` configurado nao ha escape, e a barra e absoluta."""
    assert e_barrada(oferta("Mochila Essentials adidas"), BARRADOS, [])


# ---------- o watchlist.json de verdade ----------


def test_o_arquivo_barra_bolsa_e_mochila():
    barrados = load_exclude()

    assert "mochila" in barrados
    assert "bolsa" in barrados


def test_as_marcas_de_mochila_pedidas_escapam():
    """Nike, adidas e The North Face foram nomeadas no pedido. Sem elas em
    `priority`, a barra levaria as tres junto."""
    temas = set(load_priority())

    assert {"nike", "adidas"} <= temas
    assert {"the north face", "north face"} & temas


def test_a_lista_de_exclusao_ja_vem_normalizada():
    assert all(t == t.lower() and t.isascii() for t in load_exclude())


def test_exclude_ausente_e_o_normal(tmp_path):
    """Campo opcional: watchlist antiga nao pode quebrar."""
    caminho = tmp_path / "w.json"
    caminho.write_text(json.dumps({"keywords": [{"term": "x"}]}), encoding="utf-8")

    assert load_exclude(caminho) == []


def test_entrada_vazia_e_ignorada(tmp_path):
    caminho = tmp_path / "w.json"
    caminho.write_text(
        json.dumps({"keywords": [], "exclude": ["mochila", "", "  "]}), encoding="utf-8"
    )

    assert load_exclude(caminho) == ["mochila"]


def test_nada_barrado_esta_tambem_em_priority():
    """Termo nos dois lugares nunca barra nada -- o escape sempre dispara, e a
    entrada da lista de exclusao vira letra morta que engana quem le."""
    inuteis = set(load_exclude()) & set(load_priority())

    assert inuteis == set()


# ---------- celular sai do grupo ----------


def test_o_telefone_esta_barrado():
    """Pedido em 06/09/2026. Medido sobre 2.289 posts enviados: 92 eram
    telefone (4,0%), e esta lista pega os 92 com 2 falsos positivos.

    `iphone` ficou de fora de proposito. Ele nao pegava nenhum telefone --
    iPhone passa dos tetos de preco e nunca entrou -- e pegava 11 acessorios
    ("Carregador Para iPhone", "Fone Compativel Com Iphone") mais um gloss:
    "Liphoney" contem "iphone" como substring.
    """
    barrados = load_exclude()

    for termo in ("smartphone", "galaxy a", "moto g", "poco x", "redmi note"):
        assert termo in barrados
    assert "iphone" not in barrados
    assert "celular" not in barrados  # pega tripe, suporte e fone


def test_o_termo_de_busca_de_celular_saiu_da_watchlist():
    """Barrar na selecao sem tirar da busca gastaria chamada de API em toda
    rodada para colher o que sera descartado depois."""
    from promo.pipeline import load_watchlist

    assert "smartphone" not in {w.term for w in load_watchlist()}


def test_os_telefones_reais_sao_barrados():
    barrados, temas = load_exclude(), load_priority()
    titulos = [
        "Smartphone Samsung Galaxy A17 5g 128gb 4gb Super Amoled 6.7''",
        "Smartphone Motorola Moto g56 5G 256GB - 8GB RAM+16GB Ram Boost",
        "Xiaomi Poco X7 Pro 5g 12ram /512 Gb Global Cor Preto",
        "Celular Samsung Galaxy A56 5g 256gb 8gb Ram Cinza",
        "Smartphone Motorola Edge 70 5g - 512gb 24gb",
    ]

    for titulo in titulos:
        assert e_barrada(oferta(titulo), barrados, temas), titulo


def test_o_gloss_nao_e_confundido_com_telefone():
    """"Liphoney" contem "iphone". Foi por isso que `iphone` ficou fora."""
    gloss = oferta("Gloss Fran By Franciny Ehlke Liphoney Mel Liphoney-mel")

    assert not e_barrada(gloss, load_exclude(), load_priority())

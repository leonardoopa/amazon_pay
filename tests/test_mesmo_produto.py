"""Mesmo nome e mesma marca e repeticao. Marca diferente e outra oferta.

A regra do dono, dita em 12/09/2026: "pode ser enviado whey varias vezes,
portanto que sejam de marcas diferentes; faca o comparativo por nome do
produto e nao por categoria; se o nome e a marca forem as mesmas, voce nao
envia, so envia no outro dia".

Ela substitui duas assinaturas que resumiam o titulo antes de comparar -- a de
tres palavras e a de duas -- e erravam nos dois sentidos:

  - juntavam marcas diferentes que comecam igual. "Whey Protein Concentrado
    1Kg Sabor Mocaccino Sx" e "Whey Protein Concentrado 100% Puro Optimum
    Nutrition" viravam a mesma familia, e uma bloqueava a outra.
  - separavam a mesma marca quando o titulo comecava diferente. "Suplemento em
    po Growth Supplements Whey" e "100% Whey Concentrado Growth Supplements"
    viravam familias distintas, e o mesmo whey saia duas vezes.

`mesmo_produto` compara os titulos inteiros. Sao dois caminhos, porque nenhum
dos dois sozinho acerta todos os casos reais -- ver o docstring da funcao.

Todos os pares abaixo sairam no grupo de verdade.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.db import mesmo_produto  # noqa: E402


# ---------- marcas diferentes passam ----------


def test_whey_de_marcas_diferentes_sao_ofertas_diferentes():
    assert not mesmo_produto(
        "Whey Protein Concentrado 1Kg Sabor Mocaccino Sx",
        "Whey Protein Concentrado 100% Puro - On / Optimum Nutrition",
    )


def test_dux_e_growth_nao_se_bloqueiam():
    assert not mesmo_produto(
        "Suplemento em pó Dux Nutrition Whey Protein Concentrado",
        "Suplemento em pó Growth Supplements Whey Protein Concentrado",
    )


def test_black_skull_e_growth_nao_se_bloqueiam():
    assert not mesmo_produto(
        "Suplemento em pó Black Skull Whey 100% HD sabor Caramelo",
        "Suplemento em pó Growth Supplements Whey Protein",
    )


def test_vitafor_e_dux_nao_se_bloqueiam():
    assert not mesmo_produto(
        "Whey Fort 3w Neutro 900g Vitafor",
        "Fresh Whey - DUX Nutrition Pote 450g",
    )


def test_tenis_de_marcas_diferentes_passam():
    assert not mesmo_produto(
        "Tênis New Balance Original 480 Low Masculino",
        "Tênis Kappa Sparta Unissex Original Confortável",
    )


def test_perfumes_diferentes_passam():
    """Pedido de 09/09/2026, que continua valendo: quatro perfumes de uma vez
    pode, desde que sejam perfumes diferentes."""
    assert not mesmo_produto(
        "Perfume Lattafa Asad 100ml Eau De Parfum",
        "Perfume Carolina Herrera 212 Vip Men 100ml",
    )


def test_televisores_de_marcas_diferentes_passam():
    assert not mesmo_produto(
        "Smart Tv Philips 55 4k 55pug7019 Google Tv Comando De Voz",
        "Smart TV PRO LG 55'' 4K Ultra HD AI 55UN85C",
    )


def test_aspiradores_de_marcas_diferentes_passam():
    assert not mesmo_produto(
        "Aspirador de Pó Vertical e Portátil WAP High Speed",
        "Aspirador De Água E Pó Philco 1500w 20l Pas80",
    )


# ---------- mesmo produto barra ----------


def test_o_mesmo_whey_em_dois_tamanhos_e_um_produto_so():
    assert mesmo_produto(
        "Whey Isolate Protein Fuse Refil 1,8kg Dark Lab Sabor Chocolate",
        "Whey Isolate Protein Fuse 900g Dark Lab Sabor Morango",
    )


def test_a_mesma_marca_com_titulo_reescrito_e_um_produto_so():
    """O caso que a assinatura de tres palavras deixava passar."""
    assert mesmo_produto(
        "Suplemento em pó Growth Supplements Whey Protein Concentrado",
        "100% Whey Concentrado 900g Growth Supplements Morango",
    )


def test_os_quatro_anuncios_do_short_ausare_sao_um_produto_so():
    """Quatro anuncios, quatro precos, o mesmo short -- e o caso que so o
    segundo caminho pega: a sobreposicao de palavras cai para 0,36 porque cada
    titulo tem uma cauda de marketing diferente."""
    shorts = [
        "Short Saia Esportivo Feminino Ausare de Lycra com Toque Gelado",
        "Short Saia Esportivo Feminino Ausare com Proteção UV 50+",
        "Short Saia Esportivo Ausare Feminino, para Treino e Beach Tennis",
    ]
    for outro in shorts[1:]:
        assert mesmo_produto(shorts[0], outro)


def test_a_mesma_camiseta_hering_em_dois_tamanhos():
    assert mesmo_produto(
        "Camiseta Feminina Hering Listrada Azul Lisa P",
        "Camiseta Feminina Hering Box Listrada Azul Liso Xg",
    )


def test_a_mesma_bicicleta_ergometrica():
    """Um dos dois exemplos que o dono citou."""
    assert mesmo_produto(
        "Bicicleta Ergométrica Spinning Profissional Fitness Academia",
        "Bicicleta Ergometrica Spinning Profissional Fitness",
    )


def test_a_mesma_air_fryer_da_wap():
    assert mesmo_produto(
        "Fritadeira Elétrica Air Fryer Oven Black INOX WAP",
        "Fritadeira Elétrica Wap Air Fryer Oven Digital Black",
    )


def test_o_mesmo_copo_stanley_em_outra_cor():
    assert mesmo_produto(
        "Copo Térmico Quencher Stanley 1.18l Cor Watercolor",
        "Copo Térmico Quencher Stanley Cor Quartzo Rosa 591ml",
    )


def test_duas_air_fryers_da_mesma_marca_tambem_barram():
    """Modelos diferentes, marca igual. O dono citou tres air fryers WAP
    seguidas na lista de repeticao."""
    assert mesmo_produto(
        "Fritadeira Elétrica Air Fryer Oven Black INOX WAP",
        "Fritadeira Elétrica Air Fryer 8 em 1 Wap Barbecue Health",
    )


# ---------- o que nao pode virar balde ----------


def test_titulo_sem_palavra_significativa_nao_casa_com_ninguem():
    assert not mesmo_produto("de a com", "para o na")
    assert not mesmo_produto("", "Whey Protein Growth")


def test_sabor_e_tamanho_nao_separam_o_mesmo_produto():
    assert mesmo_produto(
        "Whey Protein Growth Supplements 1kg Sabor Chocolate",
        "Whey Protein Growth Supplements 900g Sabor Morango",
    )


def test_cor_e_genero_nao_separam_o_mesmo_produto():
    assert mesmo_produto(
        "Tênis De Corrida Masculino Questar 4 adidas Branco",
        "Tênis De Corrida Feminino Questar 4 adidas Preto",
    )

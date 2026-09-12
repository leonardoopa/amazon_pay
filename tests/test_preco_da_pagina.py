"""O preco do produto lido da propria pagina, e nao da API de catalogo.

Pedido do dono em 12/09/2026: publicar no nosso grupo tudo o que os grupos
"xet das promocoes" e "Economizei" postam do Mercado Livre, com o nosso link.

O que impedia isso nao era regra nenhuma: era cobertura. A rota
`/products/{id}` so responde por produto de CATALOGO, e a maior parte do que
esses grupos postam e anuncio de vendedor -- medido em 09/09/2026, 13 de 43
links resolvidos tinham preco pela API. Os outros 30 morriam antes de virar
oferta.

A pagina que o `grupo_wa` ja baixa para descobrir o ID tem tudo o que falta:
preco, riscado, foto, titulo e o endereco canonico do anuncio. Medido em
12/09/2026 sobre os 25 links mais recentes do xet: 24 com preco, 22 com
riscado, 24 com endereco canonico. Onde a API tambem respondia, os precos
bateram em 5 de 5 casos comparaveis.

A armadilha e que a pagina social nao mostra um produto: mostra um em
destaque, e uma vitrine de recomendados embaixo. Ler qualquer preco solto do
HTML pega o de um recomendado -- numa das paginas medidas havia quatro
`"price"` no corpo e o primeiro NAO era o do produto certo. O que separa e o
titulo: o `og:title` E o produto em destaque.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.grupo_wa import (  # noqa: E402
    _cartao_em_destaque,
    _precos_do_cartao,
)

# Recorte fiel da pagina real de 12/09/2026, com um segundo cartao depois do
# destaque -- que e exatamente o que confundia a leitura ingenua.
PAGINA = """
<head>
<meta property="og:title" content="Kit 3 Shorts Esportivos Plus Size Masculino">
<meta property="og:image" content="https://http2.mlstatic.com/D_1-F.jpg">
</head>
<body>
<div class="poly-card">
  <img alt="Kit 3 Shorts Esportivos Plus Size Masculino"/>
  <a href="https://produto.mercadolivre.com.br/MLB-3872536765-kit-3-shorts-_JM?matt_event_ts=1">
  <s><span class="andes-money-amount andes-money-amount--previous"
     aria-label="Antes: 121 reais com 33 centavos"></span></s>
  <div class="poly-price__current"><span class="andes-money-amount"
     aria-label="Agora: 83 reais com 31 centavos"></span></div>
</div>
<div class="poly-card">
  <img alt="Outro Produto Recomendado Qualquer"/>
  <a href="https://produto.mercadolivre.com.br/MLB-9999999999-outro-_JM">
  <span aria-label="Agora: 31 reais com 35 centavos"></span>
</div>
</body>
"""


def test_o_cartao_do_destaque_e_o_do_titulo_e_nao_o_primeiro_preco():
    cartao = _cartao_em_destaque(PAGINA, "Kit 3 Shorts Esportivos Plus Size Masculino")

    assert "MLB-3872536765" in cartao
    agora, antes = _precos_do_cartao(cartao)
    assert agora == 83.31
    assert antes == 121.33


def test_o_preco_do_recomendado_nao_vaza_para_o_destaque():
    """31,35 e do segundo cartao. Foi o valor que a leitura ingenua pegava."""
    cartao = _cartao_em_destaque(PAGINA, "Kit 3 Shorts Esportivos Plus Size Masculino")

    agora, _antes = _precos_do_cartao(cartao)
    assert agora != 31.35


def test_titulo_que_nao_aparece_no_corpo_nao_inventa_cartao():
    assert _cartao_em_destaque(PAGINA, "Geladeira Que Nao Esta Nesta Pagina") == ""
    assert _cartao_em_destaque(PAGINA, "") == ""


def test_anuncio_sem_desconto_traz_so_o_preco_de_agora():
    cartao = '<span aria-label="Agora: 49 reais com 90 centavos"></span>'

    assert _precos_do_cartao(cartao) == (49.90, 0.0)


def test_preco_sem_centavos_e_lido_inteiro():
    """O ML omite "com N centavos" quando o valor e redondo."""
    cartao = (
        '<span aria-label="Antes: 150 reais"></span>'
        '<span aria-label="Agora: 59 reais"></span>'
    )

    assert _precos_do_cartao(cartao) == (59.0, 150.0)


def test_valor_sem_rotulo_conta_como_preco_de_agora():
    """Cartao sem desconto as vezes vem sem o prefixo "Agora"."""
    cartao = '<span aria-label="229 reais com 49 centavos"></span>'

    assert _precos_do_cartao(cartao) == (229.49, 0.0)


def test_riscado_menor_que_o_preco_atual_e_descartado():
    """Ruido de cartao vizinho viraria "de R$ 20 por R$ 80" -- aumento
    anunciado como promocao. Melhor perder o "de"."""
    cartao = (
        '<span aria-label="Antes: 20 reais"></span>'
        '<span aria-label="Agora: 80 reais"></span>'
    )

    assert _precos_do_cartao(cartao) == (80.0, 0.0)


def test_cartao_vazio_nao_tem_preco_nenhum():
    assert _precos_do_cartao("") == (0.0, 0.0)


# ---------- da pista para a oferta ----------


def _pista(**campos):
    from promo.sources.grupo_wa import Pista

    base = {
        "external_id": "MLB3872536765",
        "titulo": "Kit 3 Shorts Esportivos",
        "origem": "xet",
        "imagem": "https://http2.mlstatic.com/D_1-F.jpg",
        "url": "https://produto.mercadolivre.com.br/MLB-3872536765-kit-_JM",
        "preco": 83.31,
        "preco_antes": 121.33,
    }
    base.update(campos)
    return Pista(**base)


def test_a_pista_com_preco_vira_oferta_completa():
    from promo.pipeline import _offer_da_pista

    offer = _offer_da_pista(_pista())

    assert offer is not None
    assert offer.source == "mercadolivre"
    assert offer.external_id == "MLB3872536765"
    assert offer.price == 83.31
    assert offer.original_price == 121.33
    assert offer.image_url == "https://http2.mlstatic.com/D_1-F.jpg"
    # O endereco canonico e o que o Link Builder precisa: o endereco final da
    # pagina social e o perfil de afiliado DELES.
    assert offer.url.startswith("https://produto.mercadolivre.com.br/MLB-")
    assert "/social/" not in offer.url


def test_a_pista_sem_preco_nao_vira_oferta():
    from promo.pipeline import _offer_da_pista

    assert _offer_da_pista(_pista(preco=0.0)) is None


def test_a_pista_sem_endereco_canonico_nao_vira_oferta():
    """Sem endereco nao ha link nosso, e sem link nosso o post nao rende nada.

    O link deles nunca entra no lugar: e a comissao de quem achou a oferta.
    """
    from promo.pipeline import _offer_da_pista

    assert _offer_da_pista(_pista(url="")) is None


def test_a_pista_sem_riscado_vira_oferta_sem_de():
    from promo.pipeline import _offer_da_pista

    offer = _offer_da_pista(_pista(preco_antes=0.0))

    assert offer is not None
    assert offer.original_price is None

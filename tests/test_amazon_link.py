"""Link de afiliado da Amazon sem API e sem raspagem.

A Creators API pede vendas qualificadas na conta Associates e ainda nao
liberou. Raspar nao e alternativa: o Operating Agreement proibe "any use of
data mining, robots, or similar data gathering and extraction tools" e exige
que preco exibido venha da API. O risco de raspar cairia sobre a mesma conta
que precisa sobreviver ate liberar.

O que sobra e permitido: montar a URL canonica com `?tag=` e usar a imagem
publica por ASIN. Quem escolhe a oferta e le o preco e uma pessoa, pelo comando
`promo amazon-add`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.amazon_link import (  # noqa: E402
    extrair_asin,
    imagem_do_asin,
    link_de_afiliado,
)

TAG = "comunidadedodesconto-20"

# A URL que o dono mandou, copiada do navegador dele, com os rastreadores da
# sessao inteiros.
URL_REAL = (
    "https://www.amazon.com.br/MAX-TITANIUM-CREATINA-300-MONOHIDRATADA/dp/"
    "B07DVJC66X?ref=dlx_ofert_dg_dcl_B07DVJC66X_dt_sl7_dc&"
    "pf_rd_r=KWQKCGPBZQ06KFRY9JRD&pf_rd_p=2c335a86-cc6e-4b38-baf6-aa5ea80847dc&"
    "sbo=RZvfv%2F%2FHxDF%2BO5021pAnSA%3D%3D&th=1"
)


# ---------- extrair o ASIN ----------


def test_a_url_real_do_dono():
    assert extrair_asin(URL_REAL) == "B07DVJC66X"


def test_url_curta_de_produto():
    assert extrair_asin("https://www.amazon.com.br/dp/B07DVJC66X") == "B07DVJC66X"


def test_gp_product():
    assert (
        extrair_asin("https://www.amazon.com.br/gp/product/B07DVJC66X") == "B07DVJC66X"
    )


def test_gp_aw_d_do_app():
    assert extrair_asin("https://www.amazon.com.br/gp/aw/d/B07DVJC66X/") == "B07DVJC66X"


def test_asin_puro():
    assert extrair_asin("B07DVJC66X") == "B07DVJC66X"


def test_asin_puro_em_minuscula():
    """Quem digita a mao nem sempre trava o caps."""
    assert extrair_asin("b07dvjc66x") == "B07DVJC66X"


def test_espaco_em_volta_nao_atrapalha():
    assert extrair_asin("  B07DVJC66X  ") == "B07DVJC66X"


def test_url_sem_esquema():
    assert extrair_asin("amazon.com.br/dp/B07DVJC66X") == "B07DVJC66X"


def test_isbn_de_livro_com_x_no_fim():
    """Livro usa o ISBN-10 no lugar do ASIN, e ele pode terminar em X."""
    assert extrair_asin("https://www.amazon.com.br/dp/857542001X") == "857542001X"


# ---------- o que NAO tem ASIN ----------


def test_busca_nao_tem_asin():
    """Pagina de busca nao e produto. Chutar aqui viraria post apontando para
    outra coisa, com o preco deste."""
    assert extrair_asin("https://www.amazon.com.br/s?k=creatina") is None


def test_link_curto_do_sitestripe_nao_carrega_o_asin():
    """`link.amazon/B02Iv5dY1` e identificador do encurtador, nao ASIN -- tem
    minuscula, e o produto dele e B07DVJC66X."""
    assert extrair_asin("https://link.amazon/B02Iv5dY1") is None


def test_texto_vazio():
    assert extrair_asin("") is None
    assert extrair_asin("   ") is None


def test_none_nao_estoura():
    assert extrair_asin(None) is None


def test_palavra_de_dez_letras_nao_e_asin():
    """Sem o caminho `/dp/`, so um ASIN puro conta -- e ele e maiusculo."""
    assert extrair_asin("https://exemplo.com/creatinaxx") is None


def test_home_da_amazon():
    assert extrair_asin("https://www.amazon.com.br") is None


# ---------- montar o link ----------


def test_o_link_leva_a_tag():
    link = link_de_afiliado("B07DVJC66X", TAG)

    assert link == "https://www.amazon.com.br/dp/B07DVJC66X?tag=" + TAG


def test_o_link_descarta_os_rastreadores_da_sessao():
    """A URL copiada do navegador carrega `ref`, `pf_rd_r` e `sbo` -- que sao
    da sessao DELE. Levar isso ao grupo publica o que nao e nosso e ainda
    concorre com a nossa tag."""
    link = link_de_afiliado(extrair_asin(URL_REAL), TAG)

    for lixo in ("ref=", "pf_rd_r", "pf_rd_p", "sbo=", "th=1"):
        assert lixo not in link


def test_a_tag_da_loja_tambem_serve():
    """A conta tem mais de um ID de rastreamento; os dois valem no `?tag=`."""
    assert link_de_afiliado("B07DVJC66X", "economizeveyo-20").endswith(
        "?tag=economizeveyo-20"
    )


# ---------- imagem ----------


def test_imagem_e_derivada_do_asin():
    assert (
        imagem_do_asin("B07DVJC66X")
        == "https://m.media-amazon.com/images/P/B07DVJC66X.jpg"
    )

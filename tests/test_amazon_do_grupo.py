"""A oferta da Amazon lida do que o outro grupo postou.

Os textos abaixo sao mensagens REAIS do xet e do Economizei, colhidas em
16/09/2026. Elas escrevem diferente -- o xet abre com chamada em caixa alta e
usa valores inteiros, o Economizei comeca no produto e risca o "de" com til --
e o parser precisa dar o mesmo resultado nas duas.

O que estes testes guardam e a honestidade do numero. "R$ 45" sozinho, quando a
mensagem dizia "R$ 45 em 2x", e preco falso saindo com o nosso link do lado.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.amazon_grupo import (  # noqa: E402
    OfertaAmazon,
    _condicoes_da_mensagem,
    _precos_da_mensagem,
    _titulo_da_mensagem,
    nosso_link,
    oferta_da_mensagem,
)

XET_KITKAT = """EU NÃO TENHO EDUCAÇÃO PRA COMER ISSO

Creme Crocante KitKat 330g

De R$ 31 por R$ 20
https://link.amazon/B01wQSA6J
Selecione a opção de compra: *Programe e Poupe* (pode cancelar quando quiser)"""

XET_SEM_DE = """ESSE CREME É MUITO BOM MENINAS

Dove Sérum Hidratante Corporal Pró-Retinol + Firmador 380ml

R$ 28
https://link.amazon/B0dS0gEoz
Selecione a opção de compra: *Programe e Poupe* (pode cancelar quando quiser)"""

XET_PARCELADO = """SUA PELE TÁ IMPLORANDO POR UM DESSE

Bio Oil Restaurador 125ml

De R$ 129 por R$ 45 em 2x
https://link.amazon/B04UsBVvH
Selecione a opção de compra: *Programe e Poupe* (pode cancelar quando quiser)"""

ECONOMIZEI_VODKA = """Vodka Absolut Tabasco 750ml

~De R$ 117,99~

*Por R$ 79,99*

🛒 Compre Aqui: https://link.amazon/B0h68j2Sk"""

ECONOMIZEI_MONITOR = """Monitor Samsung 32", UHD 4K, HDMI Display Port Freesync Série UJ59

~De R$ 1.999,99~

*Por R$ 1.299,99*

🛒 Compre Aqui: https://link.amazon/B0zzzzzzz"""


# ---------- o titulo ----------


def test_a_chamada_em_caixa_alta_nao_e_o_titulo():
    """O xet grita na primeira linha. Nome de produto vem na seguinte."""
    assert _titulo_da_mensagem(XET_KITKAT) == "Creme Crocante KitKat 330g"


def test_o_economizei_comeca_no_produto():
    assert _titulo_da_mensagem(ECONOMIZEI_VODKA) == "Vodka Absolut Tabasco 750ml"


def test_o_titulo_sobrevive_a_virgula_e_as_aspas():
    """Titulo de marketplace tem polegada, virgula e acento -- nao pode truncar."""
    titulo = _titulo_da_mensagem(ECONOMIZEI_MONITOR)

    assert titulo.startswith('Monitor Samsung 32"')
    assert "UJ59" in titulo


def test_linha_de_preco_nao_vira_titulo():
    assert _titulo_da_mensagem("De R$ 31 por R$ 20\n\nhttps://link.amazon/X") == ""


# ---------- os precos ----------


@pytest.mark.parametrize(
    "texto, preco, antes",
    [
        (XET_KITKAT, 20.0, 31.0),
        (ECONOMIZEI_VODKA, 79.99, 117.99),
        (ECONOMIZEI_MONITOR, 1299.99, 1999.99),
    ],
)
def test_de_e_por_sao_lidos_nos_dois_formatos(texto, preco, antes):
    assert _precos_da_mensagem(texto) == (preco, antes)


def test_preco_sozinho_sai_sem_riscado():
    """Sem "de" nao ha desconto a afirmar -- e o post nao pode inventar um."""
    assert _precos_da_mensagem(XET_SEM_DE) == (28.0, 0.0)


def test_o_milhar_com_ponto_nao_vira_numero_errado():
    """ "R$ 1.999,99" tem que virar 1999.99, nunca 1.99999 nem 199999."""
    preco, _antes = _precos_da_mensagem("Por R$ 1.999,99")

    assert preco == 1999.99


def test_mensagem_com_tres_precos_nao_e_adivinhada():
    """Sem saber qual e qual, chutar produziria preco errado no grupo."""
    texto = "Produto X\nDe R$ 100 por R$ 80\nFrete R$ 20"

    assert _precos_da_mensagem(texto) == (0.0, 0.0)


# ---------- as condicoes, que fazem o preco ser verdadeiro ----------


def test_o_parcelado_vira_condicao_e_nao_descarta_a_oferta():
    """Pedido do dono em 16/09/2026: sair igual ao post deles."""
    assert _condicoes_da_mensagem(XET_PARCELADO) == ("em 2x", "Programe e Poupe")


def test_programe_e_poupe_e_condicao():
    assert _condicoes_da_mensagem(XET_KITKAT) == ("Programe e Poupe",)


def test_sem_juros_entra_no_rotulo():
    assert _condicoes_da_mensagem("por R$ 45 em 3x sem juros") == ("em 3x sem juros",)


def test_preco_a_vista_nao_tem_condicao():
    assert _condicoes_da_mensagem(ECONOMIZEI_VODKA) == ()


def test_a_condicao_escrita_junta_as_duas():
    oferta = OfertaAmazon(
        asin="B0FMLBQ136",
        titulo="Bio Oil",
        preco=45.0,
        condicoes=("em 2x", "Programe e Poupe"),
    )

    assert oferta.condicao == "em 2x e Programe e Poupe"


def test_sem_condicao_a_frase_fica_vazia():
    oferta = OfertaAmazon(asin="B0FMLBQ136", titulo="Vodka", preco=79.99)

    assert oferta.condicao == ""


# ---------- o link ----------


def test_o_nosso_link_e_canonico_e_leva_a_nossa_tag():
    """A tag deles (`pietroxet-20`) nao pode encostar no nosso post, e os
    rastreadores de sessao (`ref=`, `pf_rd_r`) tambem nao."""
    link = nosso_link("B0FMLBQ136", "comunidaded0a-20")

    assert link == "https://www.amazon.com.br/dp/B0FMLBQ136?tag=comunidaded0a-20"
    assert "pietroxet" not in link
    assert "ref=" not in link


def test_sem_asin_nao_ha_oferta(monkeypatch: pytest.MonkeyPatch):
    """Link que nao leva a produto -- uma pagina de campanha, por exemplo --
    nao vira post. Chutar ASIN mandaria o grupo para outro produto."""
    monkeypatch.setattr(
        "promo.sources.amazon_grupo.asin_do_link", lambda _url, **_kw: None
    )

    assert oferta_da_mensagem(XET_KITKAT, "https://link.amazon/B0aanF32w") is None


def test_a_oferta_completa_sai_dos_campos_certos(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "promo.sources.amazon_grupo.asin_do_link", lambda _url, **_kw: "B0FMLBQ136"
    )

    oferta = oferta_da_mensagem(XET_PARCELADO, "https://link.amazon/B04UsBVvH")

    assert oferta == OfertaAmazon(
        asin="B0FMLBQ136",
        titulo="Bio Oil Restaurador 125ml",
        preco=45.0,
        preco_antes=129.0,
        condicoes=("em 2x", "Programe e Poupe"),
    )


# ---------- a condicao chega no post ----------


def _scored(condicao: str = "", preco: float = 45.0):
    from promo.models import Offer, ScoredOffer

    offer = Offer(
        source="amazon",
        external_id="B079VQTRM7",
        title="Bio Oil Restaurador 125ml",
        price=preco,
        url="https://www.amazon.com.br/dp/B079VQTRM7",
        original_price=129.0,
        condicao=condicao,
    )
    return ScoredOffer(
        offer=offer,
        baseline=129.0,
        discount_pct=65.1,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


def test_o_fallback_cola_a_condicao_no_preco():
    from promo.copywriter import fallback_copy

    texto = fallback_copy(_scored("em 2x"), "https://www.amazon.com.br/dp/X?tag=t")

    assert "*R$ 45,00* em 2x" in texto


def test_o_post_sem_a_condicao_e_recusado():
    """O numero sozinho e um terco do valor real."""
    from promo.copywriter import _reject_preco_sem_condicao

    texto = "CHAMADA\n\nBio Oil\n\nDe R$ 129,00 por *R$ 45,00*\n\nhttps://x"

    with pytest.raises(RuntimeError, match="condicao"):
        _reject_preco_sem_condicao(texto, _scored("em 2x").offer)


def test_a_condicao_noutra_linha_nao_serve():
    """Quem le o preco tem que ler a condicao no mesmo folego."""
    from promo.copywriter import _reject_preco_sem_condicao

    texto = "CHAMADA\n\nBio Oil\n\nDe R$ 129,00 por *R$ 45,00*\nem 2x\n\nhttps://x"

    with pytest.raises(RuntimeError, match="condicao"):
        _reject_preco_sem_condicao(texto, _scored("em 2x").offer)


def test_oferta_a_vista_nao_exige_nada():
    from promo.copywriter import _reject_preco_sem_condicao

    _reject_preco_sem_condicao("CHAMADA\n\nVodka\n\n*R$ 79,99*", _scored("").offer)


def test_os_fatos_mandam_a_condicao_para_o_gemini():
    from promo.copywriter import _facts

    fatos = _facts(_scored("em 2x e Programe e Poupe"), "https://x")

    assert "em 2x e Programe e Poupe" in fatos
    assert "OBRIGATORIA" in fatos


def test_a_divulgacao_da_amazon_sai_literal_no_repasse():
    """Clausula 5: frase exata. O repasse nao e excecao."""
    from promo.copywriter import DISCLOSURES, fallback_copy

    texto = fallback_copy(_scored("em 2x"), "https://www.amazon.com.br/dp/X?tag=t")

    assert DISCLOSURES["amazon"] in texto


def test_o_link_do_post_nunca_leva_a_tag_deles():
    from promo.sources.amazon_grupo import AmazonDoGrupo

    link = AmazonDoGrupo("comunidaded0a-20").affiliate_url(_scored().offer)

    assert link.endswith("?tag=comunidaded0a-20")
    assert "pietroxet" not in link


def test_sem_tag_nao_ha_link_e_a_oferta_fica_retida():
    """Post sem tag e trabalho de graca para a Amazon; o pipeline segura a
    oferta quando `affiliate_url` volta vazio."""
    from promo.sources.amazon_grupo import AmazonDoGrupo

    assert AmazonDoGrupo("").affiliate_url(_scored().offer) == ""

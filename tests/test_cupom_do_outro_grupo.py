"""O cupom que o outro grupo citou junto do produto entra no nosso post.

Pedido do dono em 12/09/2026: "quando os grupos como xet e economizei mandarem
cupons, coloque esses cupons nas nossas publicacoes tambem, pois assim e mais
facil de vendermos".

O codigo e um fato sobre a campanha do ML, igual ao preco: quem abrir o site
acha o mesmo cupom, e ele funciona no carrinho de qualquer um. O que nao se
aproveita e o texto em volta.

Medido no mesmo dia: 36 das 50 mensagens do xet e 13 das 15 do Economizei
citam cupom, sempre delimitado -- crase no xet, asterisco no Economizei.

O par (produto, cupom) e o que importa. Esses codigos sao quase sempre de marca
ou de loja, entao um cupom lido de uma mensagem e colado noutro produto viraria
codigo que o checkout recusa.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.grupo_wa import (  # noqa: E402
    cupons_da_mensagem,
    links_e_cupons,
)


def msg(texto: str) -> dict:
    return {"message": {"conversation": texto}}


# ---------- ler o codigo ----------


def test_le_o_cupom_entre_crases_como_o_xet_escreve():
    texto = (
        "BOLSA DA PUMA PRA ACADEMIA\n\nBolsa Puma Phase Small Sports 22L\n\n"
        "De R$ 229 por R$ 147 em 5x\nUse o cupom: `EXCLUSIVONOMELI` 🎟️\n\n"
        "Loja Oficial Puma no ML\nhttps://meli.la/2LYM5DU"
    )

    assert cupons_da_mensagem(texto) == ("EXCLUSIVONOMELI",)


def test_le_o_cupom_entre_asteriscos_como_o_economizei_escreve():
    texto = (
        "Kit 6 Cuecas Boxer Reebok\n\n~De R$ 149,99~\n\n*Por R$ 52,99*\n\n"
        "🎟️ Cupom: *EXCLUSIVONOMELI*\n\n🛒 Compre Aqui: https://meli.la/1S7Fboy"
    )

    assert cupons_da_mensagem(texto) == ("EXCLUSIVONOMELI",)


def test_le_os_dois_quando_a_mensagem_oferece_alternativa():
    texto = "Use o Cupom: `PROMONETS` ou `XETPROMOCOES` 🎟️"

    assert cupons_da_mensagem(texto) == ("PROMONETS", "XETPROMOCOES")


def test_codigo_com_numero_e_valido():
    assert cupons_da_mensagem("Use o Cupom: `FULL1209` 🎟️") == ("FULL1209",)


def test_nao_repete_o_mesmo_codigo():
    texto = "Cupom: *DESCONTASSO* -- lembrando, o cupom *DESCONTASSO*"

    assert cupons_da_mensagem(texto) == ("DESCONTASSO",)


# ---------- o que nao e cupom ----------


def test_mensagem_sem_a_palavra_cupom_nao_rende_codigo():
    """Os dois grupos usam asterisco para dar enfase o tempo todo. Sem o
    rotulo, "OFERTA" e "HOJE" virariam codigo."""
    texto = "*PRECINHO* de HOJE\n*IMPERDIVEL*\nhttps://meli.la/2buwzMD"

    assert cupons_da_mensagem(texto) == ()


def test_palavra_generica_entre_delimitadores_nao_e_cupom():
    texto = "Use o Cupom: `LEVEAGORA` 🎟️ + Selecione *PIX* e *COMPRE* *AQUI*"

    assert cupons_da_mensagem(texto) == ("LEVEAGORA",)


def test_codigo_sem_delimitador_nao_e_lido():
    """Sem delimitador nao da para separar o codigo do resto da frase."""
    assert cupons_da_mensagem("Use o cupom EXCLUSIVONOMELI na compra") == ()


def test_codigo_curto_demais_nao_e_lido():
    assert cupons_da_mensagem("Cupom: `ABC`") == ()


def test_texto_vazio_nao_estoura():
    assert cupons_da_mensagem("") == ()


# ---------- o par produto/cupom ----------


def test_o_cupom_fica_com_o_link_da_mesma_mensagem():
    registros = [
        msg("Bolsa Puma\nUse o cupom: `EXCLUSIVONOMELI`\nhttps://meli.la/2LYM5DU"),
        msg("Perfume Sabah\nUse o Cupom: `FULL1209`\nhttps://meli.la/1nfoG7J"),
    ]

    assert links_e_cupons(registros) == [
        ("https://meli.la/2LYM5DU", ("EXCLUSIVONOMELI",)),
        ("https://meli.la/1nfoG7J", ("FULL1209",)),
    ]


def test_cupom_de_uma_mensagem_nao_vaza_para_a_outra():
    """Colar um cupom de marca noutro produto da codigo que o checkout
    recusa."""
    registros = [
        msg("Com cupom: `EXCLUSIVONOMELI`\nhttps://meli.la/aaa"),
        msg("Sem cupom nenhum aqui\nhttps://meli.la/bbb"),
    ]

    pares = dict(links_e_cupons(registros))

    assert pares["https://meli.la/aaa"] == ("EXCLUSIVONOMELI",)
    assert pares["https://meli.la/bbb"] == ()


def test_post_de_outra_loja_nao_produz_par():
    """O xet posta Netshoes tambem, com cupom que so vale la. Como o link nao
    e do ML, nao ha produto nenhum para carregar o codigo."""
    registros = [
        msg("Tenis Adidas\nCupom: `XETPROMOCOES`\nhttps://rakuten.swlink.pro/kU8pBa52")
    ]

    assert links_e_cupons(registros) == []


def test_links_das_mensagens_continua_devolvendo_so_as_urls():
    from promo.sources.grupo_wa import links_das_mensagens

    registros = [msg("Cupom: `ABCDEF`\nhttps://meli.la/aaa")]

    assert links_das_mensagens(registros) == ["https://meli.la/aaa"]


# ---------- o cupom no post ----------


def test_o_cupom_deles_vem_na_frente_dos_nossos():
    from promo.models import Offer
    from promo.pipeline import _CUPONS_DO_OUTRO_GRUPO, cupom_para

    offer = Offer(
        source="mercadolivre",
        external_id="MLB777",
        title="Bolsa Puma Phase Small Sports 22L",
        price=147.0,
        url="https://mercadolivre.com.br/MLB777",
    )
    _CUPONS_DO_OUTRO_GRUPO["MLB777"] = ("EXCLUSIVONOMELI",)
    try:
        saida = cupom_para(offer, [{"code": "GERALZAO", "restrito": False}])
    finally:
        _CUPONS_DO_OUTRO_GRUPO.pop("MLB777", None)

    assert saida.split(" ou ")[0] == "EXCLUSIVONOMELI"


def test_o_cupom_deles_sai_mesmo_sem_cupom_nosso_nenhum():
    """O estado normal: o painel e o Pelando nao conhecem esse codigo."""
    from promo.models import Offer
    from promo.pipeline import _CUPONS_DO_OUTRO_GRUPO, cupom_para

    offer = Offer(
        source="mercadolivre",
        external_id="MLB778",
        title="Kit 6 Cuecas Boxer Reebok",
        price=52.99,
        url="https://mercadolivre.com.br/MLB778",
    )
    _CUPONS_DO_OUTRO_GRUPO["MLB778"] = ("EXCLUSIVONOMELI",)
    try:
        assert cupom_para(offer, []) == "EXCLUSIVONOMELI"
    finally:
        _CUPONS_DO_OUTRO_GRUPO.pop("MLB778", None)


def test_produto_sem_cupom_deles_segue_a_regra_de_sempre():
    from promo.models import Offer
    from promo.pipeline import cupom_para

    offer = Offer(
        source="mercadolivre",
        external_id="MLB779",
        title="Tênis Kappa Sparta",
        price=99.0,
        url="https://mercadolivre.com.br/MLB779",
    )

    assert cupom_para(offer, []) is None


def test_cupom_do_ml_nunca_sai_em_post_da_amazon():
    """A trava de fonte continua valendo: o checkout da Amazon nunca vai
    aceitar codigo de campanha do ML."""
    from promo.models import Offer
    from promo.pipeline import _CUPONS_DO_OUTRO_GRUPO, cupom_para

    offer = Offer(
        source="amazon",
        external_id="B0XYZ",
        title="Bolsa Puma Phase Small",
        price=147.0,
        url="https://amazon.com.br/dp/B0XYZ",
    )
    _CUPONS_DO_OUTRO_GRUPO["B0XYZ"] = ("EXCLUSIVONOMELI",)
    try:
        assert cupom_para(offer, []) is None
    finally:
        _CUPONS_DO_OUTRO_GRUPO.pop("B0XYZ", None)

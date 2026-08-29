"""A mesma chamada nao sai duas vezes no grupo.

O prompt sempre listou as chamadas recentes pedindo para nao repetir, e o
banco mostrou que isso nao bastava: seis repeticoes, todas a 2 a 6 posts de
distancia -- dentro da mesma rodada, portanto dentro da lista que o modelo
tinha em maos. "MAIS BARATO QUE O TEU IFOOD DE ONTEM" saiu tres vezes.

Produto nenhum repetiu; o que o leitor via era a frase.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.copywriter import Copywriter, _chave_da_chamada  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402


def uma_oferta() -> ScoredOffer:
    return ScoredOffer(
        offer=Offer(
            source="mercadolivre",
            external_id="MLB1",
            title="Air Fryer Philco 6,5L",
            price=299.0,
            url="https://mercadolivre.com.br/MLB1",
        ),
        baseline=450.0,
        discount_pct=33.0,
        observations=9,
        lowest_ever=False,
        verified=True,
    )


def escritor_que_responde(*textos: str) -> Copywriter:
    """Copywriter com o Gemini trocado por uma lista de respostas fixas."""
    escritor = Copywriter.__new__(Copywriter)
    escritor._escrever_uma_vez = mock.Mock(side_effect=list(textos))
    return escritor


# ---------- a chave de comparacao ----------


def test_emoji_e_pontuacao_nao_fazem_a_chamada_ser_nova():
    a = _chave_da_chamada("MAIS BARATO QUE UM LANCHE DE PADARIA 🦷")
    b = _chave_da_chamada("mais barato que um lanche de padaria!")
    assert a == b


def test_acento_nao_faz_a_chamada_ser_nova():
    assert _chave_da_chamada("QUEM AUTORIZOU ESSE PREÇO?") == _chave_da_chamada(
        "quem autorizou esse preco"
    )


def test_chamadas_diferentes_continuam_diferentes():
    assert _chave_da_chamada("PRECO DE ERRO NO SISTEMA") != _chave_da_chamada(
        "MAIS BARATO QUE O TEU IFOOD DE ONTEM"
    )


def test_texto_vazio_nao_estoura():
    assert _chave_da_chamada("") == ""


# ---------- o comportamento ----------


def test_chamada_nova_passa_de_primeira():
    escritor = escritor_que_responde("PRECO DE ERRO NO SISTEMA\n\nresto do post")

    texto = escritor.write(uma_oferta(), "https://link", None, ["OUTRA COISA"])

    assert texto.startswith("PRECO DE ERRO")
    assert escritor._escrever_uma_vez.call_count == 1


def test_chamada_repetida_pede_outra():
    escritor = escritor_que_responde(
        "MAIS BARATO QUE O TEU IFOOD DE ONTEM\n\nresto",
        "PRECO DE ERRO NO SISTEMA\n\nresto",
    )

    texto = escritor.write(
        uma_oferta(), "https://link", None, ["MAIS BARATO QUE O TEU IFOOD DE ONTEM"]
    )

    assert texto.startswith("PRECO DE ERRO")
    assert escritor._escrever_uma_vez.call_count == 2


def test_insistir_na_repeticao_levanta_e_cai_no_fallback():
    """Levantar aqui é de propósito: o `deliver()` trata a excecao usando o
    `fallback_copy`, que abre com o titulo do produto e nunca repete."""
    escritor = escritor_que_responde(
        "MAIS BARATO QUE O TEU IFOOD DE ONTEM\n\nresto",
        "mais barato que o teu ifood de ontem!!\n\nresto",
    )

    with pytest.raises(RuntimeError, match="ja usada"):
        escritor.write(
            uma_oferta(), "https://link", None, ["MAIS BARATO QUE O TEU IFOOD DE ONTEM"]
        )

    assert escritor._escrever_uma_vez.call_count == 2


def test_sem_lista_de_recentes_nao_ha_o_que_comparar():
    escritor = escritor_que_responde("QUALQUER CHAMADA\n\nresto")

    texto = escritor.write(uma_oferta(), "https://link", None, None)

    assert texto.startswith("QUALQUER CHAMADA")
    assert escritor._escrever_uma_vez.call_count == 1


def test_linha_vazia_na_lista_nao_bloqueia_tudo():
    """Post antigo com primeira linha vazia entrava como "" na lista e
    bloquearia qualquer texto cuja chave tambem reduzisse a vazio."""
    escritor = escritor_que_responde("CHAMADA NOVA\n\nresto")

    texto = escritor.write(uma_oferta(), "https://link", None, ["", "  "])

    assert texto.startswith("CHAMADA NOVA")

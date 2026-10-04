"""O comando `promo amazon-add`, que enfileira oferta da Amazon escolhida a mao.

Existe enquanto a Creators API nao libera. O que ele automatiza e tudo menos a
escolha: link com a tag, imagem por ASIN, texto pelo copywriter, e a fila de
sempre com o mesmo gotejamento.

O preco entra a mao de proposito, e nao por falta de engenho: raspar a Amazon
bate no Operating Agreement do Associates, que proibe ferramenta de extracao e
exige que preco exibido venha da API. Quem le o preco aqui e uma pessoa.

O post sai `verified=False` -- a baseline e o "de" que a loja anuncia, nao a
nossa mediana. O site usa essa coluna para nao apresentar repasse como prova
medida, e o mesmo vale aqui.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.cli import main  # noqa: E402

URL = "https://www.amazon.com.br/dp/B07DVJC66X"
TAG = "comunidadedodesconto-20"


@pytest.fixture
def banco(tmp_path, monkeypatch):
    caminho = tmp_path / "promos.db"
    monkeypatch.setenv("DB_PATH", str(caminho))
    monkeypatch.setenv("AMAZON_PARTNER_TAG", TAG)
    return caminho


def rodar(*extra: str) -> int:
    return main(
        [
            "amazon-add",
            URL,
            "--titulo",
            "Creatina Monohidratada 300g Max Titanium",
            "--preco",
            "89.90",
            "--de",
            "129.90",
            "--sem-ia",
            *extra,
        ]
    )


def linhas(caminho: Path, tabela: str) -> list[sqlite3.Row]:
    """As linhas da tabela, ou vazio se o banco nem chegou a existir.

    Recusa boa nao cria banco: as validacoes rodam antes do `init_db()`, entao
    entrada invalida nao deixa arquivo para tras. Tratar isso como lista vazia
    e o que deixa o mesmo assert servir para os dois casos.
    """
    if not Path(caminho).exists():
        return []
    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    return conn.execute(f"SELECT * FROM {tabela}").fetchall()


# ---------- o caminho feliz ----------


def test_enfileira_o_post(banco):
    assert rodar() == 0

    posts = linhas(banco, "posts")
    assert len(posts) == 1
    assert posts[0]["status"] == "pending"
    assert posts[0]["product_id"] == "amazon:B07DVJC66X"


def test_o_post_nao_e_verificado(banco):
    """Nao medimos essa baseline: o "de" e o que a loja anuncia. O site le esta
    coluna para nao apresentar repasse como prova."""
    rodar()

    assert linhas(banco, "posts")[0]["verified"] == 0


def test_o_desconto_e_calculado(banco):
    rodar()

    post = linhas(banco, "posts")[0]
    assert post["price"] == pytest.approx(89.90)
    assert post["baseline"] == pytest.approx(129.90)
    assert post["discount_pct"] == pytest.approx(30.79, abs=0.01)


def test_o_produto_entra_no_banco(banco):
    rodar()

    produto = linhas(banco, "products")[0]
    assert produto["source"] == "amazon"
    assert produto["external_id"] == "B07DVJC66X"
    assert produto["title"] == "Creatina Monohidratada 300g Max Titanium"


def test_o_link_de_afiliado_e_salvo(banco):
    """Sem isso o `pending-links` voltaria a pedir link do painel do ML para um
    produto que nem e do ML."""
    rodar()

    link = linhas(banco, "affiliate_links")[0]
    assert link["url"] == f"https://www.amazon.com.br/dp/B07DVJC66X?tag={TAG}"


def test_a_imagem_sai_do_asin(banco):
    rodar()

    assert (
        linhas(banco, "posts")[0]["image_url"]
        == "https://m.media-amazon.com/images/P/B07DVJC66X.jpg"
    )


def test_sem_imagem_manda_so_texto(banco):
    rodar("--sem-imagem")

    assert linhas(banco, "posts")[0]["image_url"] is None


def test_imagem_propria_ganha_da_automatica(banco):
    rodar("--imagem", "https://exemplo/foto.jpg")

    assert linhas(banco, "posts")[0]["image_url"] == "https://exemplo/foto.jpg"


def test_a_tag_da_linha_de_comando_ganha_do_ambiente(banco):
    rodar("--tag", "economizeveyo-20")

    assert linhas(banco, "affiliate_links")[0]["url"].endswith("?tag=economizeveyo-20")


def test_o_texto_traz_o_link_sem_a_divulgacao(banco):
    """A divulgacao da Amazon foi retirada a pedido em 22/09/2026, com a fonte
    desligada. A tag de afiliado no link continua sendo o que faz a comissao
    existir -- ela nao pode sumir junto."""
    rodar()

    copy = linhas(banco, "posts")[0]["copy"]
    assert f"?tag={TAG}" in copy
    assert "Programa de Associados" not in copy


# ---------- dry-run ----------


def test_dry_run_nao_enfileira(banco):
    assert rodar("--dry-run") == 0
    assert linhas(banco, "posts") == []


# ---------- o que tem que ser recusado ----------


def test_sem_tag_nao_roda(banco, monkeypatch):
    """Link sem tag e trabalho de graca para a Amazon."""
    monkeypatch.setenv("AMAZON_PARTNER_TAG", "")

    assert rodar() == 2
    assert linhas(banco, "posts") == []


def test_url_sem_asin_e_recusada(banco):
    codigo = main(
        [
            "amazon-add",
            "https://www.amazon.com.br/s?k=creatina",
            "--titulo",
            "Creatina",
            "--preco",
            "89.90",
            "--de",
            "129.90",
            "--sem-ia",
        ]
    )

    assert codigo == 2
    assert linhas(banco, "posts") == []


def test_preco_maior_que_o_de_e_recusado(banco):
    """Sem queda nao ha oferta -- e um post que sobe preco e pior que post
    nenhum."""
    codigo = main(
        [
            "amazon-add",
            URL,
            "--titulo",
            "Creatina",
            "--preco",
            "129.90",
            "--de",
            "89.90",
            "--sem-ia",
        ]
    )

    assert codigo == 2
    assert linhas(banco, "posts") == []


def test_preco_igual_ao_de_e_recusado(banco):
    codigo = main(
        [
            "amazon-add",
            URL,
            "--titulo",
            "Creatina",
            "--preco",
            "89.90",
            "--de",
            "89.90",
            "--sem-ia",
        ]
    )

    assert codigo == 2


def test_preco_zero_e_recusado(banco):
    codigo = main(
        [
            "amazon-add",
            URL,
            "--titulo",
            "Creatina",
            "--preco",
            "0",
            "--de",
            "89.90",
            "--sem-ia",
        ]
    )

    assert codigo == 2

"""Republicar o que o grupo vizinho posta, sem os nossos filtros de preco.

Pedido em 09/09/2026: "toda vez que eles mandarem algo no grupo deles
(promocao do Mercado Livre) a gente reenvie no nosso grupo com o nosso link,
com 100% de prioridade".

O que ja existia era so a ORDENACAO: a pista ia para o topo da fila. Nao
bastava, e a medicao mostrou por que -- a pista quase nunca CHEGAVA a ser
ordenada. `score` exige historico proprio, que produto recem-descoberto nao
tem, e `score_campaign` exige preco riscado no anuncio, presente em 9 de 13
medidos. As outras viravam so coleta.

Daqui saiu a terceira porta, `score_pista`, e com ela uma escolha editorial: o
post nao tem desconto nenhum para anunciar, nem medido nem riscado. O que
sustenta a publicacao e a curadoria de quem achou. O texto entao fala do preco
de hoje e nao pode escrever "De X por Y" nem porcentagem -- que e o que este
arquivo garante.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import Rules  # noqa: E402
from promo.db import SCHEMA  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.scoring import score_pista as _score_pista  # noqa: E402


def banco():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


REGRAS = Rules.load()


def score_pista(offer: Offer, conn=None, rules: Rules | None = None):
    """`score_pista` real, com um banco vazio quando o teste nao se importa.

    Banco vazio quer dizer "esta pista nunca virou post", que e o caso da
    maioria dos testes aqui. Quem testa o cooldown passa o seu.
    """
    return _score_pista(conn if conn is not None else banco(), offer, rules or REGRAS)


def oferta(preco: float = 155.0, disponivel: bool = True) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB60687768",
        title="Lattafa Bade'e Al Oud for glory 100ml",
        price=preco,
        url="https://mercadolivre.com.br/p/MLB60687768",
        available=disponivel,
    )


# ---------- a terceira porta ----------


def test_pista_sem_historico_e_sem_riscado_vira_post():
    """O caso que as outras duas portas recusam, e que motivou esta."""
    scored = score_pista(oferta())

    assert scored is not None
    assert scored.offer.price == 155.0


def test_pista_nao_afirma_desconto():
    """Sem baseline e sem riscado nao ha queda para anunciar."""
    scored = score_pista(oferta())

    assert scored.discount_pct == 0.0
    assert scored.baseline == scored.offer.price
    assert scored.verified is False
    assert scored.sem_medicao is True
    assert scored.lowest_ever is False


@pytest.mark.parametrize(
    "preco, disponivel",
    [(0.0, True), (-1.0, True), (155.0, False)],
    ids=["preco zero", "preco negativo", "indisponivel"],
)
def test_pista_sem_preco_util_fica_de_fora(preco, disponivel):
    """A porta e fraca, nao inexistente: sem preco valido nao ha post."""
    assert score_pista(oferta(preco, disponivel)) is None


# ---------- o texto ----------


def pontuada_sem_medicao() -> ScoredOffer:
    return score_pista(oferta())


def test_os_fatos_nao_carregam_preco_antigo():
    """O prompt nao pode receber um "De" que nao existe.

    Antes deste caminho, `_facts` mandava `baseline` sempre -- e com baseline
    igual ao preco o modelo receberia "De R$ 155,00 / Por R$ 155,00 / Desconto
    0%" e escreveria em cima disso.
    """
    from promo.copywriter import _facts

    fatos = _facts(pontuada_sem_medicao(), "https://mercadolivre.com/sec/abc")

    assert "De (media" not in fatos
    assert "Desconto contra a media" not in fatos
    assert "R$ 155,00" in fatos
    assert "NAO escreva porcentagem" in fatos


def test_o_texto_que_inventa_desconto_e_recusado():
    """A instrucao no prompt nao basta -- e a razao de todo `_reject_*`."""
    from promo.copywriter import _reject_unfounded_claims

    for inventado in (
        "Perfume Lattafa com 30% off, corre!",
        "De R$ 299,00 por R$ 155,00, aproveita",
        "Esse perfume caiu de preco, olha so",
        "Custava R$ 300 e agora ta R$ 155",
    ):
        with pytest.raises(RuntimeError, match="afirma desconto"):
            _reject_unfounded_claims(inventado, pontuada_sem_medicao())


def test_o_texto_honesto_passa():
    """Falar do preco de hoje e exatamente o que se espera."""
    from promo.copywriter import _reject_unfounded_claims

    _reject_unfounded_claims(
        "Lattafa Bade'e Al Oud saindo por R$ 155,00. Perfume arabe que dura o "
        "dia inteiro.",
        pontuada_sem_medicao(),
    )


def test_o_cupom_continua_permitido_na_pista():
    """Cupom e desconto de verdade, e nao depende de baseline nenhuma.

    A guarda olha afirmacao sobre o preco ANTIGO. Barrar a palavra "desconto"
    tiraria da pista a unica coisa que ela poderia legitimamente anunciar.
    """
    from promo.copywriter import _reject_unfounded_claims

    _reject_unfounded_claims(
        "Lattafa por R$ 155,00. Usa o cupom TORCIDA que ainda tem desconto.",
        pontuada_sem_medicao(),
    )


def test_o_template_de_emergencia_nao_repete_o_preco():
    """Sem Gemini o post sai por template, e ele tambem nao pode mentir.

    Com `baseline` igual ao preco, o formato antigo produzia "De R$ 155,00 por
    R$ 155,00".
    """
    from promo.copywriter import fallback_copy

    texto = fallback_copy(pontuada_sem_medicao(), "https://mercadolivre.com/sec/abc")

    assert "De R$" not in texto
    assert texto.count("155,00") == 1
    assert "*R$ 155,00*" in texto


# ---------- a fila ----------


def banco():
    import sqlite3

    from promo.db import SCHEMA

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def enfileirar(conn, produto: str, prioridade: int = 0) -> int:
    from promo.db import create_post, record_offer

    record_offer(
        conn,
        Offer(
            source="mercadolivre",
            external_id=produto,
            title=f"Produto {produto}",
            price=100.0,
            url=f"https://mercadolivre.com.br/{produto}",
        ),
    )
    return create_post(
        conn,
        f"mercadolivre:{produto}",
        100.0,
        150.0,
        33.0,
        "texto",
        prioridade=prioridade,
    )


def test_a_pista_passa_na_frente_do_que_ja_esperava():
    """O ponto do pedido: 100% de prioridade.

    A fila drena a 120 s por post. Com trinta esperando, herdar a posicao pelo
    `created_at` colocaria a pista uma hora depois -- e ela vale justamente
    porque acabou de ser postada.
    """
    from promo.db import pending_posts

    conn = banco()
    enfileirar(conn, "ANTIGO_1")
    enfileirar(conn, "ANTIGO_2")
    enfileirar(conn, "PISTA", prioridade=1)

    ordem = [linha["id"] for linha in pending_posts(conn)]

    assert ordem[0] == 3


def test_entre_posts_normais_a_ordem_continua_a_de_chegada():
    """A coluna nova nao pode reordenar o que ja funcionava."""
    from promo.db import pending_posts

    conn = banco()
    primeiro = enfileirar(conn, "A")
    segundo = enfileirar(conn, "B")
    terceiro = enfileirar(conn, "C")

    ordem = [linha["id"] for linha in pending_posts(conn)]

    assert ordem == [primeiro, segundo, terceiro]


def test_o_post_da_pista_nasce_com_prioridade():
    """`_gravar_post` le a marca da descoberta, e nao um campo do produto."""
    from promo.pipeline import _VISTOS_EM_OUTRO_GRUPO, _gravar_post

    conn = banco()
    from promo.db import record_offer

    scored = score_pista(oferta())
    record_offer(conn, scored.offer)

    _VISTOS_EM_OUTRO_GRUPO.clear()
    _VISTOS_EM_OUTRO_GRUPO.add(scored.offer.external_id)
    try:
        _gravar_post(conn, scored, "texto do post", "")
    finally:
        _VISTOS_EM_OUTRO_GRUPO.clear()

    assert conn.execute("SELECT prioridade FROM posts").fetchone()[0] == 1


def test_post_normal_nasce_sem_prioridade():
    from promo.db import record_offer
    from promo.pipeline import _VISTOS_EM_OUTRO_GRUPO, _gravar_post

    conn = banco()
    scored = ScoredOffer(
        offer=oferta(), baseline=200.0, discount_pct=22.5, observations=9,
        lowest_ever=False,
    )
    record_offer(conn, scored.offer)

    _VISTOS_EM_OUTRO_GRUPO.clear()
    _gravar_post(conn, scored, "texto do post", "")

    assert conn.execute("SELECT prioridade FROM posts").fetchone()[0] == 0


# ---------- o que o grupo mostrou em 09/09/2026 ----------
#
# Tres defeitos reais, medidos no banco de producao no mesmo dia em que a
# terceira porta entrou no ar.


def test_a_pista_nao_volta_a_cada_rodada():
    """O pior dos tres: `MLB4341313127` saiu SEIS vezes num dia.

    04:53, 05:59, 07:28, 08:42, 10:03 e 13:10. A causa e estrutural, e nao um
    acaso: a fonte rele as mesmas 50 mensagens a cada rodada, entao a pista
    continua aparecendo enquanto o grupo vizinho nao empurrar ela para fora da
    janela. Sem cooldown, republicar de novo nao era um risco -- era o que ia
    acontecer sempre.
    """
    from promo.db import create_post, mark_post_sent, record_offer

    conn = banco()
    produto = oferta()
    record_offer(conn, produto)
    post = create_post(
        conn, produto.product_id, 155.0, 155.0, 0.0, "texto", prioridade=1
    )
    mark_post_sent(conn, post)

    assert score_pista(produto, conn=conn) is None


def test_a_pista_inedita_passa_pelo_cooldown():
    """A trava e sobre repetir, nao sobre publicar."""
    conn = banco()

    assert score_pista(oferta(), conn=conn) is not None


def test_o_cooldown_da_pista_so_cede_a_preco_menor():
    """`_in_cooldown` libera quem ficou 10% mais barato que no post anterior.

    A pista que a fonte rele a cada rodada volta sempre pelo mesmo preco,
    entao a trava vale integralmente ate `REPOST_COOLDOWN_DAYS` passar. E o
    que faz "uma vez por dia" ser uma garantia aqui, e nao uma tendencia.
    """
    from promo.db import create_post, mark_post_sent, record_offer

    conn = banco()
    produto = oferta()
    record_offer(conn, produto)
    # O post anterior saiu sem desconto nenhum -- o piso mais baixo possivel.
    post = create_post(
        conn, produto.product_id, 155.0, 155.0, 0.0, "texto", prioridade=1
    )
    mark_post_sent(conn, post)

    assert score_pista(produto, conn=conn) is None


# ---------- a foto ----------


def test_a_pista_carrega_a_foto_do_anuncio():
    """57 dos 90 primeiros posts de pista sairam sem imagem, contra 0 de 210
    posts normais. `fetch_by_ids` nao busca foto: ele confia em quem chama."""
    from promo.sources.grupo_wa import IMAGEM, produto_do_link

    html = (
        '<meta property="og:title" content="Perfume Lattafa 100ml">'
        '<meta property="og:image" content="https://http2.mlstatic.com/D_1-F.jpg">'
        '{"item_id":"MLB60687768"}'
    )

    assert IMAGEM.search(html).group(1) == "https://http2.mlstatic.com/D_1-F.jpg"
    assert produto_do_link is not None  # a tupla de tres e coberta abaixo


def test_o_anuncio_sem_og_image_nao_derruba_a_pista():
    from promo.sources.grupo_wa import IMAGEM

    assert IMAGEM.search('<meta property="og:title" content="Perfume">') is None


# ---------- perfumes diferentes na mesma rodada ----------


def test_quatro_perfumes_diferentes_saem_juntos():
    """Pedido do dono em 09/09/2026, com os titulos que sairam de verdade.

    A assinatura fina separa marca e modelo; a grossa (`CATEGORIA`) juntaria
    "lattafa perfume" com "carolina perfume" pela metade e derrubaria tres dos
    quatro.
    """
    from promo.pipeline import _uma_por_familia

    titulos = [
        "Zaad Tradicional Eau De Parfum 95ml - O Boticário",
        "Perfume Lattafa Asdaaf Ameerat Al Arab Eau De Parfum 100ml",
        "Perfume Carolina Herrera 212 Men Heroes Eau de Toilette 150ml",
        "Perfume Calvin Klein CK One 200ml",
    ]
    pistas = [
        score_pista(
            Offer(
                source="mercadolivre",
                external_id=f"MLB{i}",
                title=t,
                price=155.0,
                url=f"https://mercadolivre.com.br/MLB{i}",
            )
        )
        for i, t in enumerate(titulos)
    ]

    assert len(_uma_por_familia(pistas, 3)) == 4


def test_o_mesmo_perfume_em_dois_anuncios_sai_uma_vez():
    """O caso que a linha existe para barrar: o ML lista o mesmo produto sob
    dezenas de vendedores, com `product_id` diferente e titulo quase igual."""
    from promo.pipeline import _uma_por_familia

    pistas = [
        score_pista(
            Offer(
                source="mercadolivre",
                external_id=f"MLB{i}",
                title=t,
                price=155.0,
                url=f"https://mercadolivre.com.br/MLB{i}",
            )
        )
        for i, t in enumerate(
            [
                "Perfume Calvin Klein CK One 200ml",
                "Perfume Calvin Klein CK One 200ml Original Lacrado",
            ]
        )
    ]

    assert len(_uma_por_familia(pistas, 3)) == 1


# ---------- a foto que a carteira ja tinha ----------


def test_reconsulta_sem_foto_nao_apaga_a_foto_do_banco():
    """A pista chegou sem imagem por um dia e zerou produto que ja tinha foto.

    Medido em 09/09/2026: cinco produtos com post antigo ilustrado ficaram sem
    `image_url` na carteira. O estrago passa do caminho da pista -- o proximo
    post desses produtos sairia so com texto mesmo vindo pela vitrine.
    """
    from promo.db import record_offer

    conn = banco()
    com_foto = Offer(
        source="mercadolivre",
        external_id="MLB1",
        title="Whey Protein 1kg",
        price=78.99,
        url="https://mercadolivre.com.br/MLB1",
        image_url="https://http2.mlstatic.com/D_NQ_NP_608162-F.jpg",
    )
    record_offer(conn, com_foto)

    from dataclasses import replace

    record_offer(conn, replace(com_foto, image_url=None, price=80.0))

    guardada = conn.execute("SELECT image_url FROM products").fetchone()[0]
    assert guardada == "https://http2.mlstatic.com/D_NQ_NP_608162-F.jpg"


def test_foto_nova_substitui_a_antiga():
    """Nao virou coluna imutavel: quando ha foto nova, ela vale."""
    from dataclasses import replace

    from promo.db import record_offer

    conn = banco()
    original = Offer(
        source="mercadolivre",
        external_id="MLB1",
        title="Whey Protein 1kg",
        price=78.99,
        url="https://mercadolivre.com.br/MLB1",
        image_url="https://http2.mlstatic.com/velha.jpg",
    )
    record_offer(conn, original)
    record_offer(conn, replace(original, image_url="https://http2.mlstatic.com/nova.jpg"))

    assert (
        conn.execute("SELECT image_url FROM products").fetchone()[0]
        == "https://http2.mlstatic.com/nova.jpg"
    )


def test_string_vazia_conta_como_sem_foto():
    """`p.imagem or None` manda None, mas outra fonte pode mandar ''."""
    from dataclasses import replace

    from promo.db import record_offer

    conn = banco()
    original = Offer(
        source="mercadolivre",
        external_id="MLB1",
        title="Whey Protein 1kg",
        price=78.99,
        url="https://mercadolivre.com.br/MLB1",
        image_url="https://http2.mlstatic.com/velha.jpg",
    )
    record_offer(conn, original)
    record_offer(conn, replace(original, image_url=""))

    assert (
        conn.execute("SELECT image_url FROM products").fetchone()[0]
        == "https://http2.mlstatic.com/velha.jpg"
    )


# ---------- o retry da midia ----------


def test_a_espera_cresce_entre_as_tentativas():
    """Retry imediato nao ajuda contra `getaddrinfo EAI_AGAIN`.

    Em 09/09/2026 as duas tentativas cairam em 5 segundos, no mesmo resolver
    ainda indisponivel, e o post saiu sem foto.
    """
    from promo.delivery.evolution import ESPERAS_DA_MIDIA

    assert len(ESPERAS_DA_MIDIA) == 3
    assert ESPERAS_DA_MIDIA[-1] == 0.0  # nao espera depois da ultima
    assert ESPERAS_DA_MIDIA[0] < ESPERAS_DA_MIDIA[1]

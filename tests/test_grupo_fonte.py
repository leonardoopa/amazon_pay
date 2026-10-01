"""Outro grupo como fonte de DESCOBERTA -- a pista, nunca o post.

Pedido em 09/09/2026: aproveitar o que o "xet das promocoes | 39" (990
membros) publica. O pedido original era copiar o post e trocar o link.

Nao e o que este codigo faz, e a diferenca nao e so etica. Copiar o texto
pegaria a redacao de quem escreveu e a comissao de quem achou a oferta. E
entregaria post PIOR: viria no formato deles, sem a nossa medicao de baseline,
sem os pisos de desconto, sem o cooldown que impede repeticao.

O que se aproveita e a PISTA -- qual produto esta em oferta --, que e um fato
e nao tem dono. O produto entra na carteira e passa pelo funil inteiro.

O link deles e o mesmo formato que o nosso Link Builder gera: `meli.la/XXXX`
redireciona para `/social/<nickname>` e o ID do anuncio NAO aparece na URL --
mora no corpo, em `item_id`. Medido ao vivo em 09/09/2026: 50 mensagens, 43
links distintos, 8 de 8 resolvidos corretamente (Osklen, Puma, Calvin Klein,
Lattafa, Samsung).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.grupo_wa import (  # noqa: E402
    ITEM_ID,
    Pista,
    _texto_da_mensagem,
    links_das_mensagens,
)


def msg(**tipos) -> dict:
    return {"message": tipos}


# ---------- de onde o texto vem ----------


def test_le_mensagem_de_texto_simples():
    assert _texto_da_mensagem(msg(conversation="oferta boa")) == "oferta boa"


def test_le_legenda_de_imagem():
    """Post de oferta chega como legenda de foto na maioria das vezes -- ler
    so `conversation` perderia quase tudo."""
    m = msg(imageMessage={"caption": "PRECO ABSURDO https://meli.la/1x"})

    assert "meli.la/1x" in _texto_da_mensagem(m)


def test_le_texto_estendido():
    m = msg(extendedTextMessage={"text": "com link https://meli.la/2y"})

    assert "meli.la/2y" in _texto_da_mensagem(m)


def test_le_legenda_de_video():
    m = msg(videoMessage={"caption": "video https://meli.la/3z"})

    assert "meli.la/3z" in _texto_da_mensagem(m)


def test_mensagem_sem_texto_nao_estoura():
    assert _texto_da_mensagem({}) == ""
    assert _texto_da_mensagem(msg(audioMessage={"seconds": 3})) == ""


# ---------- quais links contam ----------


def test_acha_o_encurtador_do_ml():
    achados = links_das_mensagens([msg(conversation="olha https://meli.la/1TNmSrY")])

    assert achados == ["https://meli.la/1TNmSrY"]


def test_acha_link_direto_de_produto():
    m = msg(conversation="https://produto.mercadolivre.com.br/MLB-123-tenis")

    assert links_das_mensagens([m]) == [
        "https://produto.mercadolivre.com.br/MLB-123-tenis"
    ]


def test_ignora_link_de_outra_loja():
    """Grupo concorrente posta Amazon, Shopee e Netshoes tambem. Este modulo
    so descobre no ML, que e onde o bot mede preco."""
    m = msg(conversation="https://amzn.to/abc e https://shopee.com.br/x")

    assert links_das_mensagens([m]) == []


def test_pontuacao_colada_no_fim_sai():
    """A frase termina no link, e o ponto vira parte da URL."""
    m = msg(conversation="corre https://meli.la/1TNmSrY.")

    assert links_das_mensagens([m]) == ["https://meli.la/1TNmSrY"]


def test_nao_repete_o_mesmo_link():
    """O grupo republica a mesma oferta ao longo do dia."""
    m = msg(conversation="https://meli.la/1x")

    assert links_das_mensagens([m, m, m]) == ["https://meli.la/1x"]


def test_varios_links_na_mesma_mensagem():
    m = msg(conversation="https://meli.la/1x e tambem https://meli.la/2y")

    assert len(links_das_mensagens([m])) == 2


def test_sem_mensagem_nenhuma():
    assert links_das_mensagens([]) == []


# ---------- achar o produto por tras do link ----------


def test_o_item_id_e_o_produto_da_oferta():
    """A pagina social lista a vitrine inteira do perfil -- medido, 191
    ocorrencias de MLB numa pagina cujo produto era um so. `item_id` e o unico
    campo que aponta o produto em destaque; pegar qualquer MLB do HTML levaria
    ao produto errado."""
    html = '{"outros":"MLB999999999","item_id":"MLB6857535908","x":"MLB111111111"}'

    assert ITEM_ID.search(html).group(1) == "MLB6857535908"


def test_item_id_ausente_nao_casa():
    assert ITEM_ID.search('{"id":"MLB123456789"}') is None


# ---------- a pista nao carrega o post ----------


def test_a_pista_leva_so_o_que_o_ml_publica():
    """Nada do texto nem do link deles atravessa.

    Todo campo aqui sai da pagina do produto no ML, que e publica: `titulo` e
    `imagem` do `og:title` e do `og:image`, `preco` e `preco_antes` do cartao
    do anuncio, `url` do endereco canonico dele. Nenhum vem da mensagem que
    eles escreveram, e nenhum e o link de afiliado deles.

    `origem` e so o nome do grupo, para o log.
    """
    p = Pista(external_id="MLB1", titulo="Tenis Osklen Casual", origem="xet")

    assert set(vars(p)) == {
        "external_id",
        "titulo",
        "origem",
        "imagem",
        "url",
        "preco",
        "preco_antes",
        "cupons",
    }


def test_a_pista_sem_foto_e_valida():
    """Anuncio sem `og:image` nao pode derrubar a pista: o post sai sem foto,
    como saia antes de existir este campo."""
    p = Pista(external_id="MLB1", titulo="Tenis Osklen Casual", origem="xet")

    assert p.imagem == ""


# ---------- prioridade na fila ----------


def test_produto_de_outro_grupo_vai_para_o_topo():
    """Vem antes ate de desconto muito maior: um grupo de 990 membros que
    posta ha meses acertou a curadoria antes de nos, e o produto ja provou que
    atrai clique em publico parecido."""
    from promo.models import Offer, ScoredOffer
    from promo.pipeline import _VISTOS_EM_OUTRO_GRUPO, chave_da_fila

    def pontuada(eid, desconto):
        offer = Offer(
            source="mercadolivre", external_id=eid, title="Produto " + eid,
            price=100.0, url="u",
        )
        return ScoredOffer(
            offer=offer, baseline=200.0, discount_pct=desconto,
            observations=9, lowest_ever=False,
        )

    _VISTOS_EM_OUTRO_GRUPO.clear()
    _VISTOS_EM_OUTRO_GRUPO.add("MLB_DO_XET")
    try:
        ofertas = [pontuada("MLB_NOSSO", 60.0), pontuada("MLB_DO_XET", 15.0)]
        ordem = [
            s.offer.external_id
            for s in sorted(ofertas, key=lambda s: chave_da_fila(s, [], []), reverse=True)
        ]

        assert ordem == ["MLB_DO_XET", "MLB_NOSSO"]
    finally:
        _VISTOS_EM_OUTRO_GRUPO.clear()


def test_sem_pista_a_ordem_e_a_de_antes():
    """Desligar a fonte nao pode reordenar a fila."""
    from promo.models import Offer, ScoredOffer
    from promo.pipeline import _VISTOS_EM_OUTRO_GRUPO, chave_da_fila

    _VISTOS_EM_OUTRO_GRUPO.clear()
    offer = Offer(source="mercadolivre", external_id="X", title="P", price=100.0, url="u")
    s = ScoredOffer(offer=offer, baseline=200.0, discount_pct=30.0,
                    observations=9, lowest_ever=False)

    assert chave_da_fila(s, [], [])[0] is False


# ---------- a configuracao de producao ----------


def test_o_watchlist_real_tem_os_grupos_fonte():
    """Os dois grupos que o dono pediu em 12/09/2026: xet e Economizei."""
    from promo.pipeline import load_grupos_fonte

    jids = {f["jid"] for f in load_grupos_fonte()}

    assert "120363241588284782@g.us" in jids  # xet das promocoes | 39
    assert "120363046034439841@g.us" in jids  # Economizei | 26


def test_fonte_sem_jid_fica_de_fora(tmp_path):
    import json

    from promo.pipeline import load_grupos_fonte

    caminho = tmp_path / "watchlist.json"
    caminho.write_text(
        json.dumps({"keywords": [], "grupos_fonte": [{"nome": "sem jid"}]}),
        encoding="utf-8",
    )

    assert load_grupos_fonte(caminho) == []


# ---------- a pista virando produto medido ----------


def test_a_pista_chega_medida_a_fonte_do_ml(monkeypatch, banco_em_memoria):
    """A tupla que sai daqui tem que ser a que `fetch_by_ids` desempacota.

    Este teste nasceu de um bug em producao: `collect_de_outros_grupos`
    montava `(f"{source.name}:{id}", id, None)` e `fetch_by_ids` desempacota
    `(external_id, titulo, imagem)`. Cada GET virou
    `/products/mercadolivre:MLB.../items`, todos 404, nenhum erro no log --
    "15 pista(s), 0 produto(s) medido(s)" por duas rodadas.

    Por isso a fonte aqui e a `MercadoLivre` de verdade, com transporte falso:
    uma fonte de mentira aceitaria qualquer tupla e o contrato voltaria a
    divergir sem ninguem ver.
    """
    import httpx

    from promo.config import MercadoLivreConfig
    from promo.pipeline import collect_de_outros_grupos
    from promo.sources import grupo_wa
    from promo.sources.mercadolivre import MercadoLivre

    pedidos: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pedidos.append(request.url.path)
        if request.url.path == "/products/MLB38617889":
            # A pista nao trouxe foto; o catalogo tem.
            return httpx.Response(
                200,
                json={"name": "Power Bank", "pictures": [{"url": "https://f/1.jpg"}]},
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "price": 90.0,
                        "original_price": 200.0,
                        "condition": "new",
                        "currency_id": "BRL",
                        "shipping": {"free_shipping": True},
                        "category_id": "MLB1246",
                    }
                ]
            },
        )

    ml = MercadoLivre(
        MercadoLivreConfig(
            client_id="1", client_secret="2",
            redirect_uri="https://example.com/callback", site_id="MLB",
        )
    )
    ml._client = httpx.Client(transport=httpx.MockTransport(handler))
    ml.access_token = lambda: "token"  # type: ignore[method-assign]

    monkeypatch.setattr(
        "promo.pipeline.load_grupos_fonte",
        lambda *a, **k: [{"jid": "j@g.us", "nome": "xet", "limite": 3}],
    )
    monkeypatch.setattr(
        grupo_wa,
        "pistas",
        lambda **k: [
            grupo_wa.Pista(external_id="MLB38617889", titulo="Power Bank", origem="xet")
        ],
    )

    from promo.config import EvolutionConfig

    monkeypatch.setattr(
        EvolutionConfig,
        "load",
        classmethod(
            lambda cls, *a, **k: EvolutionConfig(
                base_url="http://e", api_key="k", instance="i", group_jid="g@g.us"
            )
        ),
    )

    achados = collect_de_outros_grupos(ml)

    assert pedidos == ["/products/MLB38617889/items", "/products/MLB38617889"]
    assert [o.external_id for o in achados] == ["MLB38617889"]
    assert achados[0].title == "Power Bank"
    assert achados[0].image_url == "https://f/1.jpg"


def test_um_id_que_a_api_recusa_nao_derruba_os_outros():
    """403 num ID nao pode zerar a leva.

    A rota so aceita produto de catalogo. O que vem de outro grupo as vezes e
    ID de anuncio, e para esse a API responde 403 -- medido em 09/09/2026,
    1 de 3 pistas. Antes, esse 403 subia por `fetch_by_ids` e a rodada inteira
    voltava vazia.
    """
    import httpx

    from promo.config import MercadoLivreConfig
    from promo.sources.mercadolivre import MercadoLivre

    def handler(request: httpx.Request) -> httpx.Response:
        if "MLB_ANUNCIO" in request.url.path:
            return httpx.Response(403, json={"error": "access_denied"})
        return httpx.Response(
            200,
            json={
                "results": [
                    {"price": 90.0, "condition": "new", "currency_id": "BRL",
                     "shipping": {}, "category_id": "MLB1"}
                ]
            },
        )

    ml = MercadoLivre(
        MercadoLivreConfig(
            client_id="1", client_secret="2",
            redirect_uri="https://example.com/callback", site_id="MLB",
        )
    )
    ml._client = httpx.Client(transport=httpx.MockTransport(handler))
    ml.access_token = lambda: "token"  # type: ignore[method-assign]

    achados = ml.fetch_by_ids(
        [
            ("MLB_ANUNCIO", "Tenis", None),
            ("MLB_CATALOGO", "Barbeador", None),
        ]
    )

    assert [o.external_id for o in achados] == ["MLB_CATALOGO"]

"""Vitrine /ofertas do ML e a separacao entre repasse e oferta verificada.

A vitrine da volume desde o dia zero, mas o "de" dela e o preco riscado pela
loja -- justamente o numero que o resto do projeto existe pra nao usar como
prova. Entao os dois tipos de post coexistem, e o que os separa nao e tecnico:
e o que o texto pode afirmar.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import Rules  # noqa: E402
from promo.db import SCHEMA  # noqa: E402
from promo.models import Offer, ScoredOffer  # noqa: E402
from promo.scoring import score_campaign  # noqa: E402
from promo.sources.ml_ofertas import ParseFailed, _offer, _payload  # noqa: E402


def card(preco=100.0, anterior=200.0, titulo="Produto", id_="MLB1", url="www.x.com/p"):
    componentes = [{"type": "title", "title": {"text": titulo}}]
    if preco is not None:
        bloco = {"current_price": {"value": preco}}
        if anterior is not None:
            bloco["price_labels"] = [
                {"values": [{"key": "previous_price", "price": {"value": anterior}}]}
            ]
        componentes.append({"type": "price", "price": bloco})
    return {
        "metadata": {"id": id_, "url": url},
        "pictures": {"pictures": [{"id": "622005-MLA1"}]},
        "components": componentes,
    }


# ---------- parsing ----------


def test_extrai_preco_atual_e_riscado():
    offer = _offer(card(preco=2045.0, anterior=5369.0))
    assert offer.price == 2045.0
    assert offer.original_price == 5369.0


def test_completa_o_esquema_da_url():
    """A pagina devolve 'www.mercadolivre.com.br/...' sem https."""
    assert _offer(card()).url == "https://www.x.com/p"


def test_url_ja_completa_passa_intacta():
    assert _offer(card(url="https://www.x.com/p")).url == "https://www.x.com/p"


def test_monta_url_da_imagem_em_resolucao_cheia():
    """-F e o sufixo da resolucao cheia; o thumbnail borra no post."""
    assert _offer(card()).image_url.endswith("622005-MLA1-F.jpg")


def test_sem_preco_riscado_ainda_vira_oferta():
    """Sem o 'de' ela nao vira repasse (score_campaign recusa), mas ainda
    alimenta o historico -- que e o que a torna verificavel depois."""
    offer = _offer(card(anterior=None))
    assert offer is not None and offer.original_price is None


def test_card_sem_preco_e_descartado():
    assert _offer(card(preco=None)) is None


def test_card_sem_titulo_e_descartado():
    assert _offer(card(titulo=None)) is None


def test_layout_mudado_falha_alto():
    """Devolver lista vazia em silencio faria o grupo ficar mudo sem ninguem
    entender por que -- indistinguivel de um dia sem oferta."""
    with pytest.raises(ParseFailed, match="layout"):
        _payload("<html><body>pagina nova</body></html>")


def test_json_ilegivel_falha_alto():
    html = '<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={quebrado</script>'
    with pytest.raises(ParseFailed, match="ilegivel"):
        _payload(html)


def test_ignora_javascript_depois_do_json():
    """Depois do JSON vem mais JS na mesma tag; json.loads reclamaria."""
    html = '<script id="__NORDIC_RENDERING_CTX__">_n.ctx.r={"a":1};_n.ctx.x=2</script>'
    assert _payload(html) == {"a": 1}


# ---------- repasse x verificada ----------


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def rules(min_discount=15.0) -> Rules:
    return Rules(
        min_discount_pct=min_discount,
        baseline_window_days=60,
        min_observations=3,
        repost_cooldown_days=14,
        max_offers_per_run=10,
    )


def vitrine_offer(price=100.0, original=200.0) -> Offer:
    return Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title="P",
        price=price,
        url="https://x",
        original_price=original,
    )


def test_repasse_sai_marcado_como_nao_verificado():
    scored = score_campaign(make_conn(), vitrine_offer(), rules())
    assert scored.verified is False
    assert scored.observations == 0


def test_repasse_usa_o_riscado_da_loja_como_baseline():
    scored = score_campaign(make_conn(), vitrine_offer(100.0, 200.0), rules())
    assert scored.baseline == 200.0
    assert scored.discount_pct == 50.0


def test_repasse_nunca_afirma_menor_preco():
    """lowest_ever exige historico. Sem medicao, a afirmacao seria invencao."""
    assert score_campaign(make_conn(), vitrine_offer(), rules()).lowest_ever is False


def test_sem_riscado_nao_vira_repasse():
    offer = vitrine_offer(original=None)
    assert score_campaign(make_conn(), offer, rules()) is None


def test_desconto_pequeno_e_recusado():
    offer = vitrine_offer(price=95.0, original=100.0)  # 5%
    assert score_campaign(make_conn(), offer, rules(min_discount=15)) is None


def test_repasse_respeita_o_cooldown():
    """Repetir cansa o grupo igual, venha da vitrine ou da nossa medicao."""
    from promo.db import create_post, record_offer

    conn = make_conn()
    offer = vitrine_offer()
    record_offer(conn, offer)
    create_post(conn, offer.product_id, 100.0, 200.0, 50.0, "texto", None)

    assert score_campaign(conn, offer, rules()) is None


# ---------- guarda de linguagem ----------


def test_repasse_nao_pode_dizer_que_acompanha(monkeypatch):
    """Repassar a vitrine dizendo 'acompanhamos ha dias' e mentir sobre o
    proprio metodo -- e o metodo e o unico ativo do grupo."""
    from types import SimpleNamespace

    from promo.copywriter import Copywriter

    texto = (
        "PRECO BOM\n\nProduto\n\nDe R$ 200,00 por *R$ 100,00*\n"
        "📊 Acompanhamos ha 12 dias\n\nlink"
    )

    class Fake:
        def __init__(self, t):
            self._t = t

        @property
        def models(self):
            return self

        def generate_content(self, **kw):
            return SimpleNamespace(text=self._t, candidates=[])

    scored = ScoredOffer(
        offer=vitrine_offer(),
        baseline=200.0,
        discount_pct=50.0,
        observations=0,
        lowest_ever=False,
        verified=False,
    )

    with pytest.raises(RuntimeError, match="acompanhamento"):
        Copywriter(client=Fake(texto)).write(scored, "https://meli.la/x")


def test_verificada_pode_dizer_que_acompanha():
    from types import SimpleNamespace

    from promo.copywriter import Copywriter

    texto = (
        "PRECO BOM\n\nProduto\n\nDe R$ 200,00 por *R$ 100,00*\n"
        "📊 Acompanhamos ha 12 dias\n\nlink"
    )

    class Fake:
        def __init__(self, t):
            self._t = t

        @property
        def models(self):
            return self

        def generate_content(self, **kw):
            return SimpleNamespace(text=self._t, candidates=[])

    offer = Offer(
        source="mercadolivre",
        external_id="MLB1",
        title="P",
        price=100.0,
        url="https://x",
    )
    scored = ScoredOffer(
        offer=offer,
        baseline=200.0,
        discount_pct=50.0,
        observations=12,
        lowest_ever=True,
        verified=True,
    )

    assert "Acompanhamos" in Copywriter(client=Fake(texto)).write(
        scored, "https://meli.la/x"
    )


# ---------- link de afiliado compartilhado ----------
#
# A vitrine tinha copia propria de affiliate_url. A copia GRAVAVA
# affiliate_blocked mas nunca LIA -- entao produto que o programa recusa
# voltava ao painel em toda rodada, pra sempre. Agora as duas fontes chamam a
# mesma funcao.


class BuilderContando:
    def __init__(self):
        self.chamadas = 0

    def create(self, urls):
        from promo.sources.ml_linkbuilder import LinkResult

        self.chamadas += 1
        return LinkResult(
            {}, {u: "URL not allowed in affiliates program" for u in urls}
        )


def test_recusado_nao_volta_ao_painel(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    from promo.db import connect, init_db, record_offer
    from promo.sources.ml_ofertas import MLOfertas

    init_db()
    offer = Offer(
        source="ml_ofertas",
        external_id="MLB1",
        title="P",
        price=10.0,
        url="https://x",
        original_price=20.0,
    )
    with connect() as conn:
        record_offer(conn, offer)

    builder = BuilderContando()
    fonte = MLOfertas(builder)

    for _ in range(5):
        assert fonte.affiliate_url(offer) is None

    assert builder.chamadas == 1


def test_as_duas_fontes_usam_a_mesma_funcao():
    """Duplicar essa logica ja custou dinheiro uma vez."""
    import inspect

    from promo.sources.ml_ofertas import MLOfertas
    from promo.sources.mercadolivre import MercadoLivre

    for classe in (MLOfertas, MercadoLivre):
        assert "resolve_affiliate_link" in inspect.getsource(classe.affiliate_url)


def test_repasse_chega_ao_banco_como_nao_verificado(tmp_path, monkeypatch):
    """A marca precisa sobreviver ao INSERT. Antes ela existia so no
    `ScoredOffer` e o site lia repasse da vitrine como prova medida."""
    import sqlite3

    from promo import pipeline
    from promo.db import SCHEMA

    caminho = tmp_path / "t.db"
    monkeypatch.setenv("DB_PATH", str(caminho))
    conn = sqlite3.connect(caminho)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO products (id, source, external_id, title, url, first_seen_at,"
        " last_seen_at) VALUES ('ml_ofertas:MLB1','ml_ofertas','MLB1','P','u','x','x')"
    )
    conn.commit()
    conn.close()

    repasse = score_campaign(make_conn(), vitrine_offer(), rules())
    monkeypatch.setattr(pipeline, "flush_pending", lambda: None)
    pipeline.deliver([(repasse, "texto do repasse")])

    conn = sqlite3.connect(caminho)
    conn.row_factory = sqlite3.Row
    linha = conn.execute("SELECT verified, baseline FROM posts").fetchone()
    conn.close()
    assert linha["verified"] == 0
    assert linha["baseline"] == 200.0

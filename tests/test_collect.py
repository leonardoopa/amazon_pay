"""A coleta precisa acompanhar produto que saiu do ranking de busca.

O filtro exige MIN_OBSERVATIONS dias distintos do mesmo produto. Se o
historico so avancasse enquanto ele estivesse no top 50 da busca, quase nada
amadureceria -- e justamente os que entram em promocao sao os que oscilam.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.db import SCHEMA, now, record_offer, tracked_products  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.pipeline import refetch_tracked  # noqa: E402
from promo.sources.mercadolivre import SITE_HOST, _best_image  # noqa: E402


def make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def make_offer(external_id: str, price: float = 100.0) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id=external_id,
        title="Produto",
        price=price,
        url="https://produto.mercadolivre.com.br/x",
    )


def seen_at(conn: sqlite3.Connection, external_id: str, days_ago: int) -> None:
    """Reescreve o last_seen_at pra simular produto que sumiu ha N dias."""
    stamp = (now() - timedelta(days=days_ago)).isoformat()
    conn.execute(
        "UPDATE products SET last_seen_at = ? WHERE id = ?",
        (stamp, f"mercadolivre:{external_id}"),
    )


class FakeSource:
    """Fonte que reconsulta por ID, pra checar o que o pipeline manda."""

    name = "mercadolivre"

    def __init__(self, fail: bool = False) -> None:
        self.asked: list[tuple[str, str, str | None]] = []
        self.fail = fail

    def fetch_by_ids(self, tracked: list[tuple[str, str, str | None]]) -> list[Offer]:
        self.asked = list(tracked)
        if self.fail:
            raise RuntimeError("500 do ML")
        return [make_offer(external_id) for external_id, _, _ in tracked]


class SourceSemMultiget:
    name = "amazon"


class Rules:
    baseline_window_days = 60


def ids(rows: list[tuple[str, str, str | None]]) -> list[str]:
    return [row[0] for row in rows]


# ---------- tracked_products ----------


def test_devolve_produto_conhecido():
    conn = make_conn()
    record_offer(conn, make_offer("MLB1"))
    assert ids(tracked_products(conn, "mercadolivre", 60, 400)) == ["MLB1"]


def test_traz_titulo_e_imagem_para_evitar_chamada_extra():
    """A reconsulta so precisa do preco; nome e foto vem do banco."""
    conn = make_conn()
    offer = Offer(
        source="mercadolivre",
        external_id="MLB1",
        title="Celular Samsung",
        price=100.0,
        url="https://www.mercadolivre.com.br/p/MLB1",
        image_url="https://http2.mlstatic.com/foto-F.jpg",
    )
    record_offer(conn, offer)

    assert tracked_products(conn, "mercadolivre", 60, 400) == [
        ("MLB1", "Celular Samsung", "https://http2.mlstatic.com/foto-F.jpg")
    ]


def test_ignora_produto_fora_da_janela():
    """Historico mais velho que a janela da baseline nao serve pra nada."""
    conn = make_conn()
    record_offer(conn, make_offer("MLB1"))
    seen_at(conn, "MLB1", days_ago=90)
    assert tracked_products(conn, "mercadolivre", 60, 400) == []


def test_mantem_produto_na_borda_da_janela():
    conn = make_conn()
    record_offer(conn, make_offer("MLB1"))
    seen_at(conn, "MLB1", days_ago=59)
    assert ids(tracked_products(conn, "mercadolivre", 60, 400)) == ["MLB1"]


def test_nao_mistura_fontes():
    conn = make_conn()
    record_offer(conn, make_offer("MLB1"))
    amazon = Offer(
        source="amazon",
        external_id="B0XYZ",
        title="Produto",
        price=50.0,
        url="https://amazon.com.br/x",
    )
    record_offer(conn, amazon)
    assert ids(tracked_products(conn, "mercadolivre", 60, 400)) == ["MLB1"]


def test_respeita_o_teto():
    """O teto e literal: cada produto custa uma chamada de API."""
    conn = make_conn()
    for index in range(10):
        record_offer(conn, make_offer(f"MLB{index}"))
    assert len(tracked_products(conn, "mercadolivre", 60, 3)) == 3


# ---------- refetch_tracked ----------


def test_reconsulta_produto_que_saiu_da_busca(monkeypatch):
    """O caso que motiva tudo: conhecido no banco, ausente do ranking hoje."""
    conn = make_conn()
    record_offer(conn, make_offer("MLB1"))
    monkeypatch.setattr("promo.pipeline.connect", lambda: _as_ctx(conn))

    source = FakeSource()
    offers = refetch_tracked(source, Rules())

    assert ids(source.asked) == ["MLB1"]
    assert [o.external_id for o in offers] == ["MLB1"]


def test_fonte_sem_multiget_e_ignorada():
    assert refetch_tracked(SourceSemMultiget(), Rules()) == []


def test_banco_vazio_nao_chama_a_api(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr("promo.pipeline.connect", lambda: _as_ctx(conn))

    source = FakeSource()
    assert refetch_tracked(source, Rules()) == []
    assert source.asked == []


def test_erro_na_reconsulta_nao_derruba_a_rodada(monkeypatch):
    conn = make_conn()
    record_offer(conn, make_offer("MLB1"))
    monkeypatch.setattr("promo.pipeline.connect", lambda: _as_ctx(conn))

    assert refetch_tracked(FakeSource(fail=True), Rules()) == []


# ---------- imagem ----------


def test_prefere_as_pictures_do_catalogo():
    """O catalogo entrega -F (~39 KB); o thumbnail tem 1,7 KB."""
    produto = {
        "thumbnail": "https://http2.mlstatic.com/D_NQ_NP_1-I.jpg",
        "pictures": [{"url": "https://http2.mlstatic.com/D_NQ_NP_1-F.jpg"}],
    }
    assert _best_image(produto) == "https://http2.mlstatic.com/D_NQ_NP_1-F.jpg"


def test_promove_o_thumbnail_da_busca_para_resolucao_cheia():
    """Sem `pictures` (caso da busca), a CDN serve a versao grande em -O."""
    item = {"thumbnail": "https://http2.mlstatic.com/D_NQ_NP_1-I.jpg"}
    assert _best_image(item) == "https://http2.mlstatic.com/D_NQ_NP_1-O.jpg"


def test_thumbnail_fora_do_padrao_passa_intacto():
    """Nao inventa sufixo em URL que nao segue a convencao -I."""
    item = {"thumbnail": "https://exemplo.com/foto.png"}
    assert _best_image(item) == "https://exemplo.com/foto.png"


def test_sem_imagem_nenhuma():
    assert _best_image({}) is None


def test_url_do_produto_e_montada_do_id():
    """O catalogo devolve permalink vazio, entao a URL sai do ID."""
    assert SITE_HOST == "https://www.mercadolivre.com.br"


class _as_ctx:
    """Deixa a conexao de teste passar pelo `with connect() as conn`."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, *args: object) -> None:
        self.conn.commit()

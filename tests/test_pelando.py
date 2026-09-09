"""Cupons do ML lidos do Pelando.

Por que nao o painel do proprio ML: ele so mostra o cupom DEPOIS que o dono
clica em ativar. O bot enxergaria apenas o que ja foi ativado a mao, e a
leitura automatica seria falsa. Medido em 08/09/2026, o painel dava 1 cupom
publicavel; o Pelando, 8.

A via e o sitemap que o site publica para crawlers -- /sitemaps/coupons_recent.xml
--, e nao raspagem de listagem. O robots.txt libera bot generico e os termos
nao proibem leitura automatizada; o Promobit, por comparacao, proibe em
clausula expressa.

Metade do trabalho e de graca, porque a regra vem no proprio endereco:

    cupom-mercado-livre-10porcento-off-acima-de-rdollar79-limitado-a-rdollar30
              -> 10%, minimo R$ 79, teto R$ 30

O que so a pagina diz e o CODIGO, e ele exige cuidado: a lateral lista outros
cupons e todos aparecem em `data-copy-code` -- os mesmos 8 codigos em 4 paginas
diferentes. O da oferta e outro elemento, `coupon-code-copiable`, que ainda
carrega um `data-inactive` dizendo se o cupom parou de funcionar.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.pelando import (  # noqa: E402
    CupomPelando,
    codigo_da_pagina,
    regra_do_slug,
)

BASE = "https://www.pelando.com.br/d/"


# ---------- a regra que vem no endereco ----------


def test_le_desconto_minimo_e_teto_do_slug():
    """A URL real que o dono mandou."""
    regra = regra_do_slug(
        BASE + "cupom-mercado-livre-10porcento-off-acima-de-rdollar79-"
        "limitado-a-rdollar30-08-set-2026-49d2"
    )

    assert regra["desconto"] == 10.0
    assert regra["tipo"] == "PERCENT"
    assert regra["minimo"] == 79.0
    assert regra["teto"] == 30.0
    assert regra["postado_em"] == "2026-09-08"


def test_le_cupom_de_valor_fixo():
    regra = regra_do_slug(
        BASE + "cupom-mercado-livre-rdollar200-off-em-compras-acima-de-"
        "rdollar1599-em-selecionados-cell-pc-e-tech-08-set-2026-b7e5"
    )

    assert (regra["desconto"], regra["tipo"]) == (200.0, "FIXED")
    assert regra["minimo"] == 1599.0


def test_slug_sem_desconto_nao_vira_cupom():
    """Sem numero nao da para escrever o post, e chutar seria mentir."""
    regra = regra_do_slug(BASE + "cupom-mercado-livre-promocao-boa-08-set-2026-aaaa")

    assert "desconto" not in regra


def test_mes_desconhecido_nao_vira_data():
    regra = regra_do_slug(BASE + "cupom-10porcento-off-08-xyz-2026-aaaa")

    assert "postado_em" not in regra


# ---------- onde o cupom vale ----------


def test_sem_recorte_o_cupom_vale_no_site_todo():
    regra = regra_do_slug(
        BASE + "cupom-mercado-livre-oferece-rdollar-60-off-em-rdollar-499-08-set-2026-ade5"
    )

    assert regra["restrito"] is False
    assert regra["temas"] == []


def test_em_selecionados_sem_dizer_quais_e_restrito():
    """46 dos 121 slugs do ML sao assim. Publicar como se valesse para tudo e
    o erro que o cupom deveria evitar: a pessoa clica, tenta e falha."""
    regra = regra_do_slug(
        BASE + "cupom-mercado-livre-22porcento-off-em-compras-acima-de-rdollar1-"
        "limitado-a-rdollar500-em-selecionados-08-set-2026-26a5"
    )

    assert regra["restrito"] is True
    assert regra["temas"] == []


def test_qualificador_conhecido_vira_tema():
    regra = regra_do_slug(
        BASE + "cupom-mercado-livre-20porcento-off-acima-de-rdollar89-limitado-a-"
        "rdollar30-em-selecionados-moda-e-beleza-08-set-2026-056e"
    )

    assert regra["restrito"] is False
    assert "camiseta" in regra["temas"]
    assert "perfume" in regra["temas"]


def test_qualificador_desconhecido_continua_restrito():
    """Recorte que nao da para traduzir e recorte que nao da para prometer."""
    regra = regra_do_slug(
        BASE + "cupom-30porcento-off-em-selecionados-jardinagem-e-piscina-08-set-2026-a1"
    )

    assert regra["restrito"] is True


def test_full_nao_vira_tema():
    """FULL e o deposito do ML, nao categoria de produto. Viraria um tema que
    nao casa com titulo nenhum, e o cupom sumiria em silencio."""
    regra = regra_do_slug(
        BASE + "cupom-mercado-livre-30porcento-off-acima-de-rdollar99-limitado-a-"
        "rdollar20-em-selecionados-full-08-set-2026-1edf"
    )

    assert regra["temas"] == []
    assert regra["restrito"] is True


# ---------- o codigo da pagina ----------


def html_com_codigo(code: str, inativo: str = "false") -> str:
    return (
        '<div class="coupon-code-copiable" data-inactive="%s" data-astro-cid-x>'
        '<span class="code" data-astro-cid-y>%s</span>'
        '<button data-code="%s" class="copy">Copiar</button></div>' % (inativo, code, code)
    )


def test_acha_o_codigo_da_oferta():
    assert codigo_da_pagina(html_com_codigo("TORCIDA")) == ("TORCIDA", True, False)


def test_cupom_marcado_inativo_vem_como_inativo():
    """O `data-inactive` e a unica validacao que esta fonte oferece: o site
    marca quando a comunidade reporta que parou de funcionar."""
    assert codigo_da_pagina(html_com_codigo("VELHO", inativo="true")) == (
        "VELHO",
        False,
        False,
    )


def test_ignora_os_codigos_da_lateral():
    """`data-copy-code` traz os cupons relacionados -- os mesmos 8 apareceram
    em 4 paginas diferentes. Confundir publicaria o cupom errado com a regra
    certa."""
    html = (
        '<span class="card__code-text">OUTROCUPOM</span>'
        '<button data-copy-code="OUTROCUPOM">Copiar</button>'
        + html_com_codigo("TORCIDA")
    )

    assert codigo_da_pagina(html) == ("TORCIDA", True, False)


def test_pagina_sem_codigo_devolve_none():
    assert codigo_da_pagina("<html><body>sem cupom aqui</body></html>") is None


# ---------- o formato que o pipeline consome ----------


def cupom(**extra) -> CupomPelando:
    base = dict(
        code="TORCIDA",
        postado_em="2026-09-08",
        desconto=10.0,
        tipo="PERCENT",
        minimo=79.0,
        teto=30.0,
        url=BASE + "x",
        temas=(),
    )
    base.update(extra)
    return CupomPelando(**base)


def test_validade_e_estimada_da_postagem():
    """O Pelando nao publica validade -- a data do slug e quando o usuario
    postou. A janela curta e a defesa: o cupom sai de circulacao sozinho em
    vez de ser publicado para sempre com base num palpite."""
    d = cupom().como_dict(validade_dias=3)

    assert d["ate"] == "2026-09-11"


def test_o_dict_fala_o_dialeto_do_watchlist():
    d = cupom().como_dict(3)

    assert set(d) >= {"code", "ate", "minimo", "temas", "categorias"}
    assert d["categorias"] == []
    assert d["fonte"] == "pelando"


def test_temas_do_qualificador_chegam_no_dict():
    d = cupom(temas=("camiseta", "perfume")).como_dict(3)

    assert d["temas"] == ["camiseta", "perfume"]


def test_cupom_de_hoje_ainda_esta_no_prazo():
    """Guarda contra erro de sinal na conta da validade."""
    hoje = date.today().isoformat()

    d = cupom(postado_em=hoje).como_dict(3)

    assert date.fromisoformat(d["ate"]) > date.today()


def test_cupom_postado_ha_muito_tempo_ja_vence():
    velho = (date.today() - timedelta(days=10)).isoformat()

    d = cupom(postado_em=velho).como_dict(3)

    assert date.fromisoformat(d["ate"]) < date.today()


def test_le_o_recorte_do_corpo_da_pagina():
    """Medido em 09/09/2026: o TORCIDA tinha slug limpo
    ("10porcento-off-acima-de-rdollar79") e a restricao so no corpo --
    "Publicado por reginaoliveira73 2 h Em itens Selecionados". O post saiu
    no grupo com o cupom num whey onde ele nao aplicava, e funcionou num
    pre-treino e numa progressiva onde aplicava, que e o pior dos mundos:
    parece aleatorio para quem le."""
    html = html_com_codigo("TORCIDA") + "<span>Em itens Selecionados</span>"

    assert codigo_da_pagina(html) == ("TORCIDA", True, True)


def test_o_recorte_do_corpo_nao_descarta_o_cupom():
    """Ele funciona em parte do catalogo: descartar tiraria os posts que deram
    certo. O que o recorte proibe e mexer no PRECO anunciado."""
    cupom_restrito = cupom(restrito=True).como_dict(3)

    assert cupom_restrito["code"] == "TORCIDA"
    assert cupom_restrito["restrito"] is True


def test_cupom_sem_recorte_nao_vem_marcado():
    assert cupom().como_dict(3)["restrito"] is False

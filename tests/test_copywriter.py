"""Garante que os fatos passados ao Gemini batem com o que foi calculado."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.copywriter import (  # noqa: E402
    SYSTEM,
    Copywriter,
    _enforce_disclosure,
    _facts,
    fallback_copy,
)
from promo.models import Offer, ScoredOffer  # noqa: E402

LINK = "https://produto.mercadolivre.com.br/MLB-123?matt_tool=1"


AMAZON_DISCLOSURE = (
    "Como participante do Programa de Associados da Amazon, "
    "sou remunerado pelas compras qualificadas efetuadas"
)


def make_scored(**kwargs) -> ScoredOffer:
    offer = Offer(
        source=kwargs.pop("source", "mercadolivre"),
        external_id="MLB123",
        title="Fone Bluetooth XYZ",
        price=150.0,
        url="https://produto.mercadolivre.com.br/MLB-123",
        free_shipping=kwargs.pop("free_shipping", False),
    )
    defaults = dict(
        baseline=200.0, discount_pct=25.0, observations=10, lowest_ever=False
    )
    return ScoredOffer(offer=offer, **{**defaults, **kwargs})


class FakeModels:
    def __init__(self, text: str) -> None:
        self.text = text
        self.finish_reason = "STOP"
        self.calls: list[dict] = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            text=self.text,
            candidates=[SimpleNamespace(finish_reason=self.finish_reason)],
        )


class FakeClient:
    def __init__(self, text: str) -> None:
        self.models = FakeModels(text)


def test_facts_carregam_os_numeros_reais():
    facts = _facts(make_scored(), LINK)

    assert "R$ 150,00" in facts
    assert "R$ 200,00" in facts
    assert "25%" in facts
    assert LINK in facts
    assert "Mercado Livre" in facts


def test_facts_sinalizam_menor_preco_e_frete():
    facts = _facts(make_scored(lowest_ever=True, free_shipping=True), LINK)

    assert "menor preco" in facts
    assert "Frete gratis." in facts


def test_facts_omitem_flags_quando_falsas():
    facts = _facts(make_scored(), LINK)

    assert "menor preco" not in facts
    assert "Frete gratis." not in facts


def test_write_envia_system_instruction_e_devolve_texto():
    client = FakeClient("  Achei um bom preco\nFone XYZ  ")
    copy = Copywriter(client=client).write(make_scored(), LINK)

    assert copy.startswith("Achei um bom preco\nFone XYZ")
    call = client.models.calls[0]
    assert call["config"].system_instruction == SYSTEM
    assert LINK in call["contents"]


def test_write_recusa_resposta_truncada():
    """Modelo com thinking estoura o orcamento e devolve post sem preco/link."""
    client = FakeClient("Que achado esse fone! 🎧\nJBL Tune 52")
    client.models.finish_reason = "MAX_TOKENS"

    with pytest.raises(RuntimeError, match="truncou"):
        Copywriter(client=client).write(make_scored(), LINK)


def test_write_recusa_resposta_vazia():
    """Filtro de seguranca do Gemini pode zerar a resposta -- nao pode virar post."""
    copywriter = Copywriter(client=FakeClient(""))

    with pytest.raises(RuntimeError, match="vazia"):
        copywriter.write(make_scored(), LINK)


def test_fallback_tem_disclosure_e_numeros():
    """O fallback segue o mesmo formato De/Por do post principal.

    Sem porcentagem de proposito: o "De R$ 200,00 por R$ 150,00" ja mostra a
    queda, e o formato pedido pro grupo nao usa percentual.
    """
    text = fallback_copy(make_scored(lowest_ever=True), LINK)

    assert "De R$ 200,00 por *R$ 150,00*" in text
    assert LINK in text
    assert text.endswith("Link de afiliado - o preco pra voce nao muda.")


def test_recusa_loja_oficial_inventada():
    """Observado na pratica: com official_store=False, e sem o fato no prompt,
    o post saiu com 'Loja oficial no ML'. Selo do ML nao e adjetivo."""
    texto = "PRECO BOM\n\nProduto\n\nDe R$ 200,00 por *R$ 150,00*\n\nLoja oficial no ML\nlink"
    copywriter = Copywriter(client=FakeClient(texto))

    with pytest.raises(RuntimeError, match="loja oficial"):
        copywriter.write(make_scored(), LINK)


def test_aceita_loja_oficial_quando_e_verdade():
    texto = "PRECO BOM\n\nProduto\n\nDe R$ 200,00 por *R$ 150,00*\n\nLoja oficial no ML\nlink"
    scored = make_scored()
    scored = replace(scored, offer=replace(scored.offer, official_store=True))

    assert "Loja oficial" in Copywriter(client=FakeClient(texto)).write(scored, LINK)


def test_recusa_instrucao_do_prompt_vazada():
    """Aconteceu: saiu a linha literal 'o link sozinho numa linha' no post."""
    texto = "PRECO BOM\n\nProduto\n\nDe R$ 200,00 por *R$ 150,00*\n\no link sozinho numa linha\nlink"
    copywriter = Copywriter(client=FakeClient(texto))

    with pytest.raises(RuntimeError, match="vazou"):
        copywriter.write(make_scored(), LINK)


def test_fallback_inclui_cupom_quando_existe():
    text = fallback_copy(make_scored(), LINK, coupon="MELIDATADUPLA")
    assert "Use o cupom: MELIDATADUPLA" in text


def test_fallback_sem_cupom_nao_inventa_linha():
    """Linha de cupom vazia e pior que ausente: a pessoa procura o codigo."""
    assert "cupom" not in fallback_copy(make_scored(), LINK).lower()


# --- Divulgacao obrigatoria (Clausula 5 do Contrato de Associados) ---
#
# A frase da Amazon e literal por contrato, e violar a Clausula 5 conta como
# descumprimento material. Por isso ela e testada caractere por caractere.


def test_amazon_usa_a_frase_exata_do_contrato():
    text = fallback_copy(make_scored(source="amazon"), LINK)

    assert text.endswith(AMAZON_DISCLOSURE)


def test_facts_mandam_a_divulgacao_da_loja_certa():
    assert AMAZON_DISCLOSURE in _facts(make_scored(source="amazon"), LINK)
    assert AMAZON_DISCLOSURE not in _facts(make_scored(), LINK)


def test_disclosure_e_reposta_se_o_modelo_parafrasear():
    """LLM nao e confiavel para reproduzir texto legal -- o codigo garante."""
    client = FakeClient(
        "Achei um bom preco\nRelogio Seiko\nsou remunerado pelas compras"
    )
    copy = Copywriter(client=client).write(make_scored(source="amazon"), LINK)

    assert copy.endswith(AMAZON_DISCLOSURE)


def test_disclosure_nao_e_duplicada_quando_o_modelo_acerta():
    client = FakeClient(
        f"Achei um bom preco\nRelogio Seiko\n{LINK}\n{AMAZON_DISCLOSURE}"
    )
    copy = Copywriter(client=client).write(make_scored(source="amazon"), LINK)

    assert copy.count(AMAZON_DISCLOSURE) == 1


# ---------- formato de moeda ----------
#
# O modelo copia os numeros do prompt caractere por caractere -- e e isso que
# impede ele de inventar preco. O efeito colateral: formato errado no prompt
# vira formato errado no post. Um "R$ 27.00" num grupo brasileiro le como erro.


def test_formata_em_padrao_brasileiro():
    from promo.copywriter import brl

    assert brl(27.0) == "27,00"
    assert brl(1234.5) == "1.234,50"
    assert brl(0.99) == "0,99"


def test_separador_de_milhar_em_valor_alto():
    from promo.copywriter import brl

    assert brl(1234567.89) == "1.234.567,89"


def test_nenhum_ponto_decimal_chega_ao_post():
    """A regressao concreta: o primeiro post real saiu com 'R$ 27.00'."""

    scored = make_scored()
    facts = _facts(scored, LINK)
    precos = [linha for linha in facts.splitlines() if "R$" in linha]

    assert precos
    for linha in precos:
        valor = linha.split("R$ ")[1].split()[0]
        assert "." not in valor.rsplit(",", 1)[-1]
        assert "," in valor


# ---------- afirmacoes sem lastro ----------
#
# Observado em producao: com lowest_ever=False, e sem o fato no prompt, o
# Gemini escreveu "(menor preco ja registrado!)". Temperatura 0.8 escrevendo
# copy de oferta puxa pro superlativo sozinho. Pedir no system prompt nao
# basta -- a trava tem que ser deterministica, igual a da divulgacao.


def test_recusa_menor_preco_inventado():
    from promo.copywriter import _reject_unfounded_claims

    scored = make_scored(lowest_ever=False)
    with pytest.raises(RuntimeError, match="menor preco"):
        _reject_unfounded_claims(
            "Que achado! *R$ 48,00*, menor preço já registrado!", scored
        )


def test_aceita_menor_preco_quando_e_verdade():
    from promo.copywriter import _reject_unfounded_claims

    scored = make_scored(lowest_ever=True)
    _reject_unfounded_claims("*R$ 48,00* — menor preço já registrado!", scored)


def test_pega_variacoes_da_afirmacao():
    from promo.copywriter import _reject_unfounded_claims

    scored = make_scored(lowest_ever=False)
    for frase in (
        "o melhor preço que já vi",
        "PREÇO MAIS BAIXO do ano",
        "nunca esteve tão barato",
        "menor preco sem acento tambem",
    ):
        with pytest.raises(RuntimeError):
            _reject_unfounded_claims(frase, scored)


def test_recusa_urgencia_inventada():
    """Nao sabemos estoque nem prazo -- qualquer urgencia e invencao."""
    from promo.copywriter import _reject_unfounded_claims

    scored = make_scored(lowest_ever=True)
    for frase in (
        "Corre que acaba!",
        "Últimas unidades",
        "so hoje",
        "Por tempo limitado",
        "estoque limitado",
    ):
        with pytest.raises(RuntimeError, match="urgencia"):
            _reject_unfounded_claims(frase, scored)


def test_texto_honesto_passa():
    from promo.copywriter import _reject_unfounded_claims

    scored = make_scored(lowest_ever=False)
    _reject_unfounded_claims(
        "Achadinho bom 🎧\nFone Redmi\n*R$ 27,00* — 32% abaixo da média de R$ 39,90\n"
        "https://meli.la/x\nLink de afiliado - o preco pra voce nao muda.",
        scored,
    )


# --- 429 de cota e falsos positivos das guardas ---
#
# O free tier do Gemini corta em 20 requisicoes por dia. Sem retry, a rajada de
# uma rodada derruba posts pro fallback -- que sai sem linha de chamada, ou
# seja, o post que o grupo ignora.


def test_le_o_retry_delay_do_erro_de_cota():
    from promo.copywriter import _retry_delay

    erro = RuntimeError(
        "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, "
        "'details': [{'retryDelay': '12.5s'}]}}"
    )
    assert _retry_delay(erro) == pytest.approx(13.5)


def test_erro_que_nao_e_cota_nao_tem_espera():
    from promo.copywriter import _retry_delay

    assert _retry_delay(RuntimeError("500 internal")) is None


def test_espera_absurda_e_recusada():
    """Cota diaria estourada devolve delay enorme; travar a rodada esperando
    seria pior que cair pro texto padrao."""
    from promo.copywriter import _retry_delay

    erro = RuntimeError("429 RESOURCE_EXHAUSTED 'retryDelay': '3600s'")
    assert _retry_delay(erro) is None


def test_cota_sem_retry_delay_nao_trava():
    from promo.copywriter import _retry_delay

    assert _retry_delay(RuntimeError("429 RESOURCE_EXHAUSTED sem detalhe")) is None


def test_produto_monitor_nao_dispara_guarda_de_acompanhamento():
    """A regex antiga casava 'monitor' e barrava todo post de monitor -- o
    produto. Guarda que rejeita post legitimo custa oferta."""
    from promo.copywriter import ACOMPANHAMENTO

    assert not ACOMPANHAMENTO.search("Monitor 27 Polegadas 60 Hrz Office Bright")
    assert not ACOMPANHAMENTO.search("Kit acompanha 2 baterias e maleta")


def test_afirmacao_de_acompanhamento_ainda_e_pega():
    from promo.copywriter import ACOMPANHAMENTO

    for frase in (
        "Acompanhamos ha 12 dias",
        "monitoramos esse preco",
        "nunca vimos tao barato",
        "estamos de olho nesse produto",
        "nossa media dos ultimos dias",
    ):
        assert ACOMPANHAMENTO.search(frase), frase


# --- Nome da loja por fonte ---
#
# Era um ternario binario escrito quando so existiam duas fontes. Com
# `ml_ofertas` e `demo`, os dois cairam no else: todo post da vitrine ia pro
# Gemini com "Loja: Amazon" ao lado de link meli.la. Nenhuma guarda checa nome
# de loja, entao sairia inteiro pro grupo.


def loja_no_prompt(source: str) -> str:
    offer = Offer(
        source=source, external_id="X", title="P", price=10.0, url="https://x"
    )
    scored = ScoredOffer(
        offer=offer,
        baseline=20.0,
        discount_pct=50.0,
        observations=0,
        lowest_ever=False,
        verified=False,
    )
    linha = [l for l in _facts(scored, LINK).splitlines() if l.startswith("Loja:")]
    return linha[0]


def test_vitrine_e_mercado_livre_nao_amazon():
    assert loja_no_prompt("ml_ofertas") == "Loja: Mercado Livre"


def test_catalogo_e_mercado_livre():
    assert loja_no_prompt("mercadolivre") == "Loja: Mercado Livre"


def test_amazon_continua_amazon():
    assert loja_no_prompt("amazon") == "Loja: Amazon"


def test_demo_nao_vira_amazon():
    """`promo demo` calibra o tom; com Amazon errada ela treinava errado."""
    assert loja_no_prompt("demo") == "Loja: Mercado Livre"


def test_fonte_desconhecida_falha_alto():
    """Fonte nova sem cadastro tem que estourar, nao virar Amazon em silencio."""
    from promo.copywriter import store_name

    with pytest.raises(RuntimeError, match="shopee"):
        store_name("shopee")


def test_loja_bate_com_a_divulgacao():
    """Post com 'Loja: Amazon' e divulgacao do ML e incoerencia entregue ao
    modelo -- foi assim que o bug passou despercebido."""
    from promo.copywriter import disclosure_for, store_name

    for source in ("mercadolivre", "ml_ofertas", "demo"):
        assert store_name(source) == "Mercado Livre"
        assert disclosure_for(source) == "Link de afiliado - o preco pra voce nao muda."
    assert store_name("amazon") == "Amazon"
    assert "Programa de Associados" in disclosure_for("amazon")


# --- Rate limit por minuto ---
#
# Uma rodada escreve ate MAX_OFFERS_PER_RUN posts. Sem espacamento, as chamadas
# saem no mesmo segundo e tomam 429 de RPM. O retry existe, mas gasta tentativa
# e atrasa a rodada -- espacar na origem e mais barato.


def test_espaca_chamadas_consecutivas(monkeypatch):
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "5")
    dormidas = []
    monkeypatch.setattr("promo.copywriter.time.sleep", dormidas.append)

    # 1a chamada: so carimba (0.0). 2a: le 1.0 (passou 1s) e carimba de novo.
    relogio = iter([0.0, 1.0, 5.0])
    monkeypatch.setattr("promo.copywriter.time.monotonic", lambda: next(relogio))

    copywriter = Copywriter(client=FakeClient("PRECO BOM\n\nP\n\nlink"))
    copywriter.write(make_scored(), LINK)
    copywriter.write(make_scored(), LINK)

    assert dormidas == [4.0]  # 5s de intervalo, 1s ja passado


def test_primeira_chamada_nao_espera(monkeypatch):
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "5")
    dormidas = []
    monkeypatch.setattr("promo.copywriter.time.sleep", dormidas.append)
    monkeypatch.setattr("promo.copywriter.time.monotonic", lambda: 0.0)

    Copywriter(client=FakeClient("PRECO BOM\n\nP\n\nlink")).write(make_scored(), LINK)

    assert dormidas == []


def test_monotonic_zero_nao_desliga_o_limitador(monkeypatch):
    """0.0 e falsy: com o guard antigo (`if self._ultima_chamada`), um relogio
    que comeca em zero desligava o espacamento em silencio."""
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "5")
    dormidas = []
    monkeypatch.setattr("promo.copywriter.time.sleep", dormidas.append)
    monkeypatch.setattr("promo.copywriter.time.monotonic", lambda: 0.0)

    copywriter = Copywriter(client=FakeClient("PRECO BOM\n\nP\n\nlink"))
    copywriter.write(make_scored(), LINK)
    copywriter.write(make_scored(), LINK)

    assert dormidas == [5.0]


def test_intervalo_zero_desliga_o_espacamento(monkeypatch):
    """Com faturamento ativo o teto sobe muito e o espacamento so atrasa."""
    monkeypatch.setenv("GEMINI_MIN_INTERVAL_SECONDS", "0")
    dormidas = []
    monkeypatch.setattr("promo.copywriter.time.sleep", dormidas.append)

    copywriter = Copywriter(client=FakeClient("PRECO BOM\n\nP\n\nlink"))
    copywriter.write(make_scored(), LINK)
    copywriter.write(make_scored(), LINK)

    assert dormidas == []


# --- Divulgacao: exatamente uma vez, na forma canonica ---
#
# Observado com gemini-3.5-flash: escreveu "o preço pra voce" com acento; a
# comparacao literal nao achou e ANEXOU a canonica. Post saiu com a divulgacao
# duplicada, uma acentuada e outra nao -- e foi pro grupo assim.

DISCLOSURE_ML = "Link de afiliado - o preco pra voce nao muda."


def test_variante_acentuada_nao_duplica():
    texto = "POST\nlink\nLink de afiliado - o preço pra voce nao muda."
    resultado = _enforce_disclosure(texto, "mercadolivre")

    assert resultado.count("Link de afiliado") == 1
    assert resultado.endswith(DISCLOSURE_ML)


def test_variante_em_caixa_alta_nao_duplica():
    texto = "POST\nlink\nLINK DE AFILIADO - O PREÇO PRA VOCÊ NÃO MUDA."
    resultado = _enforce_disclosure(texto, "mercadolivre")

    assert resultado.count("Link de afiliado") == 1


def test_variante_e_trocada_pela_canonica():
    """Nao basta nao duplicar: a Amazon exige a frase caractere por caractere,
    entao a variante tem que virar a forma exata."""
    texto = "POST\nlink\nLink de afiliado - o preço pra voce nao muda."
    assert DISCLOSURE_ML in _enforce_disclosure(texto, "mercadolivre")


def test_duplicata_literal_e_reduzida_a_uma():
    texto = f"POST\nlink\n{DISCLOSURE_ML}\n{DISCLOSURE_ML}"
    assert _enforce_disclosure(texto, "mercadolivre").count("Link de afiliado") == 1


def test_ausente_e_acrescentada():
    assert _enforce_disclosure("POST\nlink", "mercadolivre").endswith(DISCLOSURE_ML)


def test_ja_canonica_passa_intacta():
    texto = f"POST\nlink\n{DISCLOSURE_ML}"
    assert _enforce_disclosure(texto, "mercadolivre") == texto


def test_amazon_mantem_a_frase_do_contrato():
    from promo.copywriter import DISCLOSURES

    exata = DISCLOSURES["amazon"]
    texto = "POST\nlink\nComo participante do Programa de Associados da Amazon, sou remunerado pelas compras qualificadas efetuadas"
    resultado = _enforce_disclosure(texto, "amazon")

    assert resultado.count("Programa de Associados") == 1
    assert exata in resultado


def test_nao_come_linha_parecida_do_post():
    """Linha do corpo que so lembra a divulgacao nao pode sumir."""
    texto = f"POST\nlink de afiliado abaixo\nlink\n{DISCLOSURE_ML}"
    resultado = _enforce_disclosure(texto, "mercadolivre")

    assert "link de afiliado abaixo" in resultado

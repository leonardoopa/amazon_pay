"""A busca propria de cada grupo tematico.

O grupo tematico recebe por duas vias. A primeira ja existia: o que o Geral
escolhe cai tambem nos grupos cujo tema a oferta cita. A segunda nasceu em
16/09/2026, com os grupos de Esportes, Perfumes e Casa, porque a primeira
entrega pouco -- a cota do Geral e disputada pelo catalogo inteiro e quem ganha
e o maior desconto, que raramente e perfume ou air fryer.

O que estes testes guardam e a regra que o dono escolheu no mesmo dia: o que
sai da busca propria fica SO no grupo dele. Mandar tambem para o Geral somaria
os quatro grupos no principal, que e o oposto do pedido.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.config import max_por_grupo_tematico  # noqa: E402
from promo.models import Offer  # noqa: E402
from promo.pipeline import GrupoDestino, load_grupos  # noqa: E402


def oferta(titulo: str) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="MLB1",
        title=titulo,
        price=100.0,
        url="https://produto.mercadolivre.com.br/MLB-1",
    )


def destinos(titulo: str) -> list[str]:
    alvo = oferta(titulo)
    return [
        g.nome.replace("Comunidade do Desconto - ", "")
        for g in load_grupos()
        if g.aceita(alvo)
    ]


# ---------- o roteamento dos grupos novos ----------


@pytest.mark.parametrize(
    "titulo, esperados",
    [
        ("Tenis Nike Revolution 7 Masculino Corrida Preto", ["Geral", "Esportes"]),
        ("Air Fryer Mondial 4L Family Preta", ["Geral", "Casa"]),
        ("Smart Tv Samsung 50 Polegadas 4K Crystal UHD", ["Geral", "Casa"]),
        ("Whey Protein Growth 1kg Baunilha", ["Geral", "Esportes"]),
        ("Monitor Gamer LG 24 Polegadas 144hz", ["Geral"]),
    ],
)
def test_a_oferta_cai_nos_grupos_certos(titulo, esperados):
    assert destinos(titulo) == esperados


def test_perfume_cai_em_mulheres_e_em_perfumes():
    """Os dois temas citam perfume, e a oferta vai para os dois -- nao e
    conflito: sao publicos que se sobrepoem."""
    saida = destinos("Perfume Malbec Desodorante Colonia 100ml O Boticario")

    assert saida == ["Geral", "Mulheres", "Perfumes"]


def test_cadeirinha_de_bebe_nao_entra_no_casa():
    """"cadeira" e tema do Casa e casaria em cadeirinha de bebe, que e outro
    produto e outro publico. So a palavra do produto separa os dois."""
    assert destinos("Cadeirinha de Bebe para Carro Burigotto") == ["Geral"]


def test_tudo_passa_pelo_geral():
    """A primeira via nao mudou: o que o Geral escolhe continua indo ao Geral."""
    for titulo in (
        "Tenis Nike Revolution 7",
        "Air Fryer Mondial 4L",
        "Perfume Malbec 100ml",
        "Monitor Gamer LG 144hz",
    ):
        assert "Geral" in destinos(titulo)


# ---------- o teto por grupo ----------


def test_o_teto_padrao_e_cinco():
    """Escolhido pelo dono em 16/09/2026."""
    assert max_por_grupo_tematico() == 5


def test_o_teto_vem_do_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MAX_POR_GRUPO_TEMATICO", "2")

    assert max_por_grupo_tematico() == 2


def test_zero_desliga_a_busca_propria(monkeypatch: pytest.MonkeyPatch):
    """Com 0 o grupo volta a receber so o que vier do Geral."""
    monkeypatch.setenv("MAX_POR_GRUPO_TEMATICO", "0")

    assert max_por_grupo_tematico() == 0


# ---------- quem participa da busca propria ----------


def test_o_geral_nao_tem_busca_propria():
    """Ele ja recebe tudo; uma busca propria dele seria a mesma rodada de novo."""
    geral = GrupoDestino(jid="", nome="Geral", temas=())

    assert geral.e_geral


def test_grupo_sem_jid_fica_de_fora():
    """JID vazio e o do Geral (o EVOLUTION_GROUP_JID do .env). Um grupo
    tematico sem JID escrito nao tem para onde mandar."""
    tematicos = [g for g in load_grupos() if not g.e_geral and g.jid]

    assert len(tematicos) == 4
    assert all(g.jid.endswith("@g.us") for g in tematicos)


def test_o_watchlist_real_tem_os_tres_grupos_novos():
    """Invariante de configuracao: os grupos criados em 16/09/2026 continuam la,
    com JID e com tema. Sem tema o grupo viraria um segundo Geral."""
    nomes = {g.nome for g in load_grupos()}

    for esperado in ("Esportes", "Perfumes", "Casa"):
        casados = [n for n in nomes if n.endswith(esperado)]
        assert casados, f"grupo de {esperado} sumiu do watchlist"

    for grupo in load_grupos():
        if grupo.jid:
            assert grupo.temas, f"{grupo.nome} sem tema receberia tudo"


# ---------- a prioridade de cada grupo ----------


def _grupo(pedaco: str) -> GrupoDestino:
    return [g for g in load_grupos() if g.nome.endswith(pedaco)][0]


def test_cada_grupo_tem_a_propria_prioridade():
    """Pedido do dono em 16/09/2026, e o Perfumes entrou em 17/09/2026."""
    assert _grupo("Esportes").prioridade
    assert _grupo("Casa").prioridade
    assert _grupo("Mulheres").prioridade
    assert _grupo("Perfumes").prioridade


def test_a_prioridade_de_perfumes_e_a_marca_importada():
    """O Perfumes ficou sem prioridade ate 17/09/2026 com o argumento de que o
    grupo inteiro ja e perfume. O dono desfez: dentro do grupo nao da na mesma,
    porque a oferta abundante e Natura e Boticario e a que converte e a
    importada. As marcas abaixo sao as que ele nomeou."""
    prioridade = _grupo("Perfumes").prioridade

    for marca in ("gaultier", "azzaro", "versace", "givenchy", "dior"):
        assert marca in prioridade, marca
    assert any("212" in alvo for alvo in prioridade)


def test_a_fila_nao_e_tema_do_esportes():
    """"fila esportivo" nao casava titulo nenhum em 24.242 do catalogo: o ML
    escreve "Tenis Fila Vector". A marca sozinha nao serve -- "fila" esta
    dentro de "afiliado" e de "filamento"."""
    esportes = _grupo("Esportes")

    assert "fila esportivo" not in esportes.temas
    assert "tenis fila" in esportes.temas
    assert "fila" not in esportes.temas


def test_a_prioridade_do_grupo_passa_na_frente():
    """O caso que motivou: organizador de gaveta com 70% ganharia da air fryer
    com 30%, e a air fryer e o motivo de alguem entrar no grupo de Casa."""
    from promo.pipeline import tem_tema

    casa = _grupo("Casa")
    candidatas = [
        oferta("Organizador De Gaveta Colmeia 8 Pecas"),
        oferta("Air Fryer Mondial 4L Family"),
        oferta("Smart Tv Samsung 50 4K"),
    ]

    candidatas.sort(key=lambda o: not tem_tema(o.title, list(casa.prioridade)))

    assert candidatas[-1].title.startswith("Organizador")


def test_a_prioridade_de_mulheres_cobre_maquiagem_cabelo_e_unha():
    """Os tres que o dono nomeou em 16/09/2026."""
    prioridade = _grupo("Mulheres").prioridade

    assert "maquiagem" in prioridade
    assert "unha" in prioridade
    assert any("shampoo" in p or "capilar" in p for p in prioridade)


def test_a_prioridade_e_subconjunto_do_tema():
    """Prioridade que nao e tema do grupo nunca seria escolhida: a oferta e
    filtrada por `aceita` antes de a ordem importar."""
    for pedaco in ("Esportes", "Casa", "Mulheres", "Perfumes"):
        grupo = _grupo(pedaco)
        for alvo in grupo.prioridade:
            assert alvo in grupo.temas, f"{pedaco}: {alvo!r} nao e tema do grupo"


# ---------- o revezamento na fila ----------


def fila(*grupos: str) -> list[tuple]:
    """(id, copy, image_url, grupo_jid) -- a tupla que `flush_pending` monta."""
    return [(i, "texto", None, jid) for i, jid in enumerate(grupos)]


def ordem(itens: list[tuple]) -> list[str]:
    from promo.pipeline import _reveza_por_grupo

    return [x[3] or "Geral" for x in _reveza_por_grupo(itens)]


def test_os_grupos_se_revezam_na_fila():
    """O caso medido em 16/09/2026: o Geral levava a rodada inteira.

    `pending_posts` ordena por prioridade e chegada, e os posts dos grupos
    tematicos sao criados por ultimo -- ficavam sempre no fim.
    """
    entrada = fila("", "", "", "", "MULHERES", "MULHERES", "ESPORTES")

    assert ordem(entrada)[:6] == [
        "Geral",
        "MULHERES",
        "ESPORTES",
        "Geral",
        "MULHERES",
        "Geral",
    ]


def test_o_orcamento_de_uma_rodada_alcanca_todos_os_grupos():
    """Sete vagas (DRIP=100) com quatro grupos esperando: ninguem fica zerado."""
    entrada = fila(*([""] * 10 + ["MULHERES"] * 5 + ["ESPORTES"] * 5 + ["CASA"] * 5))

    cabem = ordem(entrada)[:7]

    assert set(cabem) == {"Geral", "MULHERES", "ESPORTES", "CASA"}


def test_um_grupo_sozinho_nao_muda_de_ordem():
    """Sem concorrencia o revezamento nao pode reordenar nada -- prioridade e
    chegada continuam mandando."""
    from promo.pipeline import _reveza_por_grupo

    entrada = fila("", "", "")

    assert _reveza_por_grupo(entrada) == entrada


def test_o_grupo_com_mais_posts_leva_o_resto():
    """Revezar e dar a vez, nao dividir igual: quem tem fila maior continua
    saindo depois que os outros esvaziam."""
    entrada = fila("", "", "", "", "MULHERES")

    assert ordem(entrada) == ["Geral", "MULHERES", "Geral", "Geral", "Geral"]


def test_fila_vazia_nao_quebra():
    from promo.pipeline import _reveza_por_grupo

    assert _reveza_por_grupo([]) == []


# ---------- o achado do grupo tematico que tambem sai no Geral ----------


def scored_de(titulo: str):
    from promo.models import ScoredOffer

    return ScoredOffer(
        offer=oferta(titulo),
        baseline=200.0,
        discount_pct=50.0,
        observations=0,
        lowest_ever=False,
        verified=False,
    )


def test_o_que_casa_a_prioridade_do_geral_sai_no_geral(monkeypatch):
    """Pedido do dono em 16/09/2026, revertendo o "fica so no grupo dele" do
    mesmo dia: o Geral e onde esta o publico, e o revezamento derrubou a
    frequencia dele."""
    from promo import pipeline

    monkeypatch.setattr(pipeline, "products_in_cooldown", lambda conn, **k: set())
    monkeypatch.setattr(pipeline, "titulos_recentes", lambda conn, m, j: [])

    temas = pipeline.load_priority()

    assert pipeline._cabe_no_geral(scored_de("Tenis Nike Revolution 7 Corrida"), temas)
    assert pipeline._cabe_no_geral(scored_de("Perfume Malbec O Boticario 100ml"), temas)


def test_o_que_nao_casa_a_prioridade_fica_so_no_grupo(monkeypatch):
    """O filtro e o que separa "mais volume no Geral" de "o Geral vira a soma
    dos quatro grupos"."""
    from promo import pipeline

    monkeypatch.setattr(pipeline, "products_in_cooldown", lambda conn, **k: set())
    monkeypatch.setattr(pipeline, "titulos_recentes", lambda conn, m, j: [])

    temas = pipeline.load_priority()

    assert not pipeline._cabe_no_geral(scored_de("Organizador De Gaveta Colmeia"), temas)


def test_o_cooldown_do_geral_continua_valendo(monkeypatch):
    """As contas sao separadas por `grupo_jid`: o produto pode nunca ter saido
    no Esportes e ja ter saido no Geral hoje."""
    from promo import pipeline

    alvo = scored_de("Tenis Nike Revolution 7 Corrida")
    monkeypatch.setattr(
        pipeline, "products_in_cooldown", lambda conn, **k: {alvo.offer.product_id}
    )
    monkeypatch.setattr(pipeline, "titulos_recentes", lambda conn, m, j: [])

    assert not pipeline._cabe_no_geral(alvo, pipeline.load_priority())


def test_o_mesmo_nome_recente_no_geral_tambem_barra(monkeypatch):
    """Mesma regra de nome e marca do resto: outro anuncio do mesmo tenis nao
    vira um segundo post no Geral."""
    from promo import pipeline

    monkeypatch.setattr(pipeline, "products_in_cooldown", lambda conn, **k: set())
    monkeypatch.setattr(
        pipeline,
        "titulos_recentes",
        lambda conn, m, j: ["Tenis Nike Revolution 7 Masculino Corrida Preto"],
    )

    alvo = scored_de("Tenis Nike Revolution 7 Corrida Masculino")

    assert not pipeline._cabe_no_geral(alvo, pipeline.load_priority())


# ---------- o recorte do Esportes, ditado pelo dono em 16/09/2026 ----------


@pytest.mark.parametrize(
    "titulo",
    [
        "Tenis Nike Revolution 7 Masculino Corrida",
        "Kit 6 Pares Meias Lupo Cano Medio Esportiva",
        "Bermuda Adidas Masculina Treino Preta",
        "Bone Nike Aba Curva Preto",
        "Relogio Garmin Forerunner 55 GPS",
        "Whoop 4.0 Pulseira Monitor",
        "Barrinha Proteina YoPro 25g Caixa 12un",
        "Creatina Growth 300g Monohidratada",
        "Smartwatch Amazfit GTS 4 Mini",
        "Legging Feminina Ausare Compressao",
    ],
)
def test_o_esportes_aceita_o_que_o_dono_listou(titulo):
    assert "Esportes" in destinos(titulo), titulo


@pytest.mark.parametrize(
    "titulo, armadilha",
    [
        ("Boneca Barbie Dreamhouse", "bone"),
        ("Kit 6 Cuecas Lupo Boxer Algodao", "lupo"),
        ("Meia Calca Fio 40 Feminina", "meia"),
        ("Creme Hidratante Meia Noite 200ml", "meia"),
    ],
)
def test_o_esportes_recusa_o_que_so_compartilha_letras(titulo, armadilha):
    """Tema curto casa palavra de outra categoria.

    "bone" esta dentro de "boneca", "meia" dentro de "meia calca", e a Lupo faz
    cueca alem de meia esportiva. O `exclui` e o que separa -- mesmo mecanismo
    que ja tirava cueca do grupo de Mulheres.
    """
    assert "Esportes" not in destinos(titulo), f"{titulo} entrou por {armadilha!r}"


# ---------- a Lupo no Esportes: a peca decide, nao a marca ----------


@pytest.mark.parametrize(
    "titulo",
    [
        "Camisa Lupo Sport Masculina Protecao UV",
        "Legging Lupo Sport Feminina Compressao",
        "Short Lupo Running Masculino Com Bolso",
        "Kit 3 Meias Lupo Cano Medio Esportiva",
    ],
)
def test_a_lupo_de_vestir_esportiva_entra(titulo):
    """Camisa, legging e short -- os tres que o dono nomeou em 16/09/2026."""
    assert "Esportes" in destinos(titulo), titulo


@pytest.mark.parametrize(
    "titulo",
    [
        "Kit 6 Cuecas Lupo Boxer Algodao",
        "Meia Calca Lupo Fio 40 Feminina",
        "Pijama Lupo Infantil Manga Longa",
    ],
)
def test_o_resto_do_armario_lupo_fica_de_fora(titulo):
    """A Lupo faz o armario inteiro. A marca sozinha traria cueca, meia calca e
    pijama junto -- so a palavra do produto separa, e e o `exclui` que faz isso.
    """
    assert "Esportes" not in destinos(titulo), titulo


def outra(numero: int, titulo: str) -> Offer:
    """Oferta com id proprio: `oferta()` repete o MLB1, e product_id igual faz o
    segundo item ser descartado como repetido."""
    return Offer(
        source="mercadolivre",
        external_id=f"MLB{numero}",
        title=titulo,
        price=100.0,
        url=f"https://produto.mercadolivre.com.br/MLB-{numero}",
    )



# ---------- o recorte de 17/09/2026: perfume importado e maquiagem de marca ----


@pytest.mark.parametrize(
    "titulo",
    [
        "Perfume Carolina Herrera 212 Vip Rose Edp 80ml Feminino",
        "Perfume Jean Paul Gaultier Le Male Elixir 125ml",
        "Perfume Rabanne Invictus Elixir Parfum 200ml Masculino",
        "Perfume Versace Eros Parfum 100ml",
        "Perfume Yves Saint Laurent Libre Feminino 30ml",
        "Perfume Montblanc Legend Edp 100ml",
        "Prada Paradoxe Edp 90ml",
    ],
)
def test_o_perfume_importado_entra_no_grupo_de_perfumes(titulo):
    """As marcas que o dono nomeou em 17/09/2026."""
    assert "Perfumes" in destinos(titulo), titulo


@pytest.mark.parametrize(
    "titulo, armadilha",
    [
        ("Camisa Polo Ralph Lauren Masculina Algodao Pima", "ralph lauren"),
        ("Conjunto Camiseta E Bermuda Gucci Azul", "gucci"),
        ("Scarpin Slingback Bebece Salto Taca", "ck be"),
        ("Cafe Em Capsula Dolce Gusto Caixa 16un", "dolce"),
    ],
)
def test_o_perfumes_recusa_a_marca_que_nao_e_perfume(titulo, armadilha):
    """Medido contra os 24.242 titulos do catalogo de producao em 17/09/2026.

    "ralph lauren" sozinho casava 98 titulos e nenhum era perfume; "ck be"
    casava "bebece"; "dolce" casaria a capsula de cafe, que e tema prioritario
    do Geral. O `exclui` nao resolveria: `e_barrada` devolve o item quando ele
    casa um tema, e a marca seria o tema.
    """
    assert "Perfumes" not in destinos(titulo), f"{titulo} entrou por {armadilha!r}"


@pytest.mark.parametrize(
    "titulo",
    [
        "Corretivo Too Faced Born This Way Super Coverage",
        "Gloss Fran By Franciny Ehlke Liphoney Mel",
        "Protetor Solar Em Bastao Antimanchas Fps 90 Sallve",
        "Shampoo Anticaspa Dercos Ds Vichy 200ml",
        "Base Liquida Mac Cosmetics Studio Fix",
    ],
)
def test_a_maquiagem_de_marca_entra_no_grupo_de_mulheres(titulo):
    """"tem MUITA COISA PARA MULHER, maquiagens de tudo que e marca, boas
    marcas, quero que essas maquiagens sejam prioridade" -- 17/09/2026."""
    assert "Mulheres" in destinos(titulo), titulo


@pytest.mark.parametrize(
    "titulo, armadilha",
    [
        ("Pasta De Amendoim Dr. Peanut Com Whey 600g", "amend"),
        ("Barra Proteina Whey Bar Creamy Probiotica 12x38g", "creamy"),
    ],
)
def test_o_mulheres_recusa_o_que_so_compartilha_letras(titulo, armadilha):
    """"amend" esta dentro de "amendoim" (51 titulos do catalogo, quase todos
    comida) e "creamy" dentro de "whey bar creamy". Por isso os temas sao
    "amend essencial" e "creamy skincare", e nao a marca sozinha."""
    assert "Mulheres" not in destinos(titulo), f"{titulo} entrou por {armadilha!r}"


@pytest.mark.parametrize(
    "titulo",
    [
        "Tenis Fila Vector Original Masculino Academia E Corrida",
        "Camiseta Feminina Essentials Adizero Adidas Preto",
        "Smartwatch Amazfit Bip Max Gps Amoled",
        "Conjunto Academia Feminino Moda Fitness Marrom",
    ],
)
def test_o_esportes_aceita_o_recorte_de_17_09(titulo):
    assert "Esportes" in destinos(titulo), titulo


# ---------- o teto do Geral por hora ----------


def test_sem_env_nao_ha_teto():
    """O teto e opcional: sem a variavel o revezamento e o de sempre."""
    from promo.config import max_do_geral_por_hora

    assert max_do_geral_por_hora() == 0


def test_o_teto_do_geral_vem_do_env(monkeypatch: pytest.MonkeyPatch):
    from promo.config import max_do_geral_por_hora

    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "12")

    assert max_do_geral_por_hora() == 12


def test_o_teto_corta_a_fila_do_geral():
    """Com duas vagas, o terceiro post do Geral fica para a proxima drenagem --
    e a vaga dele vai para os grupos tematicos, que continuam na fila."""
    from promo.pipeline import _reveza_por_grupo

    entrada = fila("", "", "", "", "MULHERES", "ESPORTES")

    saida = [x[3] or "Geral" for x in _reveza_por_grupo(entrada, 2)]

    assert saida.count("Geral") == 2
    assert saida.count("MULHERES") == 1
    assert saida.count("ESPORTES") == 1


def test_teto_zerado_tira_o_geral_da_drenagem():
    """Bateu o teto da hora: o Geral sai da fila inteiro e os outros drenam."""
    from promo.pipeline import _reveza_por_grupo

    entrada = fila("", "", "MULHERES")

    saida = [x[3] or "Geral" for x in _reveza_por_grupo(entrada, 0)]

    assert saida == ["MULHERES"]


def test_teto_zerado_com_so_o_geral_na_fila_nao_manda_nada():
    from promo.pipeline import _reveza_por_grupo

    assert _reveza_por_grupo(fila("", ""), 0) == []


def test_sem_teto_o_revezamento_nao_muda():
    """None e "sem teto": o comportamento anterior tem que ficar identico."""
    from promo.pipeline import _reveza_por_grupo

    entrada = fila("", "", "", "", "MULHERES")

    assert _reveza_por_grupo(entrada, None) == _reveza_por_grupo(entrada)


def test_o_teto_conta_o_que_ja_saiu_na_hora(monkeypatch: pytest.MonkeyPatch):
    """Com a entrega em lotes a rodada drena varias vezes. Se cada chamada nao
    reler o banco, o teto valeria por lote e o total seria o teto vezes o
    numero de lotes."""
    from promo import pipeline

    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "12")
    monkeypatch.setattr(pipeline, "enviados_na_janela", lambda conn, jid, m: 9)

    assert pipeline._vagas_do_geral(None) == 3


def test_o_teto_nao_fica_negativo(monkeypatch: pytest.MonkeyPatch):
    from promo import pipeline

    monkeypatch.setenv("MAX_DO_GERAL_POR_HORA", "12")
    monkeypatch.setattr(pipeline, "enviados_na_janela", lambda conn, jid, m: 20)

    assert pipeline._vagas_do_geral(None) == 0


# ---------- a reserva de prioridade dentro do grupo ----------


def test_a_reserva_padrao_e_duas_vagas():
    from promo.config import reserva_de_prioridade_do_grupo

    assert reserva_de_prioridade_do_grupo() == 2


def test_a_reserva_vem_do_env(monkeypatch: pytest.MonkeyPatch):
    from promo.config import reserva_de_prioridade_do_grupo

    monkeypatch.setenv("RESERVA_DE_PRIORIDADE_DO_GRUPO", "3")

    assert reserva_de_prioridade_do_grupo() == 3


def test_a_reserva_garante_vaga_para_a_prioridade(
    banco_em_memoria, monkeypatch: pytest.MonkeyPatch
):
    """O caso que motivou a reserva: o item prioritario so passa pela SEGUNDA
    porta, e sem vaga guardada os itens comuns da primeira levam tudo.

    E o normal em Perfumes -- perfume importado quase nunca tem baseline medida
    por nos, e o Natura de sempre tem.
    """
    from promo import pipeline
    from promo.config import Rules
    from promo.models import ScoredOffer

    grupo = _grupo("Perfumes")
    # Familias e ids diferentes de proposito: `mesmo_produto` e o product_id
    # derrubariam cinco variacoes do mesmo nome antes de a reserva importar.
    comuns = [
        outra(id_, titulo)
        for id_, titulo in enumerate(
            (
                "Perfume Natura Essencial Exclusivo 100ml",
                "Perfume Boticario Malbec Club 100ml",
                "Perfume Avon Attraction Rush 75ml",
                "Perfume Jequiti Sedutor Noir 25ml",
                "Perfume Eudora Siage Cachos 50ml",
            )
        )
    ]
    prioritario = outra(9, "Perfume Rabanne Invictus Elixir Parfum 200ml")
    unique = {o.title: o for o in [*comuns, prioritario]}

    def marca(offer: Offer) -> ScoredOffer:
        return ScoredOffer(
            offer=offer,
            baseline=200.0,
            discount_pct=50.0,
            observations=7,
            lowest_ever=False,
            verified=True,
        )

    # Porta 1 so aceita o que nao e prioritario; porta 2 aceita o prioritario.
    monkeypatch.setattr(
        pipeline,
        "score",
        lambda conn, o, r: None if o is prioritario else marca(o),
    )
    monkeypatch.setattr(
        pipeline,
        "score_campaign",
        lambda conn, o, r: marca(o) if o is prioritario else None,
    )
    monkeypatch.setattr(pipeline, "score_pista", lambda conn, o, r: None)

    escolhidas = pipeline._escolhe_para_o_grupo(
        unique, grupo, Rules.load(), [], limite=5
    )

    titulos = [s.offer.title for s in escolhidas]
    assert prioritario.title in titulos
    assert len(titulos) == 5


def test_sem_reserva_a_prioridade_perde_a_vaga(
    banco_em_memoria, monkeypatch: pytest.MonkeyPatch
):
    """O mesmo cenario com a reserva desligada, para o teste acima nao passar
    por acaso."""
    from promo import pipeline
    from promo.config import Rules
    from promo.models import ScoredOffer

    monkeypatch.setenv("RESERVA_DE_PRIORIDADE_DO_GRUPO", "0")

    grupo = _grupo("Perfumes")
    # Familias e ids diferentes de proposito: `mesmo_produto` e o product_id
    # derrubariam cinco variacoes do mesmo nome antes de a reserva importar.
    comuns = [
        outra(id_, titulo)
        for id_, titulo in enumerate(
            (
                "Perfume Natura Essencial Exclusivo 100ml",
                "Perfume Boticario Malbec Club 100ml",
                "Perfume Avon Attraction Rush 75ml",
                "Perfume Jequiti Sedutor Noir 25ml",
                "Perfume Eudora Siage Cachos 50ml",
            )
        )
    ]
    prioritario = outra(9, "Perfume Rabanne Invictus Elixir Parfum 200ml")
    unique = {o.title: o for o in [*comuns, prioritario]}

    def marca(offer: Offer) -> ScoredOffer:
        return ScoredOffer(
            offer=offer,
            baseline=200.0,
            discount_pct=50.0,
            observations=7,
            lowest_ever=False,
            verified=True,
        )

    monkeypatch.setattr(
        pipeline,
        "score",
        lambda conn, o, r: None if o is prioritario else marca(o),
    )
    monkeypatch.setattr(
        pipeline,
        "score_campaign",
        lambda conn, o, r: marca(o) if o is prioritario else None,
    )
    monkeypatch.setattr(pipeline, "score_pista", lambda conn, o, r: None)

    escolhidas = pipeline._escolhe_para_o_grupo(
        unique, grupo, Rules.load(), [], limite=5
    )

    assert prioritario.title not in [s.offer.title for s in escolhidas]


# ---------- a marca obrigatoria do Perfumes ----------


@pytest.mark.parametrize(
    "titulo, armadilha",
    [
        ("Whey Protein Concentrado Sabor Natural 1kg Growth", "natura"),
        ("Percarbonato De Sodio 100% Puro Tira Manchas 1kg", "arbo"),
        ("Testo Essencial Formula Com Feno Grego E Arginina", "essencial"),
        ("Fio Dental Reach Essencial Menta 100m Johnson's", "essencial"),
        ("Camiseta Hugo Boss Masculina Regular Thompson", "hugo boss"),
        ("Polo Piquet Classica Reserva Malbec Lisa G", "malbec"),
        ("Bota Invictus Arion 2.0 6pol Leve E Reforcada", "invictus"),
        ("Eudora Shampoo E Mascara Capilar Siage Cica-therapy", "eudora"),
    ],
)
def test_o_perfumes_so_aceita_quem_diz_que_e_perfume(titulo, armadilha):
    """Medido em 17/09/2026 contra os 22.695 titulos do catalogo: o grupo
    aceitava 759 produtos e 233 nao eram perfume. Com o `exige` sobraram 26, e
    esses 26 sao perfume de verdade -- so nao trazem a palavra no titulo.

    O `exclui` nao resolveria: `e_barrada` devolve o item quando ele casa um
    tema, e aqui quem erra e o tema.
    """
    assert "Perfumes" not in destinos(titulo), f"{titulo} entrou por {armadilha!r}"


@pytest.mark.parametrize(
    "titulo",
    [
        "Perfume Natura Essencial Exclusivo Masculino 100ml",
        "212 Vip Rose EDP 30ml Feminino - Carolina Herrera",
        "Prada Paradoxe Edp 90ml",
        "Lattafa Bade'e Al Oud For Glory 100ml",
        "Hugo Boss Bottled Infinite Masculino Edp 200ml",
        "Rabanne Lady Million EDP 80ml Para Feminino",
        "Body Splash Carolina Herrera 212 Vip Rose 250ml",
    ],
)
def test_o_perfume_sem_a_palavra_no_titulo_continua_entrando(titulo):
    """O `exige` nao pode cortar perfume de verdade. Por isso a lista tem "edp",
    "edt", "eau de" e as marcas que so fazem perfume."""
    assert "Perfumes" in destinos(titulo), titulo


def test_o_exige_e_so_do_perfumes():
    """Grupo sem `exige` nao muda de comportamento -- o campo e opcional."""
    for pedaco in ("Mulheres", "Esportes", "Casa"):
        assert _grupo(pedaco).exige == ()
    assert _grupo("Perfumes").exige

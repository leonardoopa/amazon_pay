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

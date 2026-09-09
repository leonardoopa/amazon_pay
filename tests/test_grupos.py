"""Uma oferta pode ir para mais de um grupo, e cada grupo tem o seu recorte.

O primeiro grupo nichado nasceu em 09/09/2026 -- "Comunidade do Desconto -
Mulheres 🇧🇷 01" --, e a ideia e ter varios: homens, casa, saude, corrida,
eletronicos, e o geral que recebe tudo.

Duas decisoes que o resto depende:

O JID do geral fica VAZIO. Vazio significa "o EVOLUTION_GROUP_JID do .env",
que e o mesmo grupo de sempre. Escrever o JID real ali quebraria o cooldown:
os 3.063 posts ja enviados tem `grupo_jid` vazio, e trocar o valor faria todos
sumirem da conta e produtos recem-postados voltarem ao grupo no dia seguinte.

O recorte e por TITULO, e nao por categoria do ML. A oferta chega por busca,
pelos mais vendidos ou pela vitrine, e nenhuma dessas carrega o motivo de ter
vindo -- o titulo carrega. E a mesma regra que o `priority` ja usava.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.models import Offer  # noqa: E402
from promo.pipeline import GrupoDestino, load_grupos  # noqa: E402


def escrever(tmp_path: Path, grupos) -> Path:
    caminho = tmp_path / "watchlist.json"
    caminho.write_text(
        json.dumps({"keywords": [], "grupos": grupos}), encoding="utf-8"
    )
    return caminho


def oferta(titulo: str) -> Offer:
    return Offer(
        source="mercadolivre",
        external_id="X1",
        title=titulo,
        price=100.0,
        url="https://exemplo/x1",
    )


# ---------- quem aceita o que ----------


def test_grupo_sem_temas_aceita_tudo():
    geral = GrupoDestino(jid="", nome="Geral")

    assert geral.e_geral
    assert geral.aceita(oferta("Monitor AOC 22 120Hz Gaming"))
    assert geral.aceita(oferta("Kit Wella Invigo Nutri-Enrich"))


def test_grupo_com_temas_so_aceita_o_que_casa():
    mulheres = GrupoDestino(jid="X@g.us", nome="Mulheres", temas=("wella", "perfume"))

    assert mulheres.aceita(oferta("Kit Wella Invigo Nutri-Enrich"))
    assert not mulheres.aceita(oferta("Monitor AOC 22 120Hz Gaming"))


def test_o_recorte_ignora_acento_e_caixa():
    grupo = GrupoDestino(jid="X@g.us", nome="G", temas=("oleo capilar",))

    assert grupo.aceita(oferta("ÓLEO CAPILAR Extraordinário L'Oréal"))


def test_a_mesma_oferta_vai_para_dois_grupos():
    """Wella sai no Geral e no Mulheres; monitor gamer so no Geral."""
    grupos = [
        GrupoDestino(jid="", nome="Geral"),
        GrupoDestino(jid="M@g.us", nome="Mulheres", temas=("wella",)),
    ]

    wella = [g.nome for g in grupos if g.aceita(oferta("Kit Wella Invigo"))]
    monitor = [g.nome for g in grupos if g.aceita(oferta("Monitor AOC Gaming"))]

    assert wella == ["Geral", "Mulheres"]
    assert monitor == ["Geral"]


# ---------- a configuracao ----------


def test_sem_bloco_grupos_ha_um_destino_so(tmp_path):
    """Quem nao configurou nada nao muda de comportamento."""
    caminho = tmp_path / "watchlist.json"
    caminho.write_text(json.dumps({"keywords": []}), encoding="utf-8")

    grupos = load_grupos(caminho)

    assert len(grupos) == 1
    assert grupos[0].jid == ""
    assert grupos[0].e_geral


def test_jid_vazio_e_legitimo_no_geral(tmp_path):
    """Vazio quer dizer "o destino padrao do .env" -- e o que preserva o
    cooldown dos 3.063 posts que ja existem."""
    caminho = escrever(
        tmp_path, [{"nome": "Geral", "jid": "", "temas": []}]
    )

    assert load_grupos(caminho)[0].jid == ""


def test_grupo_sem_nome_fica_de_fora(tmp_path):
    """Sem nome nao da para dizer no log para onde o post foi."""
    caminho = escrever(tmp_path, [{"jid": "X@g.us", "temas": []}])

    assert load_grupos(caminho) == [GrupoDestino(jid="", nome="principal")]


def test_le_os_dois_grupos_configurados(tmp_path):
    caminho = escrever(
        tmp_path,
        [
            {"nome": "Geral", "jid": "", "temas": []},
            {"nome": "Mulheres", "jid": "M@g.us", "temas": ["wella", "perfume"]},
        ],
    )

    grupos = load_grupos(caminho)

    assert [g.nome for g in grupos] == ["Geral", "Mulheres"]
    assert grupos[1].temas == ("wella", "perfume")


def test_os_temas_chegam_normalizados(tmp_path):
    """O titulo e comparado sem acento; o tema tambem precisa estar."""
    caminho = escrever(
        tmp_path, [{"nome": "G", "jid": "X@g.us", "temas": ["MÁSCARA capilar"]}]
    )

    assert load_grupos(caminho)[0].temas == ("mascara capilar",)


# ---------- a configuracao de producao ----------


def test_a_watchlist_real_tem_o_geral_com_jid_vazio():
    """Se alguem escrever o JID do geral aqui, os 3.063 posts antigos somem do
    cooldown e o grupo recebe repetido no dia seguinte."""
    grupos = load_grupos()
    geral = [g for g in grupos if g.e_geral]

    assert len(geral) == 1
    assert geral[0].jid == ""


def test_a_watchlist_real_tem_o_grupo_de_mulheres():
    grupos = load_grupos()
    nichados = [g for g in grupos if not g.e_geral]

    assert len(nichados) == 1
    assert nichados[0].jid == "120363428560546806@g.us"
    assert "wella" in nichados[0].temas
    assert "perfume" in nichados[0].temas


def test_o_grupo_de_mulheres_recusa_o_que_nao_e_dele():
    nichado = [g for g in load_grupos() if not g.e_geral][0]

    assert not nichado.aceita(oferta("Monitor AOC 22 120Hz Gaming HDMI"))
    assert not nichado.aceita(oferta("Whey Protein Dux Nutrition 900g"))
    assert not nichado.aceita(oferta("Furadeira de Impacto Bosch"))


def test_o_grupo_de_mulheres_aceita_o_que_e_dele():
    nichado = [g for g in load_grupos() if not g.e_geral][0]

    for titulo in (
        "Kit Wella Professionals Invigo Nutri-Enrich",
        "Perfume Natura Homem Masculino 100ml",
        "Calca Legging Feminina Ausare Compressao",
        "Protetor Solar Facial FPS 70 La Roche-Posay",
        "Kit Body Splash Victoria's Secret",
    ):
        assert nichado.aceita(oferta(titulo)), titulo

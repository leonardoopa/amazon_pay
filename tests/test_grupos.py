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


def _por_nome(pedaco: str):
    """O grupo cujo nome termina no pedaco dado.

    Procurar por nome, e nao por indice, porque a lista cresceu: era um grupo
    nichado ate 16/09/2026 e passou a ser quatro (Mulheres, Esportes, Perfumes
    e Casa). Um `[0]` aqui volta a quebrar no quinto.
    """
    casados = [g for g in load_grupos() if g.nome.endswith(pedaco)]
    assert casados, f"grupo de {pedaco} sumiu do watchlist"
    return casados[0]


def test_a_watchlist_real_tem_os_quatro_grupos_nichados():
    """Mulheres desde 09/09/2026; Esportes, Perfumes e Casa desde 16/09/2026.

    O que este teste protege e a configuracao: grupo nichado sem JID nao tem
    para onde mandar, e grupo nichado sem tema viraria um segundo Geral --
    receberia TUDO, incluindo o que nao e dele.
    """
    nichados = [g for g in load_grupos() if not g.e_geral]

    assert len(nichados) == 4
    for grupo in nichados:
        assert grupo.jid.endswith("@g.us"), grupo.nome
        assert grupo.temas, grupo.nome


def test_o_grupo_de_mulheres_continua_com_os_temas_dele():
    mulheres = _por_nome("Mulheres")

    assert mulheres.jid == "120363428560546806@g.us"
    assert "wella" in mulheres.temas
    assert "perfume" in mulheres.temas


def test_os_grupos_novos_tem_os_temas_que_os_definem():
    assert "tenis de corrida" in _por_nome("Esportes").temas
    assert "perfume" in _por_nome("Perfumes").temas
    assert "air fryer" in _por_nome("Casa").temas


def test_o_grupo_de_mulheres_recusa_o_que_nao_e_dele():
    nichado = _por_nome("Mulheres")

    assert not nichado.aceita(oferta("Monitor AOC 22 120Hz Gaming HDMI"))
    assert not nichado.aceita(oferta("Whey Protein Dux Nutrition 900g"))
    assert not nichado.aceita(oferta("Furadeira de Impacto Bosch"))


def test_o_grupo_de_mulheres_aceita_o_que_e_dele():
    nichado = _por_nome("Mulheres")

    for titulo in (
        "Kit Wella Professionals Invigo Nutri-Enrich",
        "Perfume Natura Essencial Feminino 100ml",
        "Calca Legging Feminina Ausare Compressao",
        "Protetor Solar Facial FPS 70 La Roche-Posay",
        "Kit Body Splash Victoria's Secret",
    ):
        assert nichado.aceita(oferta(titulo)), titulo


def test_o_perfume_masculino_deixou_de_entrar_em_18_09_2026():
    """Este caso estava na lista de aceitos ate 18/09/2026, com "Perfume Natura
    Homem Masculino" como exemplo do que o grupo recebia. O dono desfez: "tudo
    que tiver masculino, nao e pra ser enviado no grupo das mulheres".

    Nao era hipotese -- 17 dos 119 posts enviados ao grupo nas 48h anteriores
    eram masculinos.
    """
    nichado = _por_nome("Mulheres")

    assert not nichado.aceita(oferta("Perfume Natura Homem Masculino 100ml"))


# ---------- exclusao por grupo ----------
#
# Tema e marca colidem. Em 09/09/2026 o tema "lupo" levou "Kit 6 Cuecas Lupo
# Boxer" para o grupo de Mulheres, duas vezes: a marca faz calcinha E cueca, e
# nenhum ajuste no tema separa as duas.


def test_cueca_nao_entra_no_grupo_de_mulheres():
    grupo = GrupoDestino(
        jid="120@g.us", nome="Mulheres", temas=("lupo", "calcinha"),
        exclui=("cueca",),
    )

    assert not grupo.aceita(
        oferta("Kit 6 Cuecas Lupo Boxer Box Sem Costura Basic Microfibra")
    )


def test_a_exclusao_do_grupo_nao_derruba_o_resto_da_marca():
    """O tema continua valendo para o que ele deveria trazer."""
    grupo = GrupoDestino(
        jid="120@g.us", nome="Mulheres", temas=("lupo", "calcinha"),
        exclui=("cueca",),
    )

    assert grupo.aceita(oferta("Kit 3 Calcinhas Lupo Algodao"))


def test_a_exclusao_vence_o_tema():
    """A ordem importa: `exclui` corre depois de `temas` e derruba mesmo com
    tema casando. Sem isso a palavra excluida so valeria para o que ja nao
    entrava."""
    grupo = GrupoDestino(
        jid="120@g.us", nome="Mulheres", temas=("cueca",), exclui=("cueca",)
    )

    assert not grupo.aceita(oferta("Cueca Lupo"))


def test_grupo_sem_exclusao_segue_igual():
    """A maioria dos grupos nao tem `exclui`, e nada muda para eles."""
    grupo = GrupoDestino(jid="120@g.us", nome="Mulheres", temas=("lupo",))

    assert grupo.aceita(oferta("Kit 6 Cuecas Lupo Boxer"))


def test_o_geral_com_exclusao_continua_recebendo_o_resto():
    """`exclui` funciona tambem no grupo sem temas -- ele nao vira nichado por
    causa disso."""
    grupo = GrupoDestino(jid="", nome="Geral", exclui=("cueca",))

    assert not grupo.aceita(oferta("Cueca Lupo Boxer"))
    assert grupo.aceita(oferta("Shampoo Wella 1L"))


def test_a_exclusao_do_watchlist_chega_no_grupo(tmp_path):
    arquivo = tmp_path / "watchlist.json"
    arquivo.write_text(
        json.dumps(
            {
                "grupos": [
                    {
                        "nome": "Mulheres",
                        "jid": "120@g.us",
                        "temas": ["lupo"],
                        "exclui": ["cueca", "sunga"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    grupo = load_grupos(arquivo)[0]

    assert grupo.exclui == ("cueca", "sunga")

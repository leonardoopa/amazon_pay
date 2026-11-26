"""Busca hibrida no catalogo: FTS5 e vetor, juntos por Reciprocal Rank Fusion.

O que sai daqui e so o que o bot MEDIU: preco do ultimo dia visto, a hora em que
ele foi visto e a mediana dos 60 dias anteriores. Nada e inventado depois -- o
texto da resposta parte destes numeros e e conferido contra eles.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from ..db import _iso, connect, mesmo_produto, now, price_history, sem_acento
from .banco import conectar
from .indice import Embeddings, Vetores

log = logging.getLogger("promo")

# Janela da mediana: a mesma da baseline do bot (BASELINE_WINDOW_DAYS).
JANELA_DA_MEDIANA = 60
# Menos dias que isso e a mediana nao diz nada -- o bot exige o mesmo para
# chamar uma oferta de verificada.
DIAS_MINIMOS = 7

# Oferta que saiu no grupo nesta janela vai na frente: o bot ja a conferiu e
# mandou, e quem pergunta "promocao de ps5" quer justamente essa. Em 18/11/2026
# o console de PS5 tinha saido no grupo uma hora antes, e a resposta nao o trouxe.
JANELA_DO_GRUPO = 7
# O preco de hoje pode subir depois do envio. Ate 5% acima, a oferta ainda vale.
FOLGA_DO_PRECO_DO_GRUPO = 1.05
# Abaixo de 30% da propria mediana, o preco e tratado como erro de leitura.
PISO_DO_PRECO = 0.3
# Entre dois anuncios do mesmo modelo, o visto ha mais que isso perde para o
# recente, mesmo mais barato: o preco de duas semanas atras pode nao existir mais.
DIAS_PARA_PRECO_VELHO = 3

# Medida colada no numero ("42mm", "825gb") ou logo depois dele ("825 gb"), e
# quantidade de variacao do anuncio ("(3 cores)", "2 unidades"). Mudam entre
# anuncios do mesmo modelo, entao nao contam como numero de modelo.
UNIDADES = {
    "mm", "cm", "m", "gb", "tb", "mb", "l", "ml", "w", "v", "kg", "g", "mah",
    "hz", "pol", "polegadas", "lb", "oz", "un", "pcs", "x", "k", "cores",
    "unidades", "unid", "pecas", "itens", "ano", "anos", "meses",
}  # fmt: skip
# Quanto do titulo menor tem que estar no maior para dois anuncios com o mesmo
# numero de modelo serem o mesmo produto (ver `_um_contem_o_outro`).
CONTENCAO_MINIMA = 0.85

# Entre quantos dos mais relevantes a oferta do grupo e a abaixo da mediana
# passam na frente. Com todos os termos da pergunta no titulo, a relevancia entre
# eles pesa pouco; quando so parte dos termos casou, a fila e curta, para uma
# oferta que mal tem a ver nao passar na frente.
CANDIDATOS_COM_TODOS_OS_TERMOS = 30
CANDIDATOS_COM_PARTE_DOS_TERMOS = 5
# Quantas posicoes de relevancia cada faixa adianta (`faixa_da_oferta`): a que
# saiu no grupo passa na frente de quem esta ate 6 posicoes acima dela.
AVANCO_DA_FAIXA = {0: 6, 1: 3, 2: 0}
# Titulo que abre com o tipo pedido ("Console PlayStation 5...") e o produto; o
# que so cita o tipo no meio ("Portal Ps5 ... seu console Ps5") e acessorio.
# Em 26/11/2026, "console do play 5" trazia portal e conversor e nenhum console,
# porque os acessorios tinham saido no grupo e ganhavam o avanco da faixa.
AVANCO_DO_TIPO = 12

# Palavras que a pergunta tem e o titulo do produto nao. Sem tirar, "tem ps5
# barato?" procuraria "barato" no catalogo e o AND do FTS nao acharia nada. O
# pedido em si tambem sai: em 18/11/2026, "me mande promocoes de PS5" deixou
# "mande" na busca, e o primeiro resultado foi um gel de acido mandelico.
PALAVRAS_VAZIAS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das",
    "dos", "e", "ou", "em", "no", "na", "nos", "nas", "com", "sem", "para",
    "pra", "pro", "por", "que", "qual", "quais", "tem", "ter", "tens", "teria",
    "existe", "quero", "queria", "gostaria", "preciso", "procuro", "procurando",
    "busco", "buscando", "algum", "alguma", "alguns", "algumas", "algo",
    "barato", "barata", "baratos", "baratas", "preco", "precos", "promocao",
    "promocoes", "promo", "promos", "oferta", "ofertas", "desconto",
    "descontos", "achado", "achados", "link", "links", "ate", "r", "reais",
    "mais", "menos", "bom", "boa", "melhor", "melhores", "voce", "voces", "vc",
    "vcs", "me", "mim", "manda", "mande", "mandar", "envia", "envie", "enviar",
    "mostra", "mostre", "mostrar", "passa", "passe", "indica", "indique",
    "recomenda", "recomende", "acha", "favor", "pf", "pfv", "hoje", "agora",
    "aqui", "ai",
    # Pergunta sobre o proprio assistente. Sem o Gemini, "Em que voce pode me
    # ajudar?" virou a busca "pode ajudar" e trouxe shampoo "Fragrancia
    # Poderosa" (19/11/2026). Sem estas, a pergunta fica vazia e vira conversa.
    "pode", "podes", "poderia", "consegue", "conseguiria", "ajuda", "ajudar",
    "ajude", "ajudaria", "faz", "fazer", "faria", "funciona", "serve", "sabe",
    "saber", "ver", "olhar", "procurar", "encontrar", "achar", "quem", "como",
    "onde", "quando", "porque", "pq", "isso", "isto", "esse", "essa", "este",
    "esta", "seu", "sua", "meu", "minha", "nao", "sim", "ok",
    # Pergunta de preco e de compra: "quanto ta o ps5?", "vale a pena?".
    "quanto", "quanta", "custa", "custando", "valor", "vale", "pena",
    "compensa", "ta", "comprar", "compra", "comprando",
    # Cumprimento junto do pedido: "bom dia, tem ps5?" busca so "ps5".
    "oi", "ola", "opa", "eai", "eae", "salve", "dia", "tarde", "noite", "tudo",
    "bem", "blz", "beleza", "obrigado", "obrigada", "valeu",
}  # fmt: skip

# O nome que a pessoa escreve e o que o anuncio escreve nem sempre batem: o
# titulo do console quase nunca traz "PS5", e as fritadeiras vem ora como "Air
# Fryer", ora como "Airfryer". Cada lado vira um OR dentro do mesmo termo.
SINONIMOS: dict[str, tuple[str, ...]] = {
    "ps5": ("playstation 5", "playstation5"),
    "ps 5": ("ps5", "playstation 5", "playstation5"),
    "playstation 5": ("ps5", "playstation5"),
    "play5": ("ps5", "playstation 5", "playstation5"),
    "play 5": ("ps5", "playstation 5", "playstation5"),
    "playstation5": ("ps5", "playstation 5", "playstation5"),
    "ps4": ("playstation 4", "playstation4"),
    "ps 4": ("ps4", "playstation 4", "playstation4"),
    "playstation 4": ("ps4", "playstation4"),
    "play4": ("ps4", "playstation 4", "playstation4"),
    "play 4": ("ps4", "playstation 4", "playstation4"),
    "playstation4": ("ps4", "playstation 4", "playstation4"),
    "play station": ("playstation",),
    "gta 6": ("gta vi", "grand theft auto vi"),
    "gta vi": ("gta 6", "grand theft auto vi"),
    "airfryer": ("air fryer",),
    "air fryer": ("airfryer",),
    "celular": ("smartphone",),
    "smartphone": ("celular",),
    "geladeira": ("refrigerador",),
    "refrigerador": ("geladeira",),
    "notebook": ("laptop",),
    "tv": ("televisao",),
}


@dataclass(frozen=True)
class Resultado:
    product_id: str
    titulo: str
    fonte: str
    external_id: str
    url: str
    preco: float
    visto_em: str  # ISO, como o bot grava `last_seen_at`
    mediana: float | None  # dos precos diarios da janela, sem o dia de hoje
    dias: int  # quantos dias de historico sustentam a mediana
    menor: float | None  # menor preco diario da janela
    no_grupo: str | None = None  # ISO do envio ao grupo, se o preco ainda vale
    imagem: str | None = None  # URL http(s) ou caminho local da foto guardada


@dataclass(frozen=True)
class Historico:
    """O que o bot mediu de um produto nos ultimos 60 dias."""

    resultado: Resultado
    menor_preco: float | None  # menor preco diario da janela, hoje incluido
    menor_em: str | None  # AAAA-MM-DD do menor preco
    dias: int  # dias com medicao na janela, hoje incluido
    grupo_em: str | None  # ISO do ultimo envio ao grupo na janela
    grupo_preco: float | None


def termos(consulta: str) -> list[str]:
    """As palavras da pergunta que valem buscar no titulo."""
    return [
        t
        for t in re.findall(r"[a-z0-9]+", sem_acento(consulta))
        if t not in PALAVRAS_VAZIAS and (len(t) > 1 or t.isdigit())
    ]


def conceitos(consulta: str) -> list[tuple[str, ...]]:
    """Os termos da busca, cada um com seus sinonimos: [("ps5", "playstation 5")].

    O par de palavras e testado antes da palavra sozinha, para "gta 6" virar um
    termo so (com "gta vi") em vez de "gta" E "6".
    """
    palavras = termos(consulta)
    grupos: list[tuple[str, ...]] = []
    i = 0
    while i < len(palavras):
        par = " ".join(palavras[i : i + 2])
        if i + 1 < len(palavras) and par in SINONIMOS:
            grupos.append((par, *SINONIMOS[par]))
            i += 2
            continue
        grupos.append((palavras[i], *SINONIMOS.get(palavras[i], ())))
        i += 1
    return grupos


def _expressao(grupo: tuple[str, ...]) -> str:
    """Um termo em sintaxe FTS5. Tudo entre aspas, para o texto da pessoa nunca
    virar operador; palavra solta leva `*` ("airfryer" acha "airfryers").

    Numero puro casa inteiro: com `*`, o "15" de "iphone 15" casava tambem com
    "150" e "1500" de qualquer titulo.
    """
    partes = [f'"{f}"' if " " in f or f.isdigit() else f'"{f}"*' for f in grupo]
    return partes[0] if len(partes) == 1 else f"({' OR '.join(partes)})"


def codigos_de_modelo(titulo: str) -> set[str]:
    """Os numeros de modelo do titulo: {"265s"}, {"55"}, {"5"}.

    Fica de fora o que e medida ("42mm", "825 gb") e o preco colado no titulo
    ("- R$ 4.650"), que mudam entre anuncios do mesmo modelo.
    """
    limpo = re.sub(r"R\$\s*[\d.,]+", " ", titulo)
    tokens = re.findall(r"[a-z0-9]+", sem_acento(limpo))
    codigos: set[str] = set()
    for n, token in enumerate(tokens):
        if not any(c.isdigit() for c in token):
            continue
        medida = re.fullmatch(r"\d+([a-z]+)", token)
        if medida and medida.group(1) in UNIDADES:
            continue
        if token.isdigit() and n + 1 < len(tokens) and tokens[n + 1] in UNIDADES:
            continue
        codigos.add(token)
    return codigos


def tipo_pedido(consulta: str) -> set[str]:
    """A primeira palavra de cada forma do primeiro termo: o tipo de produto.

    "console ps5" -> {"console"}; "airfryer" -> {"airfryer", "air"}.
    """
    grupos = conceitos(consulta)
    return {forma.split()[0] for forma in grupos[0]} if grupos else set()


def abre_com_o_tipo(titulo: str, tipo: set[str]) -> bool:
    """O titulo comeca pelo tipo pedido (plural e sufixo contam: "relogios")."""
    palavras = _palavras(titulo)
    return bool(palavras) and any(palavras[0].startswith(t) for t in tipo)


def _palavras(titulo: str) -> list[str]:
    """As palavras do titulo que descrevem o produto, na ordem: sem preco,
    numero solto e palavra vazia ("com", "de")."""
    limpo = re.sub(r"R\$\s*[\d.,]+", " ", titulo)
    return [
        t
        for t in re.findall(r"[a-z0-9]+", sem_acento(limpo))
        if len(t) > 1 and not t.isdigit() and t not in PALAVRAS_VAZIAS
    ]


def _um_contem_o_outro(titulo_a: str, titulo_b: str) -> bool:
    """O titulo curto cabe quase inteiro no longo, e os dois abrem igual.

    Caso de 19/11/2026: "Relogio Garmin Forerunner 265 Music 46mm GPS (3
    cores)" e "Relogio Garmin Forerunner 265 Music Preto com Cinza 46mm com
    Monitor Cardiaco..." sao o mesmo relogio, mas o resto do titulo longo derruba
    a sobreposicao do `mesmo_produto`. A primeira palavra igual e o que impede
    "Capa Apple iPhone 15" de virar o proprio "Apple iPhone 15".
    """
    a, b = _palavras(titulo_a), _palavras(titulo_b)
    if not a or not b or a[0] != b[0]:
        return False
    menor, maior = sorted((set(a), set(b)), key=len)
    return len(menor) >= 3 and len(menor & maior) / len(menor) >= CONTENCAO_MINIMA


def mesmo_modelo(titulo_a: str, titulo_b: str) -> bool:
    """Dois anuncios do mesmo modelo: o mesmo numero de modelo E o mesmo produto.

    O `mesmo_produto` do grupo e largo de proposito -- o grupo nao manda duas
    ofertas da mesma linha no mesmo dia. Sozinho, ele juntava Forerunner 55, 70,
    265 e 265S num produto so, e "relogio garmin" voltava com um relogio
    (19/11/2026). Para quem pergunta, modelo diferente e opcao diferente. Com o
    numero igual, vale tambem o titulo curto contido no longo
    (`_um_contem_o_outro`), senao o mesmo 265 aparecia duas vezes.
    """
    codigos = codigos_de_modelo(titulo_a)
    if codigos != codigos_de_modelo(titulo_b):
        return False
    if mesmo_produto(titulo_a, titulo_b):
        return True
    return bool(codigos) and _um_contem_o_outro(titulo_a, titulo_b)


def _velho(r: Resultado) -> bool:
    visto = datetime.fromisoformat(r.visto_em)
    return now() - visto > timedelta(days=DIAS_PARA_PRECO_VELHO)


def melhor_anuncio(a: Resultado, b: Resultado) -> Resultado:
    """O anuncio que fica entre dois do mesmo modelo: o recente; empatou, o barato."""
    if _velho(a) != _velho(b):
        return b if _velho(a) else a
    return b if b.preco < a.preco else a


def implausivel(r: Resultado) -> bool:
    """Preco tao abaixo da propria mediana que o mais provavel e erro de leitura.

    Em 18/11/2026 uma geladeira de R$ 2.258,86 aparecia por R$ 200,00: uma
    medicao isolada do dia 13/11. Liquidacao de verdade nao chega a 70% abaixo
    da mediana, e erro assim mandaria a pessoa atras de um preco que nao existe.
    """
    return bool(
        r.mediana and r.dias >= DIAS_MINIMOS and r.preco < r.mediana * PISO_DO_PRECO
    )


def faixa_da_oferta(r: Resultado) -> int:
    """0: saiu no grupo e o preco ainda vale; 1: abaixo da mediana; 2: o resto."""
    if r.no_grupo:
        return 0
    if r.mediana and r.dias >= DIAS_MINIMOS and r.preco <= r.mediana * 0.97:
        return 1
    return 2


def rrf(*listas: list[int], k: int = 60) -> list[int]:
    """Reciprocal Rank Fusion: junta rankings sem precisar comparar as notas.

    BM25 e cosseno vivem em escalas diferentes, e somar as notas daria peso a
    quem tem o numero maior. A posicao no ranking e comparavel: cada lista vale
    1/(k + posicao), e o `k` de 60 e o do artigo original.
    """
    notas: dict[int, float] = {}
    for lista in listas:
        for posicao, item in enumerate(lista):
            notas[item] = notas.get(item, 0.0) + 1.0 / (k + posicao + 1)
    return sorted(notas, key=lambda item: notas[item], reverse=True)


def busca_lexical(
    conn: sqlite3.Connection, consulta: str, k: int = 30
) -> tuple[list[int], bool]:
    """Itens pelo BM25 do FTS5, e se eles tem todos os termos da pergunta.

    So quando nenhum item tem todos e que vale qualquer um, com o BM25 pondo na
    frente quem tem mais. Antes o "qualquer um" completava a lista sempre, e
    "notebook gamer ate 4000" voltava com fonte de notebook e mochila.
    """
    grupos = conceitos(consulta)
    if not grupos:
        return [], True
    partes = [_expressao(g) for g in grupos]
    sql = (
        "SELECT rowid FROM itens_fts WHERE itens_fts MATCH ? "
        "ORDER BY bm25(itens_fts) LIMIT ?"
    )
    achados = [linha[0] for linha in conn.execute(sql, (" AND ".join(partes), k))]
    if achados or len(partes) == 1:
        return achados, True
    return [linha[0] for linha in conn.execute(sql, (" OR ".join(partes), k))], False


def _no_grupo(bot: sqlite3.Connection, product_ids: list[str]) -> dict:
    """O ultimo envio ao grupo de cada produto na janela: {id: (quando, preco)}."""
    marcas = ",".join("?" * len(product_ids))
    desde = _iso(now() - timedelta(days=JANELA_DO_GRUPO))
    linhas = bot.execute(
        f"""
        SELECT p.product_id, p.sent_at, p.price FROM posts p
        JOIN (
            SELECT product_id, MAX(sent_at) AS ultimo FROM posts
            WHERE status = 'sent' AND sent_at >= ? AND product_id IN ({marcas})
            GROUP BY product_id
        ) u ON u.product_id = p.product_id AND u.ultimo = p.sent_at
        """,
        (desde, *product_ids),
    )
    return {linha[0]: (linha[1], float(linha[2])) for linha in linhas}


def detalhes(product_ids: list[str]) -> dict[str, Resultado]:
    """Preco, hora da medicao e historico, direto do banco do bot.

    Produto cujo ultimo dia visto marca `available = 0` fica de fora: mandar
    alguem comprar o que acabou e pior do que nao achar.
    """
    if not product_ids:
        return {}
    marcas = ",".join("?" * len(product_ids))
    saida: dict[str, Resultado] = {}
    with connect() as bot:
        produtos = {
            p["id"]: p
            for p in bot.execute(
                f"SELECT id, source, external_id, title, url, image_url, last_seen_at "
                f"FROM products WHERE id IN ({marcas})",
                product_ids,
            )
        }
        ultimos = {
            linha["product_id"]: linha
            for linha in bot.execute(
                f"""
                SELECT h.product_id, h.price, h.available FROM price_history h
                JOIN (
                    SELECT product_id, MAX(observed_on) AS dia FROM price_history
                    WHERE product_id IN ({marcas}) GROUP BY product_id
                ) u ON u.product_id = h.product_id AND u.dia = h.observed_on
                """,
                product_ids,
            )
        }
        grupo = _no_grupo(bot, product_ids)
        for pid in product_ids:
            produto, ultimo = produtos.get(pid), ultimos.get(pid)
            if produto is None or ultimo is None or not ultimo["available"]:
                continue
            historico = price_history(bot, pid, JANELA_DA_MEDIANA)
            enviado = grupo.get(pid)
            vale = enviado and ultimo["price"] <= enviado[1] * FOLGA_DO_PRECO_DO_GRUPO
            saida[pid] = Resultado(
                product_id=pid,
                titulo=produto["title"],
                fonte=produto["source"],
                external_id=produto["external_id"],
                url=produto["url"],
                preco=float(ultimo["price"]),
                visto_em=produto["last_seen_at"],
                mediana=float(median(historico)) if historico else None,
                dias=len(historico),
                menor=min(historico) if historico else None,
                no_grupo=enviado[0] if vale else None,
                imagem=produto["image_url"] or None,
            )
    return saida


def historico(product_id: str) -> Historico | None:
    """Menor preco medido e ultimo envio ao grupo, ambos na janela de 60 dias.

    None quando o produto nao esta mais disponivel (ver `detalhes`).
    """
    resultado = detalhes([product_id]).get(product_id)
    if resultado is None:
        return None
    hoje = now().date().isoformat()
    with connect() as bot:
        menor = bot.execute(
            "SELECT price, observed_on FROM price_history WHERE product_id = ? "
            "AND observed_on >= date(?, ?) ORDER BY price, observed_on DESC LIMIT 1",
            (product_id, hoje, f"-{JANELA_DA_MEDIANA} days"),
        ).fetchone()
        dias = bot.execute(
            "SELECT COUNT(*) FROM price_history WHERE product_id = ? "
            "AND observed_on >= date(?, ?)",
            (product_id, hoje, f"-{JANELA_DA_MEDIANA} days"),
        ).fetchone()[0]
        grupo = bot.execute(
            "SELECT sent_at, price FROM posts WHERE product_id = ? "
            "AND status = 'sent' AND sent_at >= ? ORDER BY sent_at DESC LIMIT 1",
            (product_id, _iso(now() - timedelta(days=JANELA_DA_MEDIANA))),
        ).fetchone()
    return Historico(
        resultado=resultado,
        menor_preco=float(menor["price"]) if menor else None,
        menor_em=menor["observed_on"] if menor else None,
        dias=dias,
        grupo_em=grupo["sent_at"] if grupo else None,
        grupo_preco=float(grupo["price"]) if grupo else None,
    )


class Busca:
    def __init__(
        self,
        embeddings: Embeddings | None = None,
        vetores: Vetores | None = None,
        caminho: Path | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._vetores = vetores
        self._caminho = caminho

    def atualizar_vetores(self, vetores: Vetores | None) -> None:
        self._vetores = vetores

    def _semantica(self, consulta: str, k: int = 30) -> list[int]:
        if not (self._embeddings and self._vetores):
            return []
        try:
            vetor = self._embeddings.consulta(consulta)
        except Exception as exc:  # noqa: BLE001 - sem vetor, a busca lexical segue
            log.warning("Embedding da pergunta falhou; so a busca por texto: %s", exc)
            return []
        return [item for item, _ in self._vetores.vizinhos(vetor, k)]

    def _produtos(self, conn: sqlite3.Connection, itens: list[int]) -> list[str]:
        if not itens:
            return []
        marcas = ",".join("?" * len(itens))
        por_item = {
            linha["id"]: linha["product_id"]
            for linha in conn.execute(
                f"SELECT id, product_id FROM itens WHERE id IN ({marcas})", itens
            )
        }
        return [por_item[i] for i in itens if i in por_item]

    def buscar(
        self,
        consulta: str,
        preco_min: float | None = None,
        preco_max: float | None = None,
        limite: int = 3,
    ) -> list[Resultado]:
        """As melhores ofertas para a pergunta, uma por produto.

        O mesmo produto aparece no ML em dezenas de anuncios; `mesmo_produto`
        (o mesmo criterio do cooldown do grupo) deixa so o mais barato deles.
        Entre os mais relevantes, a oferta que saiu no grupo vem primeiro, depois
        a abaixo da mediana, e so entao o resto (`faixa_da_oferta`) -- dentro de
        cada faixa, a ordem de relevancia continua valendo.

        Com faixa de preco a busca olha mais fundo: os 30 mais relevantes podiam
        estar todos acima do teto, e a resposta sairia vazia sem precisar.
        """
        k = 200 if preco_min is not None or preco_max is not None else 60
        semantica = self._semantica(consulta, k)
        with conectar(self._caminho) as conn:
            lexical, completa = busca_lexical(conn, consulta, k)
            ordem = rrf(lexical, semantica)[:k]
            product_ids = self._produtos(conn, ordem)
        achados = detalhes(product_ids)
        fila = (
            CANDIDATOS_COM_TODOS_OS_TERMOS
            if completa
            else CANDIDATOS_COM_PARTE_DOS_TERMOS
        )

        escolhidos: list[Resultado] = []
        for pid in product_ids:
            r = achados.get(pid)
            if r is None:
                continue
            if preco_min is not None and r.preco < preco_min:
                continue
            if preco_max is not None and r.preco > preco_max:
                continue
            if implausivel(r):
                continue
            igual = next(
                (
                    n
                    for n, e in enumerate(escolhidos)
                    if mesmo_modelo(e.titulo, r.titulo)
                ),
                None,
            )
            if igual is None:
                escolhidos.append(r)
            else:
                # Fica um anuncio so do modelo, mas a oferta que saiu no grupo
                # continua valendo para ele. Sem isso, em 18/11/2026 o console
                # de PS5 do grupo (R$ 4.259) virou o anuncio de R$ 3.844 sem a
                # marca, e os acessorios passaram na frente dele.
                atual = escolhidos[igual]
                escolhidos[igual] = replace(
                    melhor_anuncio(atual, r), no_grupo=atual.no_grupo or r.no_grupo
                )
            if len(escolhidos) >= max(fila, limite):
                break
        # A faixa da oferta adianta posicoes, mas nao pula a fila inteira: em
        # 19/11/2026, com a faixa decidindo sozinha, uma camera de seguranca que
        # saiu no grupo e cita "iPhone" no fim do titulo passou na frente do
        # proprio iPhone 15. `sorted` e estavel: empate fica com a relevancia.
        tipo = tipo_pedido(consulta)
        ordem = sorted(
            enumerate(escolhidos),
            key=lambda par: par[0]
            - AVANCO_DA_FAIXA[faixa_da_oferta(par[1])]
            - (AVANCO_DO_TIPO if abre_com_o_tipo(par[1].titulo, tipo) else 0),
        )
        return [r for _, r in ordem][:limite]

    def parecidos(
        self, base: Resultado, limite: int = 2, excluir: set[str] | None = None
    ) -> list[Resultado]:
        """Vizinhos do produto no espaco dos vetores, em faixa de preco parecida.

        Faixa de 40% para cima ou para baixo: alternativa que custa o dobro nao
        e alternativa, e a de metade do preco costuma ser outra categoria.
        """
        if self._vetores is None:
            return []
        excluir = (excluir or set()) | {base.product_id}
        with conectar(self._caminho) as conn:
            linha = conn.execute(
                "SELECT id FROM itens WHERE product_id = ?", (base.product_id,)
            ).fetchone()
            vetor = self._vetores.vetor_de(linha["id"]) if linha else None
            if vetor is None:
                return []
            vizinhos = [item for item, _ in self._vetores.vizinhos(vetor, 40)]
            product_ids = [
                p for p in self._produtos(conn, vizinhos) if p not in excluir
            ]
        achados = detalhes(product_ids)

        saida: list[Resultado] = []
        for pid in product_ids:
            r = achados.get(pid)
            if r is None or not (0.6 * base.preco <= r.preco <= 1.4 * base.preco):
                continue
            if mesmo_modelo(base.titulo, r.titulo):
                continue
            if any(mesmo_modelo(s.titulo, r.titulo) for s in saida):
                continue
            saida.append(r)
            if len(saida) >= limite:
                break
        return saida

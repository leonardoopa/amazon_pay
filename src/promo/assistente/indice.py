"""O indice de busca: os produtos ativos do bot, com texto e vetor.

Duas metades, porque cada uma acerta um tipo de pergunta:

- Texto (FTS5): acerta nome de modelo -- "ps5 slim", "airfryer mondial 4l".
  O tokenizador separa "PlayStation®" em "playstation", que era justamente o
  caso que o `priority` do watchlist nao pegava por comparar pedaco de titulo.
- Vetor (embedding do Gemini): acerta intencao -- "fone pra correr",
  "presente ate 150" -- e erro de digitacao.

Titulo de produto e curto e ja e uma unidade so; nao ha "chunk" a fazer aqui.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import timedelta
from typing import Callable, Iterable

import numpy as np

from ..config import (
    assistente_janela_dias,
    embedding_dim,
    embedding_model,
    gemini_api_key,
)
from ..db import _iso, connect, now, sem_acento

log = logging.getLogger("promo")

# Textos por chamada de embedding. O plano gratuito do Gemini conta cada texto
# como uma requisicao, com teto de 100 por minuto numa janela deslizante. Medido
# em 18/11/2026: lote de 100 levou 429 logo depois do primeiro, e 50 a cada 40 s
# tambem -- dois lotes cabiam nos mesmos 60 s e somavam os 100. Com 40 por lote e
# 45 s de pausa (`servico.PAUSA_ENTRE_LOTES`), nenhuma janela passa de 80. A
# primeira carga de 12 mil produtos leva ~3h45; a busca por texto vale desde o
# primeiro minuto.
LOTE_DE_EMBEDDING = 40

# So com quase todo o catalogo vetorizado a busca por vetor entra. Em 18/11/2026
# o 429 parou a carga em 250 de 12 mil, e a busca usou esses 250: o vizinho "mais
# parecido" de "promocoes de PS5" foi uma figura de Papai Noel.
COBERTURA_MINIMA = 0.9

# A similaridade de cosseno destes vetores tem um piso alto: medido em
# 18/11/2026 com o `gemini-embedding-001` em 256 dimensoes, titulos sem relacao
# nenhuma com a pergunta davam 0,60 a 0,72, e o certo so 0,07 a 0,10 acima
# ("geladeira": 0,79 para a geladeira, 0,71 para um controle de PS5). Corte fixo
# nao separa. O que separou os cinco casos medidos foi exigir a mediana do
# catalogo mais esta margem -- e a pergunta vaga ("presente para mae") passa a
# nao trazer nada pelo vetor, em vez de trazer os mais proximos de qualquer jeito.
MARGEM_SOBRE_A_MEDIANA = 0.06
# Com poucos vetores a mediana nao representa o catalogo; a margem fica desligada.
MINIMO_PARA_A_MARGEM = 20


def sincronizar(
    conn: sqlite3.Connection, janela_dias: int | None = None
) -> tuple[int, int]:
    """Copia para o indice o que o bot viu na janela e tira o que saiu dela.

    Devolve (ativos, removidos). O `id` do item nao muda quando o produto ja
    existia, entao o vetor dele continua valendo enquanto o titulo for o mesmo.
    """
    janela = janela_dias or assistente_janela_dias()
    limite = _iso(now() - timedelta(days=janela))
    with connect() as bot:
        produtos = bot.execute(
            "SELECT id, source, title, last_seen_at FROM products "
            "WHERE last_seen_at >= ?",
            (limite,),
        ).fetchall()

    conn.executemany(
        """
        INSERT INTO itens (product_id, titulo, busca, fonte, visto_em)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(product_id) DO UPDATE SET
            titulo = excluded.titulo,
            busca = excluded.busca,
            fonte = excluded.fonte,
            visto_em = excluded.visto_em
        """,
        [
            (
                p["id"],
                p["title"],
                sem_acento(p["title"]),
                p["source"],
                p["last_seen_at"],
            )
            for p in produtos
        ],
    )
    ativos = {p["id"] for p in produtos}
    fora = [
        (linha["id"],)
        for linha in conn.execute("SELECT id, product_id FROM itens")
        if linha["product_id"] not in ativos
    ]
    conn.executemany("DELETE FROM itens WHERE id = ?", fora)
    # O FTS de conteudo externo nao acompanha a tabela sozinho. Doze mil titulos
    # se reconstroem em menos de um segundo, e assim nao ha gatilho a manter.
    conn.execute("INSERT INTO itens_fts(itens_fts) VALUES ('rebuild')")
    return len(ativos), len(fora)


def normalizar(vetor: Iterable[float]) -> np.ndarray:
    """Vetor de norma 1: o produto escalar vira a similaridade de cosseno.

    O `gemini-embedding-001` so devolve vetor normalizado na dimensao cheia;
    cortado em 256, a norma tem que ser refeita aqui.
    """
    v = np.asarray(list(vetor), dtype=np.float32)
    norma = float(np.linalg.norm(v))
    return v / norma if norma > 0 else v


class Embeddings:
    """Vetores do Gemini: `documentos` para o catalogo, `consulta` para a pergunta.

    Os dois tipos de tarefa existem porque o modelo aproxima a pergunta curta
    do titulo longo; usar o mesmo tipo nos dois lados piora a busca.
    """

    def __init__(self, client=None, modelo: str | None = None, dim: int | None = None):
        self._client = client
        self.modelo = modelo or embedding_model()
        self.dim = dim or embedding_dim()

    @property
    def assinatura(self) -> str:
        """Modelo e dimensao juntos: trocar qualquer um invalida os vetores."""
        return f"{self.modelo}/{self.dim}"

    def _cliente(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=gemini_api_key())
        return self._client

    def _embed(self, textos: list[str], tarefa: str) -> list[np.ndarray]:
        from google.genai import types

        resposta = self._cliente().models.embed_content(
            model=self.modelo,
            contents=textos,
            config=types.EmbedContentConfig(
                task_type=tarefa, output_dimensionality=self.dim
            ),
        )
        return [normalizar(e.values) for e in resposta.embeddings]

    def documentos(self, textos: list[str]) -> list[np.ndarray]:
        return self._embed(textos, "RETRIEVAL_DOCUMENT")

    def consulta(self, texto: str) -> np.ndarray:
        return self._embed([texto], "RETRIEVAL_QUERY")[0]


def vetorizar_pendentes(
    conn: sqlite3.Connection,
    embeddings: Embeddings,
    limite: int | None = None,
    esperar: Callable[[], bool] | None = None,
) -> int:
    """Gera o vetor do item novo ou de titulo trocado. Devolve quantos gerou.

    Grava lote a lote: um 429 no meio da primeira carga nao joga fora o que ja
    foi pago, e a proxima rodada do indexador continua de onde parou. O 429 e
    os outros erros param a rodada e devolvem o que ja foi feito -- antes a
    excecao subia e a contagem saia zerada com 100 vetores gravados.

    `esperar` roda entre um lote e outro (o ritmo do plano gratuito) e devolve
    True para parar, que e como o servico desliga sem esperar a carga acabar.
    """
    sql = (
        "SELECT i.id, i.titulo FROM itens i LEFT JOIN vetores v ON v.id = i.id "
        "WHERE v.id IS NULL OR v.titulo != i.titulo OR v.modelo != ? "
        "ORDER BY i.visto_em DESC"
    )
    parametros: tuple = (embeddings.assinatura,)
    if limite:
        sql += " LIMIT ?"
        parametros += (limite,)
    pendentes = conn.execute(sql, parametros).fetchall()

    feitos = 0
    for inicio in range(0, len(pendentes), LOTE_DE_EMBEDDING):
        if inicio and esperar is not None and esperar():
            break
        lote = pendentes[inicio : inicio + LOTE_DE_EMBEDDING]
        try:
            vetores = embeddings.documentos([linha["titulo"] for linha in lote])
        except Exception as exc:  # noqa: BLE001 - 429 e rede: continua na proxima
            log.warning(
                "Embeddings pararam em %d de %d; continuam na proxima rodada: %s",
                feitos,
                len(pendentes),
                str(exc)[:200],
            )
            break
        conn.executemany(
            "INSERT OR REPLACE INTO vetores (id, titulo, modelo, vetor) "
            "VALUES (?, ?, ?, ?)",
            [
                (linha["id"], linha["titulo"], embeddings.assinatura, v.tobytes())
                for linha, v in zip(lote, vetores)
            ],
        )
        conn.commit()
        feitos += len(lote)
    return feitos


class Vetores:
    """Os vetores do indice em memoria, para a busca por similaridade.

    Busca exata, sem indice aproximado: 12 mil vetores de 256 dimensoes sao
    uma multiplicacao de matriz de poucos milissegundos.
    """

    def __init__(self, ids: np.ndarray, matriz: np.ndarray) -> None:
        self.ids = ids
        self.matriz = matriz
        self._posicao = {int(i): n for n, i in enumerate(ids)}

    def __len__(self) -> int:
        return len(self.ids)

    @classmethod
    def carregar(cls, conn: sqlite3.Connection, assinatura: str) -> "Vetores | None":
        linhas = conn.execute(
            "SELECT id, vetor FROM vetores WHERE modelo = ?", (assinatura,)
        ).fetchall()
        if not linhas:
            return None
        ids = np.array([linha["id"] for linha in linhas], dtype=np.int64)
        matriz = np.vstack(
            [np.frombuffer(linha["vetor"], dtype=np.float32) for linha in linhas]
        )
        return cls(ids, matriz)

    def vetor_de(self, item_id: int) -> np.ndarray | None:
        posicao = self._posicao.get(int(item_id))
        return None if posicao is None else self.matriz[posicao]

    def vizinhos(self, vetor: np.ndarray, k: int = 30) -> list[tuple[int, float]]:
        """Ate `k` itens parecidos de verdade, do mais para o menos parecido.

        "De verdade" e passar da mediana do catalogo por `MARGEM_SOBRE_A_MEDIANA`.
        Sem isso a busca por vetor sempre devolve `k` itens, ate quando o
        catalogo nao tem nada parecido -- e a resposta viria com um celular para
        quem perguntou de PS5.
        """
        if len(self.ids) == 0:
            return []
        notas = self.matriz @ np.asarray(vetor, dtype=np.float32)
        candidatos = np.arange(len(notas))
        if len(notas) >= MINIMO_PARA_A_MARGEM:
            limiar = float(np.median(notas)) + MARGEM_SOBRE_A_MEDIANA
            candidatos = candidatos[notas >= limiar]
        if len(candidatos) == 0:
            return []
        k = min(k, len(candidatos))
        melhores = candidatos[np.argpartition(-notas[candidatos], k - 1)[:k]]
        melhores = melhores[np.argsort(-notas[melhores])]
        return [(int(self.ids[i]), float(notas[i])) for i in melhores]

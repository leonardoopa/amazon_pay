"""API HTTP do bot: health check, estado do banco e disparo de rodada.

O motor continua sendo o loop de fundo -- a API observa e, no maximo, adianta
uma rodada. O loop roda numa thread deste mesmo processo, e nao num container
separado, porque dois processos escrevendo no mesmo SQLite dao `database is
locked` na primeira coincidencia.

Uvicorn ja trata SIGTERM: ele dispara o shutdown do lifespan, que sinaliza o
stop e espera a thread sair. `docker stop` continua encerrando na hora, sem
esperar o sleep de 2h terminar.
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from hmac import compare_digest
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from .config import api_secret, api_worker, run_interval_seconds
from .db import connect, get_meta, hours_since, init_db, stats
from .manual import OfertaInvalida, enfileirar, escrever_texto, preparar_oferta_amazon
from .pipeline import run
from .worker import (
    LAST_ERROR_KEY,
    LAST_RUN_KEY,
    loop,
    start_drenagem,
    start_fonte_rapida,
)

log = logging.getLogger("promo")

# Uma rodada de cada vez. O loop de fundo e o POST /run disputam o mesmo banco
# e a mesma cota do Gemini; sem isso, um `curl` no momento errado duplicaria
# posts. Lock simples basta -- e um processo so.
_lock = threading.Lock()

_stop = threading.Event()
_thread: threading.Thread | None = None


def _guarded_run() -> None:
    """Roda o pipeline se nenhuma outra rodada estiver em andamento."""
    if not _lock.acquire(blocking=False):
        log.warning("Rodada ja em andamento; pulei esta.")
        return
    try:
        run()
    finally:
        _lock.release()


def worker_running() -> bool:
    return _thread is not None and _thread.is_alive()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _thread

    init_db()

    if app.state.worker_enabled:
        intervalo = run_interval_seconds()
        _thread = threading.Thread(
            target=loop,
            args=(_stop, intervalo, _guarded_run),
            name="promo-worker",
            daemon=True,
        )
        _thread.start()
        log.info("Worker iniciado (intervalo de %ds).", intervalo)
        # A entrega vive fora da rodada: ver `pipeline.drenar_continuamente`.
        start_drenagem(_stop)
        # E o ciclo curto dos grupos prioritarios, que depende dela.
        start_fonte_rapida(_stop)
    else:
        log.info("Worker desligado (API_WORKER=false). So a API responde.")

    yield

    _stop.set()
    if _thread is not None:
        # Generoso porque uma rodada em curso pode estar no meio do gotejamento.
        # Passou disso, a thread e daemon e morre com o processo.
        _thread.join(timeout=30)
    log.info("Worker encerrado.")


def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """Protege tudo que nao e /health.

    Sem API_SECRET configurado o endpoint nao abre: responde 503. Deixar
    `/run` aberto seria deixar qualquer um gastar sua cota do Gemini e mandar
    post no grupo.
    """
    esperado = api_secret()
    if not esperado:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API_SECRET nao configurado; endpoint desabilitado.",
        )
    # compare_digest em vez de `!=`: a comparacao de string do Python para no
    # primeiro byte diferente, e o tempo de resposta entrega quantos bytes
    # estao certos. Com a chave em hex, isso reduz a busca de exponencial para
    # linear no tamanho dela.
    if not compare_digest(x_api_key or "", esperado):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key invalido ou ausente.",
        )


class OfertaAmazon(BaseModel):
    """Uma oferta da Amazon escolhida a mao.

    O preco vem de fora porque a Creators API ainda nao liberou e raspar a
    Amazon bate no Operating Agreement do Associates. Quem le o preco e uma
    pessoa -- ver `promo.manual`.
    """

    produto: str = Field(description="URL do produto na Amazon, ou o ASIN")
    titulo: str = Field(description="Titulo que sai no post")
    preco: float = Field(gt=0, description="Preco de agora, em reais")
    de: float = Field(gt=0, description="Preco anunciado antes do desconto")
    tag: str | None = Field(default=None, description="Tag de associado")
    imagem: str | None = Field(default=None, description="URL da imagem")
    sem_imagem: bool = False
    sem_ia: bool = False


def create_app(worker_enabled: bool = True) -> FastAPI:
    app = FastAPI(
        title="amazon_pay",
        description="Bot de ofertas: health check, estado do banco e disparo de rodada.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.worker_enabled = worker_enabled

    @app.get("/health", tags=["infra"])
    def health(response: Response) -> dict[str, Any]:
        """Health check do container. Aberto de proposito -- nao devolve segredo.

        Responde 503 quando o banco nao abre ou quando o worker deveria estar
        vivo e nao esta. E o que o healthcheck do compose consulta.

        Nao expoe texto de excecao. Mensagem de erro de rodada carrega URL
        interna, caminho de arquivo e as vezes o JID do grupo; num endpoint
        aberto isso e reconhecimento gratis para quem estiver olhando. Aqui
        vai so o fato de ter havido erro -- o texto sai no /stats, com chave,
        e no log do container.
        """
        corpo: dict[str, Any] = {
            "status": "ok",
            "worker": "off" if not app.state.worker_enabled else "running",
        }

        try:
            with connect() as conn:
                corpo["last_run_hours_ago"] = hours_since(conn, LAST_RUN_KEY)
                corpo["last_run_failed"] = bool(get_meta(conn, LAST_ERROR_KEY))
            corpo["database"] = "ok"
        except Exception:  # noqa: BLE001 - o health nao pode levantar
            log.exception("Health check falhou no banco")
            corpo["database"] = "erro"
            corpo["status"] = "degraded"

        if app.state.worker_enabled and not worker_running():
            corpo["worker"] = "dead"
            corpo["status"] = "degraded"

        if corpo["status"] == "degraded":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

        return corpo

    @app.get("/stats", tags=["infra"], dependencies=[Depends(require_key)])
    def estado() -> dict[str, Any]:
        """Contagens do `promo stats` e o erro da ultima rodada, em JSON.

        O texto do erro vive aqui, e nao no /health, porque este endpoint
        exige a chave.
        """
        with connect() as conn:
            return {
                **stats(conn),
                "last_run_error": get_meta(conn, LAST_ERROR_KEY) or None,
            }

    @app.post(
        "/run",
        tags=["pipeline"],
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=[Depends(require_key)],
    )
    def disparar() -> dict[str, str]:
        """Adianta uma rodada, fora do intervalo do worker.

        Responde 202 e roda em background: a rodada leva minutos (o gotejamento
        entre posts e proposital) e segurar a conexao HTTP aberta esse tempo so
        renderia timeout no cliente.
        """
        # Pega o lock aqui, e nao dentro da thread, pra poder responder 409 com
        # certeza. Checar `locked()` e so depois disparar deixaria duas
        # requisicoes simultaneas receberem 202 com uma so rodada acontecendo.
        if not _lock.acquire(blocking=False):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ja existe uma rodada em andamento.",
            )

        def rodar_e_liberar() -> None:
            try:
                run()
            except Exception:  # noqa: BLE001 - erro de rodada ja e logado, nao propaga
                log.exception("Rodada manual falhou")
            finally:
                _lock.release()

        threading.Thread(target=rodar_e_liberar, name="promo-run", daemon=True).start()
        return {"status": "aceito", "detalhe": "Rodada iniciada em background."}

    @app.post(
        "/amazon-add",
        tags=["pipeline"],
        status_code=status.HTTP_201_CREATED,
        dependencies=[Depends(require_key)],
    )
    def amazon_add(oferta: OfertaAmazon) -> dict[str, Any]:
        """Enfileira uma oferta da Amazon escolhida a mao.

        Existe para o admin do site poder enfileirar sem escrever no banco do
        bot. O roteador em `web/comunidade/routers.py` proibe essa escrita de
        proposito -- um `.save()` acidental corromperia o historico de preco --
        e a regra vale tambem para o que o dono digita. Entao o site pede, e
        quem grava continua sendo o dono do schema.

        Sincrono, diferente do /run: nao ha gotejamento aqui, so uma insercao.
        Quem chama precisa da resposta para saber se o texto ficou bom.
        """
        try:
            scored = preparar_oferta_amazon(
                produto=oferta.produto,
                titulo=oferta.titulo,
                preco=oferta.preco,
                de=oferta.de,
                tag=oferta.tag,
                imagem=oferta.imagem,
                sem_imagem=oferta.sem_imagem,
            )
        except OfertaInvalida as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc

        texto = escrever_texto(scored, usar_ia=not oferta.sem_ia)
        post_id = enfileirar(scored, texto)
        return {
            "post_id": post_id,
            "asin": scored.offer.external_id,
            "link": scored.offer.url,
            "desconto_pct": round(scored.discount_pct, 1),
            "texto": texto,
        }

    return app


app = create_app(worker_enabled=api_worker())

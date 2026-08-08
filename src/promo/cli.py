"""CLI: python -m promo <comando>"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from .config import (
    MercadoLivreConfig,
    MissingConfig,
    WhatsAppConfig,
    run_interval_seconds,
)
from .db import connect, init_db
from .pipeline import build_sources, collect, flush_pending, load_watchlist, run
from .sources.mercadolivre import run_auth_flow


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_init(_: argparse.Namespace) -> int:
    init_db()
    print("Banco criado.")
    return 0


def cmd_ml_auth(_: argparse.Namespace) -> int:
    init_db()
    run_auth_flow(MercadoLivreConfig.load())
    return 0


def cmd_collect(_: argparse.Namespace) -> int:
    """So coleta e grava historico -- util nos primeiros dias, sem postar nada."""
    init_db()
    from .db import record_offer

    offers = collect(build_sources(), load_watchlist())
    with connect() as conn:
        for offer in offers:
            record_offer(conn, offer)
    print(f"{len(offers)} observacoes de preco gravadas.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    init_db()
    picked = run(dry_run=args.dry_run)
    print(f"\n{len(picked)} ofertas selecionadas.")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    """Popula o banco com historico sintetico, sem depender da API do ML."""
    from . import fixtures

    init_db()
    with connect() as conn:
        if args.clear:
            removed = fixtures.clear(conn)
            print(f"{removed} produtos de demo removidos.")
            return 0
        created = fixtures.seed(conn, days=args.days)
    print(f"{created} produtos SINTETICOS com {args.days} dias de historico.")
    print("Os anuncios nao existem no ML e os links nao abrem -- servem so pra")
    print("exercitar scoring, texto e entrega. Rode `demo` pra ver os textos.")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """Gera os posts a partir dos dados de demo, sem coletar e sem enviar.

    Existe pra calibrar o tom do texto enquanto a conta do Mercado Livre nao
    e liberada -- exercita scoring + Gemini de ponta a ponta.
    """
    from . import fixtures
    from .config import Rules
    from .copywriter import Copywriter, fallback_copy
    from .scoring import score

    init_db()
    rules = Rules.load()

    with connect() as conn:
        scored = [s for s in (score(conn, o, rules) for o in fixtures.current_offers()) if s]

    print("=" * 48)
    print("DADOS SINTETICOS -- os produtos nao existem e os links nao abrem.")
    print("Avalie o tom do texto e o calculo do desconto, nao os links.")
    print("=" * 48)

    if not scored:
        print("Nada pontuou. Rode `seed` primeiro.")
        return 1

    from .db import affiliate_link

    copywriter = None if args.no_ai else Copywriter()
    drafts: list[tuple] = []
    for item in scored:
        # Mostra o link real quando ja existe: um preview com a URL crua
        # esconde exatamente o passo que falta pra oferta render comissao.
        with connect() as conn:
            link = affiliate_link(conn, item.offer.product_id)
        sem_link = link is None
        link = link or item.offer.url
        if copywriter is None:
            text = fallback_copy(item, link)
        else:
            try:
                text = copywriter.write(item, link)
            except Exception as exc:  # noqa: BLE001
                print(f"[Gemini falhou: {exc}]")
                text = fallback_copy(item, link)
        print("\n" + "-" * 48)
        aviso = "  SEM LINK DE AFILIADO -- nao renderia comissao" if sem_link else ""
        print(f"[{item.discount_pct:.0f}% abaixo da media de R$ {item.baseline:.2f}]{aviso}")
        print(text)
        drafts.append((item, text))

    print(f"\n{len(scored)} posts gerados.")

    if not args.send:
        return 0

    # O caminho de entrega e o unico que os testes nao cobrem de verdade:
    # depende da Meta aceitar o token, o numero e a URL da imagem. Mandar 1
    # post real valida isso hoje, sem esperar a baseline amadurecer.
    from .delivery import WhatsApp, WindowClosed

    item, text = drafts[0]
    config = WhatsAppConfig.load()
    whatsapp = WhatsApp(config)
    print(f"\nEnviando 1 post de demo para {config.to}...")
    try:
        whatsapp.send_post(text, item.offer.image_url)
    except WindowClosed:
        print(
            "Janela de 24h fechada. Mande qualquer mensagem para o numero do bot "
            "no WhatsApp e rode de novo.",
            file=sys.stderr,
        )
        return 1
    print("Enviado. Confira se a imagem veio junto do texto, numa mensagem so.")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Loop infinito para o container: roda, dorme, repete.

    Prefiro isso a cron dentro da imagem -- um processo so, logs no stdout e
    SIGTERM do `docker stop` encerra na hora em vez de esperar o sleep.
    """
    init_db()
    interval = args.interval or run_interval_seconds()
    stop = threading.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log = logging.getLogger("promo")
    log.info("Daemon iniciado (intervalo de %ds). Modo: %s", interval,
             "coleta apenas" if args.collect_only else "pipeline completo")

    while not stop.is_set():
        try:
            if args.collect_only:
                cmd_collect(args)
            else:
                run()
        except MissingConfig as exc:
            log.error("Configuracao faltando: %s", exc)
            return 2
        except Exception as exc:  # noqa: BLE001 - o daemon nao pode morrer por 1 erro
            log.exception("Rodada falhou: %s", exc)

        stop.wait(interval)

    log.info("Daemon encerrado.")
    return 0


def cmd_flush(_: argparse.Namespace) -> int:
    init_db()
    flush_pending()
    return 0


def cmd_test_whatsapp(_: argparse.Namespace) -> int:
    from .delivery import WhatsApp, WindowClosed

    whatsapp = WhatsApp(WhatsAppConfig.load())
    try:
        whatsapp.send_text("Teste do bot de ofertas. Se chegou, ta funcionando.")
        print("Mensagem enviada.")
    except WindowClosed:
        print(
            "Janela de 24h fechada. Mande qualquer mensagem para o numero do bot "
            "no WhatsApp e rode de novo."
        )
        return 1
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    init_db()
    with connect() as conn:
        products = conn.execute("SELECT COUNT(*) c FROM products").fetchone()["c"]
        points = conn.execute("SELECT COUNT(*) c FROM price_history").fetchone()["c"]
        ready = conn.execute(
            """
            SELECT COUNT(*) c FROM (
                SELECT product_id FROM price_history
                GROUP BY product_id HAVING COUNT(*) >= 7
            )
            """
        ).fetchone()["c"]
        sent = conn.execute(
            "SELECT COUNT(*) c FROM posts WHERE status = 'sent'"
        ).fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) c FROM posts WHERE status = 'pending'"
        ).fetchone()["c"]

    print(f"Produtos monitorados : {products}")
    print(f"Pontos de preco      : {points}")
    print(f"Com baseline pronta  : {ready}  (>= 7 dias de historico)")
    print(f"Posts enviados       : {sent}")
    print(f"Posts na fila        : {pending}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    """Guarda um link de afiliado gerado a mao no Link Builder do ML."""
    from .db import save_affiliate_link

    init_db()
    if not args.url.startswith("https://"):
        print("O link precisa comecar com https://", file=sys.stderr)
        return 2

    with connect() as conn:
        # Aceita tanto o ID do anuncio ("MLB123") quanto o interno
        # ("mercadolivre:MLB123"), porque e o primeiro que voce copia do
        # `pending-links` e da URL do produto.
        if ":" in args.product_id:
            rows = conn.execute(
                "SELECT id FROM products WHERE id = ?", (args.product_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id FROM products WHERE external_id = ?", (args.product_id,)
            ).fetchall()

        if not rows:
            print(
                f"Anuncio {args.product_id} nao esta no banco. Rode `collect` primeiro.",
                file=sys.stderr,
            )
            return 2
        if len(rows) > 1:
            nomes = ", ".join(row["id"] for row in rows)
            print(f"ID ambiguo entre fontes ({nomes}). Passe o ID completo.", file=sys.stderr)
            return 2

        product_id = rows[0]["id"]
        save_affiliate_link(conn, product_id, args.url)

    print(f"Link salvo para {product_id}.")
    return 0


def cmd_pending_links(args: argparse.Namespace) -> int:
    """Lista produtos rastreados que ainda nao tem link de afiliado.

    O ML so emite link pelo Link Builder (desktop), entao esse e o passo
    manual do fluxo: gere os links dos que estao mais perto de virar post.
    """
    from .db import products_missing_link

    init_db()
    with connect() as conn:
        rows = products_missing_link(conn, args.limit)

    if not rows:
        print("Todo produto rastreado ja tem link.")
        return 0

    print(f"{len(rows)} produto(s) sem link de afiliado (mais historico primeiro):\n")
    for row in rows:
        print(f"{row['dias']:>3} dias  {row['external_id']}  {row['title'][:50]}")
        print(f"           {row['url']}\n")
    print("Gere em mercadolivre.com.br/afiliados/linkbuilder e salve com:")
    print("  promo link <ID> <link>")
    return 0


COMMANDS = {
    "init": (cmd_init, "Cria o banco SQLite"),
    "ml-auth": (cmd_ml_auth, "Autoriza o app no Mercado Livre (abre o navegador)"),
    "collect": (cmd_collect, "So coleta precos, sem postar (use nos primeiros dias)"),
    "run": (cmd_run, "Coleta, filtra, escreve e envia no WhatsApp"),
    "seed": (cmd_seed, "Cria historico sintetico pra testar sem a API do ML"),
    "demo": (cmd_demo, "Gera os posts a partir do historico sintetico"),
    "daemon": (cmd_daemon, "Roda em loop (usado pelo container)"),
    "flush": (cmd_flush, "Reenvia os posts que ficaram na fila"),
    "link": (cmd_link, "Salva o link de afiliado de um produto"),
    "pending-links": (cmd_pending_links, "Lista produtos sem link de afiliado"),
    "test-whatsapp": (cmd_test_whatsapp, "Manda uma mensagem de teste pra voce"),
    "stats": (cmd_stats, "Mostra o estado do banco"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promo", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Aceita `-v` antes e depois do subcomando: `promo collect -v` e a ordem
    # que a mao escreve sozinha. O SUPPRESS e essencial -- sem ele o default
    # do subparser sobrescreveria com False um `-v` dado la na frente.
    verboso = argparse.ArgumentParser(add_help=False)
    verboso.add_argument(
        "-v", "--verbose", action="store_true", default=argparse.SUPPRESS
    )

    for name, (_, help_text) in COMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text, parents=[verboso])
        if name == "run":
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="Imprime os textos no terminal em vez de enviar",
            )
        if name == "seed":
            sub.add_argument("--days", type=int, default=60, help="Dias de historico")
            sub.add_argument(
                "--clear", action="store_true", help="Remove os dados de demo"
            )
        if name == "demo":
            sub.add_argument(
                "--no-ai",
                action="store_true",
                help="Usa o texto padrao em vez de chamar o Gemini",
            )
            sub.add_argument(
                "--send",
                action="store_true",
                help="Envia 1 post de demo no WhatsApp pra validar a entrega",
            )
        if name == "link":
            sub.add_argument("product_id", help="ID do anuncio, ex.: MLB3953571145")
            sub.add_argument("url", help="Link gerado no Link Builder do ML")
        if name == "pending-links":
            sub.add_argument(
                "--limit", type=int, default=20, help="Quantos produtos listar"
            )
        if name == "daemon":
            sub.add_argument(
                "--interval",
                type=int,
                default=None,
                help="Segundos entre rodadas (padrao: RUN_INTERVAL_SECONDS)",
            )
            sub.add_argument(
                "--collect-only",
                action="store_true",
                help="So coleta preco, nao posta -- use nos primeiros 7-10 dias",
            )

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    handler, _ = COMMANDS[args.command]
    try:
        return handler(args)
    except MissingConfig as exc:
        print(f"Configuracao faltando: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

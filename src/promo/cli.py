"""CLI: python -m promo <comando>"""

from __future__ import annotations

import argparse
import logging
import sys

from .config import MercadoLivreConfig, MissingConfig, WhatsAppConfig
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


COMMANDS = {
    "init": (cmd_init, "Cria o banco SQLite"),
    "ml-auth": (cmd_ml_auth, "Autoriza o app no Mercado Livre (abre o navegador)"),
    "collect": (cmd_collect, "So coleta precos, sem postar (use nos primeiros dias)"),
    "run": (cmd_run, "Coleta, filtra, escreve e envia no WhatsApp"),
    "flush": (cmd_flush, "Reenvia os posts que ficaram na fila"),
    "test-whatsapp": (cmd_test_whatsapp, "Manda uma mensagem de teste pra voce"),
    "stats": (cmd_stats, "Mostra o estado do banco"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="promo", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, (_, help_text) in COMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text)
        if name == "run":
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="Imprime os textos no terminal em vez de enviar",
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

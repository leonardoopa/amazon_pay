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
    api_host,
    api_port,
    api_secret,
    run_interval_seconds,
)
from .db import connect, init_db, stats
from .pipeline import (
    build_sources,
    collect,
    flush_pending,
    load_categories,
    load_watchlist,
    run,
)
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


def cmd_ml_probe(_: argparse.Namespace) -> int:
    """Diz quais endpoints do ML respondem NESTA conta, agora.

    O ML foi fechando a API publica endpoint por endpoint e sem anunciar, entao
    a doc nao serve de referencia. Antes de mudar a coleta, meça.
    """
    from .sources.mercadolivre import probe

    init_db()
    resultados = probe(MercadoLivreConfig.load())

    for rotulo, status, resumo in resultados:
        marca = "ok  " if status == 200 else ("--  " if status == 0 else "FALHA")
        codigo = str(status) if status else "---"
        print(f"{marca} {codigo:>4}  {rotulo}")
        print(f"            {resumo}")

    funcionando = sum(1 for _, status, _ in resultados if status == 200)
    print(f"\n{funcionando} de {len(resultados)} endpoints respondendo.")
    return 0 if funcionando else 1


def cmd_collect(_: argparse.Namespace) -> int:
    """So coleta e grava historico -- util nos primeiros dias, sem postar nada."""
    init_db()
    from .db import record_offer

    offers = collect(build_sources(), load_watchlist(), categories=load_categories())
    with connect() as conn:
        for offer in offers:
            record_offer(conn, offer)
    print(f"{len(offers)} observacoes de preco gravadas.")
    return 0


def cmd_ml_categories(_: argparse.Namespace) -> int:
    """Lista as categorias raiz do site, com os IDs pro watchlist.json."""
    from .sources.mercadolivre import MercadoLivre

    init_db()
    for category_id, nome in MercadoLivre(MercadoLivreConfig.load()).categories():
        print(f"{category_id:<10} {nome}")
    print("\nAcompanhe os mais vendidos colocando em watchlist.json:")
    print('  "categories": [{"id": "MLB1051", "name": "Celulares", "max_price": 3000}]')
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
        scored = [
            s for s in (score(conn, o, rules) for o in fixtures.current_offers()) if s
        ]

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
        print(
            f"[{item.discount_pct:.0f}% abaixo da media de R$ {item.baseline:.2f}]{aviso}"
        )
        print(text)
        drafts.append((item, text))

    print(f"\n{len(scored)} posts gerados.")

    if not args.send:
        return 0

    # O caminho de entrega e o unico que os testes nao cobrem de verdade:
    # depende da Meta aceitar o token, o numero e a URL da imagem. Mandar 1
    # post real valida isso hoje, sem esperar a baseline amadurecer.
    from .config import delivery_backend
    from .delivery import NotConnected, WindowClosed, build_delivery

    item, text = drafts[0]
    delivery = build_delivery()
    destino = getattr(delivery.config, "group_jid", None) or getattr(
        delivery.config, "to", "?"
    )
    print(f"\nEnviando 1 post de demo via {delivery_backend()} para {destino}...")
    try:
        delivery.send_post(text, item.offer.image_url)
    except WindowClosed:
        print(
            "Janela de 24h fechada. Mande qualquer mensagem para o numero do bot "
            "no WhatsApp e rode de novo.",
            file=sys.stderr,
        )
        return 1
    except NotConnected as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Enviado. Confira se a imagem veio junto do texto, numa mensagem so.")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Loop infinito sem HTTP: roda, dorme, repete.

    Prefiro isso a cron dentro da imagem -- um processo so, logs no stdout e
    SIGTERM do `docker stop` encerra na hora em vez de esperar o sleep.

    O container padrao hoje sobe pelo `serve`, que roda este mesmo loop e ainda
    responde /health. Este comando fica pra quem nao quer porta aberta.
    """
    from .worker import loop

    init_db()
    interval = args.interval or run_interval_seconds()
    stop = threading.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    log = logging.getLogger("promo")
    log.info(
        "Daemon iniciado (intervalo de %ds). Modo: %s",
        interval,
        "coleta apenas" if args.collect_only else "pipeline completo",
    )

    tarefa = (lambda: cmd_collect(args)) if args.collect_only else run
    codigo = loop(stop, interval, tarefa)

    log.info("Daemon encerrado.")
    return codigo


def cmd_serve(args: argparse.Namespace) -> int:
    """Sobe a API (health check + /run) com o loop de coleta na mesma thread pool.

    E o comando do container. O uvicorn trata SIGTERM e o lifespan da API para
    o loop, entao `docker stop` continua encerrando na hora.
    """
    try:
        import uvicorn
    except ModuleNotFoundError:
        print(
            "uvicorn nao instalado. Rode: pip install -e '.[api]'",
            file=sys.stderr,
        )
        return 2

    host = args.host or api_host()
    port = args.port or api_port()

    if not api_secret():
        print(
            "Aviso: API_SECRET vazio -- /run e /stats vao responder 503. "
            "Defina no .env pra habilitar.",
            file=sys.stderr,
        )

    uvicorn.run("promo.api:app", host=host, port=port, log_config=None)
    return 0


def cmd_flush(_: argparse.Namespace) -> int:
    init_db()
    flush_pending()
    return 0


def cmd_test_whatsapp(_: argparse.Namespace) -> int:
    from .delivery import NotConnected, WindowClosed, build_delivery

    texto = "Teste do bot de ofertas. Se chegou, ta funcionando."
    try:
        build_delivery().send_post(texto)
        print("Mensagem enviada.")
    except WindowClosed:
        print(
            "Janela de 24h fechada. Mande qualquer mensagem para o numero do bot "
            "no WhatsApp e rode de novo."
        )
        return 1
    except NotConnected as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def cmd_wa_connect(args: argparse.Namespace) -> int:
    """Cria a instancia e emite o codigo pra parear o numero do bot.

    Parear e um passo humano e presencial: o codigo expira em menos de um
    minuto. Nao da pra automatizar, e nem deveria -- e o unico momento em que
    voce confirma qual numero vai assinar os posts.
    """
    # group_jid so e conhecido DEPOIS de parear (sai do wa-groups), entao o
    # .load() completo falharia justamente em quem ainda nao pareou.
    evolution = _evolution_sem_grupo()

    print(evolution.create_instance().get("status", "instancia criada"))
    estado = evolution.state()
    if estado == "open":
        print("Numero ja pareado. Rode `promo wa-groups` pra pegar o JID do grupo.")
        return 0

    numero = (args.number or "").lstrip("+").replace(" ", "").replace("-", "")
    dados = evolution.connect(numero or None)

    # `pairingCode` so vem preenchido em algumas versoes/fluxos -- na 2.3.7 ele
    # volta null mesmo passando o numero. Nao caia pro campo `code`: ele e o
    # conteudo do QR, uma string de 200+ chars que ninguem digita.
    if dados.get("pairingCode"):
        print(f"\nNo WhatsApp do numero +{numero}:")
        print("  Aparelhos conectados > Conectar aparelho > Conectar com numero")
        print(f"\n  CODIGO: {dados['pairingCode']}\n")
        print("Expira em menos de um minuto. Se perder, rode de novo.")
        return 0

    # Salvar o QR num PNG parece obvio e NAO funciona: a Evolution rotaciona o
    # QR a cada ~30s e cada novo invalida o anterior. Entre gerar o arquivo,
    # abrir a imagem e apontar a camera, o codigo ja morreu -- e o WhatsApp
    # responde "nao foi possivel conectar", que parece erro de celular.
    # O painel da propria Evolution renova o QR na tela; use ele.
    painel = f"{_evolution_sem_grupo().config.base_url.rstrip('/')}/manager"
    print(f"\nAbra o painel: {painel}")
    print("  1. Server URL: a mesma acima, sem /manager")
    print("  2. API Key: o valor de EVOLUTION_API_KEY do seu .env")
    print(
        f"  3. Clique na instancia '{_evolution_sem_grupo().config.instance}' e conecte"
    )
    print("\nNo celular: Aparelhos conectados > Conectar aparelho.")
    print("O QR do painel se renova sozinho, entao da pra tentar quantas vezes quiser.")
    print("\nPareado? Rode `promo wa-groups --search <nome do grupo>`.")
    return 0


def cmd_wa_logout(_: argparse.Namespace) -> int:
    """Desconecta o numero pareado, sem apagar a instancia.

    Existe pro momento de trocar o numero de teste pelo chip dedicado: desloga,
    e o proximo `wa-connect` pareia outro numero na mesma instancia.
    """
    evolution = _evolution_sem_grupo()
    if evolution.state() != "open":
        print("Nenhum numero conectado.")
        return 0

    evolution.logout()
    print("Numero desconectado. O aparelho tambem some de 'Aparelhos conectados'.")
    print("Pra parear outro: `promo wa-connect`, e atualize EVOLUTION_GROUP_JID.")
    return 0


def _evolution_sem_grupo():
    """Evolution antes de existir grupo configurado (usado no pareamento)."""
    from .config import EvolutionConfig, _get, _optional
    from .delivery import Evolution

    return Evolution(
        EvolutionConfig(
            base_url=_optional("EVOLUTION_BASE_URL", "http://localhost:8080"),
            api_key=_get("EVOLUTION_API_KEY"),
            instance=_optional("EVOLUTION_INSTANCE", "ofertas"),
            group_jid="",
        )
    )


def cmd_wa_groups(args: argparse.Namespace) -> int:
    """Acha o JID do grupo de ofertas pra colar no .env.

    O `--search` nao e conveniencia: quando o numero pareado e pessoal, a
    listagem crua joga no terminal (e no log) o nome de todo grupo privado de
    que a pessoa participa. Filtrar por nome mostra so o que interessa.
    """
    grupos = _evolution_sem_grupo().groups()
    if not grupos:
        print("Nenhum grupo. O numero pareado precisa ser membro do grupo de ofertas.")
        return 1

    total = len(grupos)
    if args.search:
        alvo = args.search.lower()
        grupos = [g for g in grupos if alvo in (g.get("subject") or "").lower()]
        if not grupos:
            print(f"Nenhum dos {total} grupos casa com '{args.search}'.")
            print("Confira o nome exato, ou rode sem --search pra ver todos.")
            return 1

    for grupo in grupos:
        membros = grupo.get("size", "?")
        print(f"{grupo.get('subject', 'sem nome')}  ({membros} membros)")
        print(f"  {grupo.get('id')}\n")

    if args.search:
        print(f"({len(grupos)} de {total} grupos; os outros nao foram exibidos)")
    print("Cole o ID do grupo certo em EVOLUTION_GROUP_JID no .env.")
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    init_db()
    with connect() as conn:
        s = stats(conn)

    print(f"Produtos monitorados : {s['products']}")
    print(f"Pontos de preco      : {s['price_points']}")
    print(f"Com baseline pronta  : {s['with_baseline']}  (>= 7 dias de historico)")
    print(f"Posts enviados       : {s['posts_sent']}")
    print(f"Posts na fila        : {s['posts_pending']}")
    print(f"Posts com falha      : {s['posts_failed']}")
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
            print(
                f"ID ambiguo entre fontes ({nomes}). Passe o ID completo.",
                file=sys.stderr,
            )
            return 2

        product_id = rows[0]["id"]
        save_affiliate_link(conn, product_id, args.url)

    print(f"Link salvo para {product_id}.")
    return 0


def cmd_amazon_add(args: argparse.Namespace) -> int:
    """Enfileira uma oferta da Amazon escolhida a mao.

    Existe porque a Creators API ainda nao liberou -- ela pede vendas
    qualificadas na conta Associates -- e raspar a Amazon nao e alternativa: o
    Operating Agreement proibe "data mining, robots, or similar data gathering
    and extraction tools" e exige que preco exibido venha da API. O risco de
    raspar cairia sobre a mesma conta que precisa sobreviver ate liberar.

    Entao a escolha e o preco sao de uma pessoa, e o resto e automatico: link
    com a tag, imagem por ASIN, texto pelo copywriter e a fila de sempre, com o
    mesmo gotejamento. Poucos posts por semana bastam para as vendas que
    liberam a API -- e quando liberar, `sources/amazon.py` ja esta pronto e
    nada disto vira lixo.

    O post sai `verified=False`: o "de" e o que a loja anuncia, nao a nossa
    mediana. Mesma marcacao do repasse da vitrine do ML.
    """
    from .config import amazon_partner_tag
    from .copywriter import Copywriter, fallback_copy
    from .db import create_post, recent_headlines, record_offer, save_affiliate_link
    from .models import Offer, ScoredOffer
    from .sources.amazon_link import extrair_asin, imagem_do_asin, link_de_afiliado

    tag = args.tag or amazon_partner_tag()
    if not tag:
        print(
            "Sem tag de associado. Passe --tag ou defina AMAZON_PARTNER_TAG no .env.",
            file=sys.stderr,
        )
        return 2

    asin = extrair_asin(args.produto)
    if not asin:
        print(
            f"Nao achei o ASIN em {args.produto!r}. Passe a URL do produto "
            "(com /dp/ASIN) ou o ASIN de dez caracteres.",
            file=sys.stderr,
        )
        return 2

    if args.preco <= 0 or args.de <= 0:
        print("Preco e 'de' precisam ser maiores que zero.", file=sys.stderr)
        return 2
    if args.preco >= args.de:
        print(
            f"O preco (R$ {args.preco:.2f}) precisa ser MENOR que o de "
            f"(R$ {args.de:.2f}) -- sem queda nao ha oferta.",
            file=sys.stderr,
        )
        return 2

    desconto = (args.de - args.preco) / args.de * 100
    link = link_de_afiliado(asin, tag)
    offer = Offer(
        source="amazon",
        external_id=asin,
        title=args.titulo.strip(),
        price=args.preco,
        url=link,
        original_price=args.de,
        image_url=None if args.sem_imagem else (args.imagem or imagem_do_asin(asin)),
    )
    scored = ScoredOffer(
        offer=offer,
        baseline=args.de,
        discount_pct=desconto,
        observations=0,
        lowest_ever=False,
        verified=False,
    )

    init_db()
    with connect() as conn:
        record_offer(conn, offer)
        save_affiliate_link(conn, offer.product_id, link)
        recentes = recent_headlines(conn)

    if args.sem_ia:
        texto = fallback_copy(scored, link)
    else:
        try:
            texto = Copywriter().write(scored, link, None, recentes)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("promo").warning(
                "Gemini falhou (%s); usando o texto padrao.", exc
            )
            texto = fallback_copy(scored, link)

    if args.dry_run:
        print(texto)
        print()
        print(f"(dry-run: nada foi enfileirado. Imagem: {offer.image_url or 'nenhuma'})")
        return 0

    with connect() as conn:
        post_id = create_post(
            conn,
            offer.product_id,
            offer.price,
            scored.baseline,
            scored.discount_pct,
            texto,
            offer.image_url,
            verified=False,
        )

    print(f"Post {post_id} na fila: {asin} a R$ {args.preco:.2f} (-{desconto:.0f}%)")
    print(f"Link: {link}")
    print("Sai no proximo gotejamento, ou rode `promo flush` para drenar agora.")
    return 0


def cmd_link_all(args: argparse.Namespace) -> int:
    """Gera de uma vez os links que faltam, pelo painel de afiliados.

    Em lote porque o endpoint aceita varias URLs por chamada -- gerar um a um
    seria uma requisicao (e um risco de rate limit) por produto.
    """
    from .db import mark_affiliate_blocked, products_missing_link, save_affiliate_link
    from .sources.ml_linkbuilder import LinkBuilder, SessionExpired

    init_db()
    config = MercadoLivreConfig.load()
    if not (config.affiliate_cookie and config.affiliate_tag):
        print(
            "ML_AFFILIATE_COOKIE / ML_AFFILIATE_TAG nao configurados -- a geracao "
            "automatica esta desligada. Veja o README, ou siga com `promo link`.",
            file=sys.stderr,
        )
        return 2

    with connect() as conn:
        faltando = products_missing_link(conn, args.limit)

    # So Mercado Livre: o link da Amazon e a URL do produto com a partner tag,
    # montada pela propria fonte, e nao passa por painel nenhum.
    faltando = [row for row in faltando if row["id"].startswith("mercadolivre:")]
    if not faltando:
        print("Todo produto rastreado do ML ja tem link.")
        return 0

    builder = LinkBuilder(config.affiliate_cookie, config.affiliate_tag)
    try:
        resultado = builder.create([row["url"] for row in faltando])
    except SessionExpired as exc:
        print(exc, file=sys.stderr)
        return 1

    salvos, recusados = 0, []
    with connect() as conn:
        for row in faltando:
            link = resultado.links.get(row["url"])
            if link:
                save_affiliate_link(conn, row["id"], link)
                salvos += 1
                continue
            motivo = resultado.recusados.get(row["url"])
            if motivo:
                # Anotado pra nao voltar a pedir: inelegivel nao muda de ideia
                # entre uma rodada e outra.
                mark_affiliate_blocked(conn, row["id"], motivo)
                recusados.append((row["external_id"], row["title"][:40], motivo))

    print(f"{salvos} link(s) gerados e salvos.")

    if recusados:
        # Silenciar isso faria o backfill parecer completo. E o motivo importa:
        # "URL not allowed" e regra do programa, nao erro seu.
        print(f"\n{len(recusados)} fora do programa de afiliados:")
        for external_id, titulo, motivo in recusados:
            print(f"  {external_id}  {titulo}")
            print(f"    {motivo}")

    restante = len(faltando) - salvos - len(recusados)
    if restante:
        print(f"\n{restante} produto(s) sem resposta do painel -- tente de novo.")
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
    "ml-probe": (cmd_ml_probe, "Testa quais endpoints do ML ainda respondem"),
    "ml-categories": (cmd_ml_categories, "Lista as categorias do site e seus IDs"),
    "collect": (cmd_collect, "So coleta precos, sem postar (use nos primeiros dias)"),
    "run": (cmd_run, "Coleta, filtra, escreve e envia no WhatsApp"),
    "seed": (cmd_seed, "Cria historico sintetico pra testar sem a API do ML"),
    "demo": (cmd_demo, "Gera os posts a partir do historico sintetico"),
    "daemon": (cmd_daemon, "Roda em loop, sem HTTP"),
    "serve": (cmd_serve, "Sobe a API (health check + /run) com o loop junto"),
    "flush": (cmd_flush, "Reenvia os posts que ficaram na fila"),
    "link": (cmd_link, "Salva o link de afiliado de um produto (manual)"),
    "amazon-add": (
        cmd_amazon_add,
        "Enfileira uma oferta da Amazon escolhida a mao (sem a API)",
    ),
    "link-all": (cmd_link_all, "Gera pelo painel os links que faltam, em lote"),
    "pending-links": (cmd_pending_links, "Lista produtos sem link de afiliado"),
    "wa-connect": (cmd_wa_connect, "Pareia o chip secundario na Evolution (QR)"),
    "wa-groups": (cmd_wa_groups, "Acha o JID do grupo (use --search)"),
    "wa-logout": (cmd_wa_logout, "Desconecta o numero pareado da Evolution"),
    "test-whatsapp": (
        cmd_test_whatsapp,
        "Manda uma mensagem de teste pelo backend ativo",
    ),
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
        if name == "amazon-add":
            sub.add_argument(
                "produto", help="URL do produto na Amazon, ou o ASIN (ex.: B07DVJC66X)"
            )
            sub.add_argument("--titulo", required=True, help="Titulo do produto")
            sub.add_argument(
                "--preco", type=float, required=True, help="Preco de agora, em reais"
            )
            sub.add_argument(
                "--de",
                type=float,
                required=True,
                help="Preco anunciado antes do desconto, em reais",
            )
            sub.add_argument(
                "--tag", default=None, help="Tag de associado (padrao: AMAZON_PARTNER_TAG)"
            )
            sub.add_argument(
                "--imagem", default=None, help="URL da imagem (padrao: a do ASIN)"
            )
            sub.add_argument(
                "--sem-imagem",
                action="store_true",
                help="Manda so texto, sem foto",
            )
            sub.add_argument(
                "--sem-ia",
                action="store_true",
                help="Usa o texto padrao em vez de chamar o Gemini",
            )
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="Mostra o texto e nao enfileira nada",
            )
        if name == "wa-groups":
            sub.add_argument(
                "--search",
                default=None,
                help="Mostra so os grupos cujo nome contem este texto",
            )
        if name == "wa-connect":
            sub.add_argument(
                "--number",
                help="Numero do bot em E.164 sem '+' (ex.: 5581996257747)",
            )
        if name in ("pending-links", "link-all"):
            sub.add_argument(
                "--limit", type=int, default=20, help="Quantos produtos processar"
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
        if name == "serve":
            sub.add_argument(
                "--host", default=None, help="Interface (padrao: API_HOST)"
            )
            sub.add_argument(
                "--port", type=int, default=None, help="Porta (padrao: API_PORT)"
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

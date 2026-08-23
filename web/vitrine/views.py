"""Views da vitrine.

O banco do bot pode não existir ainda (site no ar antes da primeira coleta),
então tudo que lê de lá degrada para vazio em vez de estourar 500. Uma landing
sem feed converte; uma landing quebrada, não.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from django.contrib import messages
from django.db.utils import DatabaseError, OperationalError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render

from .forms import InscricaoForm
from .models import Grupo, HistoricoPreco, Post, Produto
from .precos import montar_curva

# Quantos dias de histórico o gráfico mostra.
JANELA_DIAS = 60

# O catálogo de demonstração (`semear_demo`) entra no mesmo banco do bot, mas
# os preços dele são inventados — está escrito em src/promo/fixtures.py. Por
# isso ele fica fora de tudo que o site apresenta como prova: os contadores da
# home, o /saude e as frases da página de detalhe que afirmam coleta diária.
FONTE_DEMO = "demo"

# Os três produtos da vitrine que rola (#prova), na ordem em que aparecem.
#
# É lista fixa de propósito. Cada um desses tem um filme produzido à mão
# (`static/vitrine/video/produto/<id>.mp4`), e deixar a coleta escolher a
# vitrine faz esse trabalho sumir na primeira oferta nova com desconto maior
# — que foi exatamente o que aconteceu. Oferta nova entra pela esteira e pela
# seção "As últimas que passaram no filtro"; aqui, não.
#
# Trocar um produto daqui pede também o filme dele com o id no nome.
VITRINE_FIXA = (
    "MLB46211942",  # relógio — Smartwatch Huawei Band 10
    "MLB52052995",  # air fryer — Philco 6,5L
    "MLB46470846",  # notebook — Vaio FE16
)


def _seguro(consulta, padrao):
    """Executa uma leitura do banco do bot tolerando ele não existir ainda."""
    try:
        return consulta()
    except (OperationalError, DatabaseError, sqlite3.Error):
        return padrao


def _ofertas(limite: int = 12) -> list[dict]:
    """Últimas ofertas enviadas, já casadas com o produto."""
    posts = _seguro(lambda: list(Post.objects.publicados()[:limite]), [])
    if not posts:
        return []

    ids = {post.product_id for post in posts}
    produtos = _seguro(
        lambda: {p.id: p for p in Produto.objects.filter(id__in=ids)}, {}
    )

    ofertas = []
    for post in posts:
        produto = produtos.get(post.product_id)
        if produto is None:
            continue  # produto sumiu do catálogo; o post perde o contexto
        ofertas.append(
            {
                "post": post,
                "produto": produto,
                "external_id": produto.external_id,
                "quando": _quando(post.sent_at or post.created_at),
                # O card muda de texto quando o desconto é da loja, não nosso:
                # o mesmo layout com o rótulo "média" em cima do "de/por" do
                # anúncio é a mentira que a página inteira acusa a loja de
                # contar.
                "verificado": post.verified,
            }
        )
    return ofertas


def _quando(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _numeros() -> dict:
    """Contadores da prova social. Só números reais — nada inflado.

    O catálogo de demonstração fica de fora: ele existe para a vitrine ter
    produto com filme, e contar preço inventado como "preço registrado" é
    exatamente a mentira que o site acusa a loja de contar. O /saude lê
    daqui também, então o monitoramento continua enxergando só o real.
    """
    produtos = _seguro(lambda: Produto.objects.exclude(source=FONTE_DEMO).count(), 0)
    observacoes = _seguro(
        lambda: HistoricoPreco.objects.using("promos")
        .exclude(product_id__startswith=f"{FONTE_DEMO}:")
        .count(),
        0,
    )
    # `medidos()` e não `publicados()`: o rótulo na tela diz "aprovadas no
    # filtro" e "abaixo da mediana". Repasse da vitrine do ML não passou por
    # filtro nenhum nosso — a loja escolheu o preço riscado — e contá-lo aqui
    # seria vender o número da loja como medição própria.
    enviados = _seguro(lambda: Post.objects.medidos().count(), 0)
    economia = 0.0
    for post in _seguro(lambda: list(Post.objects.medidos()[:500]), []):
        economia += post.economia
    return {
        "produtos": produtos,
        "observacoes": observacoes,
        "ofertas_enviadas": enviados,
        "economia_total": economia,
    }


def _historico(produto) -> list[float]:
    return list(
        _seguro(
            lambda: list(
                HistoricoPreco.objects.using("promos")
                .filter(product_id=produto.id)
                .order_by("observed_on")
                .values_list("price", flat=True)
            ),
            [],
        )
    )[-JANELA_DIAS:]


def _com_curva(ofertas: list[dict], quantas: int) -> list[dict]:
    """As de maior desconto que já têm histórico para desenhar.

    São elas que sustentam a promessa do site — sem gráfico, o card vira só
    mais um "de/por" e o argumento inteiro cai.

    Repasse da vitrine do ML não entra: o número grande do topo (`-29%`) e a
    curva ao lado dele afirmam que a queda foi medida por nós. Num repasse o
    desconto é contra o preço que a loja riscou, e o produto pode nem ter
    histórico — a mesma tela diria a coisa errada com mais destaque do que
    qualquer outra na página.
    """
    escolhidas = []
    medidas = [item for item in ofertas if item.get("verificado", True)]
    for item in sorted(medidas, key=lambda o: -o["post"].discount_pct):
        precos = _historico(item["produto"])
        curva = montar_curva(precos)
        if curva is None:
            continue
        escolhidas.append(
            {
                **item,
                "curva": curva,
                "dias": len(precos),
                "video": _video_do_produto(item["external_id"]),
            }
        )
        if len(escolhidas) == quantas:
            break
    return escolhidas


def _produto_por_external_id(external_id: str):
    """Acha o produto pelo id da loja, preferindo o coletado ao de demonstração.

    O mesmo `external_id` pode existir duas vezes: uma linha `demo:` semeada
    à mão e outra que a coleta trouxe depois (a watchlist tem "smartwatch",
    "notebook" e "air fryer" — justamente os três da vitrine). Sem uma ordem
    declarada, o Django resolveria pelo id e o `demo:` ganharia por vir antes
    no alfabeto: a página mostraria preço inventado tendo o real ao lado.
    """
    real = _seguro(
        lambda: Produto.objects.filter(external_id=external_id)
        .exclude(source=FONTE_DEMO)
        .order_by("-last_seen_at")
        .first(),
        None,
    )
    if real is not None:
        return real
    return _seguro(
        lambda: Produto.objects.filter(external_id=external_id).first(), None
    )


def _fixos() -> list[dict]:
    """Os produtos de `VITRINE_FIXA`, montados direto do catálogo.

    Não passa por `posts`: o post é o que o bot mandou para o grupo hoje, e
    amarrar a vitrine a ele foi o que trocou os produtos sozinho. Aqui basta
    o produto existir e ter histórico para desenhar a curva.

    Produto que sumiu do catálogo ou ainda não tem dois dias de preço é
    pulado em silêncio — a vitrine encolhe, mas nunca fica quebrada.
    """
    itens = []
    for external_id in VITRINE_FIXA:
        produto = _produto_por_external_id(external_id)
        if produto is None:
            continue
        precos = _historico(produto)
        curva = montar_curva(precos)
        if curva is None:
            continue
        itens.append(
            {
                "produto": produto,
                "external_id": produto.external_id,
                "curva": curva,
                "dias": len(precos),
                "video": _video_do_produto(produto.external_id),
                "demonstracao": produto.source == FONTE_DEMO,
            }
        )
    return itens


def _video_do_produto(external_id: str) -> str | None:
    """Filme do produto, se alguém já produziu um.

    A ligação é pelo nome do arquivo — `MLB46211942.mp4` casa com o produto
    de mesmo id. Sem tabela de-para no código: quem renderiza um vídeo novo
    só precisa salvá-lo com o id certo, e ele entra sozinho. Produto sem
    filme continua com a foto, que é o caso da maioria.
    """
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static as url_estatica

    caminho = f"vitrine/video/produto/{external_id}.mp4"
    return url_estatica(caminho) if finders.find(caminho) else None


def _cinema() -> dict:
    """Localiza o vídeo do Scroll Cinema, se ele já existir.

    A seção só entra quando o arquivo está lá. Meia tela preta com um
    spinner eterno seria pior do que não ter o efeito, e o hero normal
    continua funcionando sozinho.
    """
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static as url_estatica

    if finders.find("vitrine/video/hero.mp4") is None:
        return {"cinema_video": None, "cinema_poster": None}

    poster = finders.find("vitrine/video/poster.jpg")
    return {
        "cinema_video": url_estatica("vitrine/video/hero.mp4"),
        "cinema_poster": url_estatica("vitrine/video/poster.jpg") if poster else None,
    }


def home(request):
    grupos = list(Grupo.objects.filter(ativo=True))
    ofertas = _ofertas()
    # Uma para o topo e três para a vitrine que rola.
    destaques = _com_curva(ofertas, 4)
    destaque = destaques[0] if destaques else None
    # A vitrine é fixa; a lista dinâmica só entra se o catálogo ainda não
    # tiver os produtos dela — seção vazia numa página que promete prova é
    # pior do que prova com outro produto.
    fixos = _fixos()
    return render(
        request,
        "vitrine/home.html",
        {
            "grupos": grupos,
            "grupo_aberto": next((g for g in grupos if not g.lotado), None),
            "ofertas": ofertas,
            # A esteira só leva o que foi medido: a pílula passa rápido e não
            # cabe a ressalva de onde veio a baseline. O grid mais abaixo leva
            # tudo, com a etiqueta dizendo qual é qual.
            "esteira": [item for item in ofertas if item["verificado"]],
            # Explica a etiqueta "preço da loja" só quando ela está na tela.
            "tem_repasse": any(not item["verificado"] for item in ofertas),
            "destaque": destaque,
            "vitrine": fixos or destaques[1:4] or destaques[:3],
            "numeros": _numeros(),
            "form": InscricaoForm(),
            "janela_dias": JANELA_DIAS,
            # No preview do link, foto de produto real com preço convence mais
            # que arte genérica. Sem destaque, o context processor entra com a
            # imagem padrão.
            "og_imagem": (
                _absoluta(request, destaque["produto"].image_url) if destaque else None
            ),
            "og_imagem_alt": destaque["produto"].title if destaque else None,
            **_cinema(),
        },
    )


def _absoluta(request, url: str | None) -> str | None:
    """Deixa a URL pronta para meta tag: absoluta, com esquema e domínio.

    `build_absolute_uri` devolve inalterado o que já é absoluto, então serve
    tanto para o CDN da loja quanto para arquivo nosso.
    """
    return request.build_absolute_uri(url) if url else None


def oferta(request, external_id: str):
    """Página de uma oferta, com a curva de preço que prova o desconto."""
    produto = _produto_por_external_id(external_id)
    if produto is None:
        raise Http404("Oferta não encontrada")

    historico = _seguro(
        lambda: list(
            HistoricoPreco.objects.using("promos")
            .filter(product_id=produto.id)
            .order_by("observed_on")
            .values_list("observed_on", "price")
        ),
        [],
    )[-JANELA_DIAS:]

    post = _seguro(
        lambda: Post.objects.publicados().filter(product_id=produto.id).first(), None
    )

    return render(
        request,
        "vitrine/oferta.html",
        {
            "produto": produto,
            "post": post,
            "curva": montar_curva([preco for _, preco in historico]),
            "dias": len(historico),
            "primeiro_dia": _data(historico[0][0]) if historico else None,
            # Produto de demonstração não teve preço coletado: a página troca
            # as afirmações de monitoramento por um aviso do que ele é.
            "demonstracao": produto.source == FONTE_DEMO,
            # Repasse da vitrine do ML: o desconto do post é contra o preço
            # riscado da loja. A página para de chamar essa baseline de
            # "mediana histórica" e diz de onde ela veio.
            "verificado": post.verified if post else True,
            "grupo_aberto": Grupo.aberto(),
            "og_imagem": _absoluta(request, produto.image_url),
        },
    )


def _data(iso: str) -> date | None:
    try:
        return date.fromisoformat(iso)
    except (TypeError, ValueError):
        return None


def inscrever(request):
    """Captura de e-mail — o que salva a visita quando todo grupo está lotado."""
    if request.method != "POST":
        return redirect("vitrine:home")

    form = InscricaoForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Pronto. Você recebe o convite assim que abrir vaga.")
    else:
        messages.error(request, "E-mail inválido ou já cadastrado.")
    return redirect("vitrine:home")


def saude(request):
    """Endpoint de monitoração: diz se o banco do bot está acessível."""
    numeros = _numeros()
    return JsonResponse(
        {
            "ok": True,
            "banco_do_bot": numeros["produtos"] > 0 or numeros["observacoes"] > 0,
            **numeros,
        }
    )

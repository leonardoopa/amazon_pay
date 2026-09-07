"""Views da vitrine.

O banco do bot pode não existir ainda (site no ar antes da primeira coleta),
então tudo que lê de lá degrada para vazio em vez de estourar 500. Uma landing
sem feed converte; uma landing quebrada, não.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.db.models import F, Sum
from django.db.utils import DatabaseError, OperationalError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import escape
from django.views.decorators.http import require_POST

from .forms import InscricaoForm, OfertaAmazonForm
from .models import Clique, Grupo, HistoricoPreco, Post, Produto
from .precos import montar_curva

# Quantos dias de histórico o gráfico mostra.
JANELA_DIAS = 60

# Por quanto tempo guardar o que veio do banco do bot.
#
# A home consultava o `promos.db` a cada visita, e a coleta só muda esse dado a
# cada duas horas: era leitura repetida de um arquivo que o bot está escrevendo
# ao lado. Um minuto é curto o bastante para a oferta nova aparecer quase na
# hora e longo o bastante para uma rajada de visitas custar uma consulta só.
CACHE_SEGUNDOS = 60

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
    return _em_cache(f"vitrine:ofertas:{limite}", lambda: _ofertas_do_banco(limite))


def _ofertas_do_banco(limite: int) -> list[dict]:
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


def _em_cache(chave: str, produzir):
    """Guarda por CACHE_SEGUNDOS o resultado de uma leitura do banco do bot.

    O cache é local ao processo (LocMemCache), então cada worker do gunicorn
    tem o seu. Não é problema aqui: o pior caso é uma consulta por worker por
    minuto em vez de uma por visita, e o dado é o mesmo para todo mundo — não
    há nada por visitante para vazar de um para o outro.
    """
    valor = cache.get(chave)
    if valor is None:
        valor = produzir()
        cache.set(chave, valor, CACHE_SEGUNDOS)
    return valor


def _numeros() -> dict:
    """Contadores da prova social. Só números reais — nada inflado.

    O catálogo de demonstração fica de fora: ele existe para a vitrine ter
    produto com filme, e contar preço inventado como "preço registrado" é
    exatamente a mentira que o site acusa a loja de contar. O /saude lê
    daqui também, então o monitoramento continua enxergando só o real.
    """
    return _em_cache("vitrine:numeros", _numeros_do_banco)


def _numeros_do_banco() -> dict:
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
    return {
        "produtos": produtos,
        "observacoes": observacoes,
        "ofertas_enviadas": enviados,
        "economia_total": _economia_total(),
    }


def _economia_total() -> float:
    """Soma de quanto cada oferta medida ficou abaixo da mediana.

    A soma é do banco, não do Python. Antes eram até 500 objetos carregados e
    somados aqui, o que tinha dois problemas: o teto de 500 tornava o número
    silenciosamente errado depois do 500º post, e cada visita pagava a
    carga inteira. O `filter(baseline__gt=F("price"))` faz o mesmo que o
    `max(0, ...)` da propriedade `economia`, mas em SQL.
    """
    agregado = _seguro(
        lambda: Post.objects.medidos()
        .filter(baseline__gt=F("price"))
        .aggregate(total=Sum(F("baseline") - F("price"))),
        {},
    )
    return float((agregado or {}).get("total") or 0.0)


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


@require_POST
def inscrever(request):
    """Captura de e-mail — o que salva a visita quando todo grupo está lotado.

    Três camadas contra robô, em ordem de custo: o campo-isca do formulário
    (custa nada e pega a maioria), o limite por IP aqui, e o `unique` do
    e-mail no banco. Nenhuma delas cobra nada de quem é gente.
    """
    if _excedeu_limite(request):
        # Mensagem de gente, não de sistema: quem cai aqui por acidente
        # (clique duplo, aba duplicada) precisa entender o que fazer.
        messages.error(request, "Muitas tentativas. Espere um minuto e tente de novo.")
        return redirect("vitrine:home")

    form = InscricaoForm(request.POST)
    if form.is_valid():
        form.save()
        messages.success(request, "Pronto. Você recebe o convite assim que abrir vaga.")
    else:
        messages.error(request, "E-mail inválido ou já cadastrado.")
    return redirect("vitrine:home")


# Envios permitidos por IP dentro da janela. Cinco é folgado para uma pessoa
# (errar o e-mail duas vezes é normal) e apertado para quem varre formulário.
LIMITE_ENVIOS = 5
JANELA_LIMITE_SEGUNDOS = 300


def _excedeu_limite(request) -> bool:
    """Conta envios por IP numa janela curta.

    O contador vive no cache do processo, então com N workers do gunicorn o
    limite efetivo é N × LIMITE_ENVIOS. Fica assim de propósito: um contador
    compartilhado exigiria Redis ou uma tabela, e para um formulário de um
    campo a isca já derruba o volume — isto aqui é o teto que impede alguém de
    encher a tabela num laço.

    O IP não é gravado em lugar nenhum: ele só compõe a chave do contador, que
    expira junto com a janela.
    """
    ip = _ip_do_visitante(request)
    if not ip:
        return False
    chave = f"vitrine:inscricao:{ip}"
    try:
        enviados = cache.get_or_set(chave, 0, JANELA_LIMITE_SEGUNDOS)
        cache.set(chave, (enviados or 0) + 1, JANELA_LIMITE_SEGUNDOS)
    except Exception:  # noqa: BLE001 - cache indisponível não pode barrar cadastro
        return False
    return (enviados or 0) >= LIMITE_ENVIOS


def _ip_do_visitante(request) -> str:
    """IP de quem pediu, respeitando o proxy da frente.

    Atrás do Caddy o `REMOTE_ADDR` é o próprio container, igual para todos —
    limitar por ele barraria o site inteiro quando um único robô batesse. O
    primeiro endereço do `X-Forwarded-For` é o cliente.
    """
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def entrar(request):
    """Redireciona para o convite do grupo, registrando o clique.

    Existe para responder "quantas pessoas o site levou para o grupo?" — que
    era uma pergunta sem resposta enquanto o botão apontava direto para o
    `chat.whatsapp.com`. De quebra, o convite passa a ter um lugar só: trocar
    o link do grupo é editar o cadastro, não caçar `href` em cinco templates.

    Sem grupo com vaga, manda para a captura de e-mail em vez de dar erro:
    quem clicou já demonstrou interesse, e é a hora de pedir o contato.
    """
    grupo = Grupo.aberto()
    origem = request.GET.get("de", "")
    validas = {chave for chave, _ in Clique.ORIGENS}
    try:
        Clique.objects.create(
            grupo=grupo,
            origem=origem if origem in validas else "desconhecida",
        )
    except DatabaseError:
        # Medição não pode impedir a conversão que ela mede.
        pass

    if grupo is None:
        return redirect(reverse("vitrine:home") + "#vaga")
    return redirect(grupo.convite)


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


def robots(request):
    """robots.txt servido pelo Django, não por arquivo estático.

    Assim ele aponta para o sitemap no domínio de quem pediu — o mesmo código
    roda em localhost, no IP do servidor e no domínio final, sem três versões
    do arquivo.

    `/entrar/` fica fora do índice de propósito: é um redirecionamento para o
    WhatsApp, não conteúdo, e cada visita de robô ali viraria um clique falso
    na medição.
    """
    linhas = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /entrar/",
        "Disallow: /inscrever/",
        "Disallow: /saude/",
        "",
        f"Sitemap: {request.build_absolute_uri(reverse('vitrine:sitemap'))}",
        "",
    ]
    return HttpResponse("\n".join(linhas), content_type="text/plain; charset=utf-8")


# Quantas páginas de oferta entram no sitemap. O buscador não precisa da
# história inteira, e um sitemap de milhares de URLs de produto que a loja pode
# ter tirado do ar gasta o rastreamento no lugar errado.
SITEMAP_LIMITE = 200


def sitemap(request):
    """Sitemap XML: a home e as páginas de oferta que têm curva.

    Escrito à mão em vez de `django.contrib.sitemaps` porque a fonte aqui é o
    banco do bot, somente leitura e sem `LastModified` confiável por linha — o
    framework pediria um model gerenciado e um `sites` instalado para entregar
    o mesmo XML de duas dezenas de linhas.
    """
    urls = [(request.build_absolute_uri(reverse("vitrine:home")), "1.0", None)]

    for item in _ofertas(SITEMAP_LIMITE):
        quando = item.get("quando")
        urls.append(
            (
                request.build_absolute_uri(
                    reverse("vitrine:oferta", args=[item["external_id"]])
                ),
                "0.7",
                quando.date().isoformat() if quando else None,
            )
        )

    partes = ['<?xml version="1.0" encoding="UTF-8"?>']
    partes.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    for endereco, prioridade, modificado in urls:
        partes.append("  <url>")
        partes.append(f"    <loc>{escape(endereco)}</loc>")
        if modificado:
            partes.append(f"    <lastmod>{modificado}</lastmod>")
        partes.append(f"    <priority>{prioridade}</priority>")
        partes.append("  </url>")
    partes.append("</urlset>")
    return HttpResponse("\n".join(partes), content_type="application/xml")


@staff_member_required
def amazon_add(request):
    """Tela do admin para enfileirar uma oferta da Amazon escolhida à mão.

    Existe porque a Creators API ainda não liberou e o dono vê as ofertas no
    celular. Sem isto, enfileirar exigiria SSH e uma linha de comando.

    A view NÃO escreve no banco do bot: ela chama o `POST /amazon-add` da API,
    que é quem grava. O roteador em `comunidade/routers.py` proíbe essa escrita
    de propósito — um `.save()` acidental corromperia o histórico de preço — e
    a regra não abre exceção para o backoffice.
    """
    formulario = OfertaAmazonForm(request.POST or None)
    resultado = None

    if request.method == "POST" and formulario.is_valid():
        dados = formulario.cleaned_data
        try:
            resposta = httpx.post(
                f"{settings.BOT_API_URL.rstrip('/')}/amazon-add",
                json={
                    "produto": dados["produto"],
                    "titulo": dados["titulo"],
                    "preco": float(dados["preco"]),
                    "de": float(dados["de"]),
                    "imagem": dados["imagem"] or None,
                    "sem_ia": dados["sem_ia"],
                },
                headers={"X-API-Key": settings.BOT_API_SECRET},
                # Generoso porque o Gemini escreve o texto durante a chamada, e
                # ele leva uns 40s por oferta.
                timeout=90.0,
            )
        except httpx.HTTPError as exc:
            messages.error(request, f"Não consegui falar com o bot: {exc}")
        else:
            if resposta.status_code == 201:
                resultado = resposta.json()
                messages.success(
                    request,
                    f"Post {resultado['post_id']} na fila: {resultado['asin']} "
                    f"(-{resultado['desconto_pct']}%). Sai no próximo gotejamento.",
                )
                formulario = OfertaAmazonForm()
            else:
                # O 422 traz a mensagem do `promo.manual`, que é escrita para
                # quem digitou. Os outros não trazem nada de útil.
                detalhe = ""
                try:
                    detalhe = resposta.json().get("detail", "")
                except ValueError:
                    detalhe = resposta.text[:200]
                messages.error(
                    request, f"O bot recusou ({resposta.status_code}): {detalhe}"
                )

    return render(
        request,
        "vitrine/amazon_add.html",
        {"formulario": formulario, "resultado": resultado, "title": "Oferta da Amazon"},
    )

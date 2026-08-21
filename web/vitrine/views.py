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
    """Contadores da prova social. Só números reais — nada inflado."""
    produtos = _seguro(lambda: Produto.objects.count(), 0)
    observacoes = _seguro(
        lambda: HistoricoPreco.objects.using("promos").count(), 0
    )
    enviados = _seguro(lambda: Post.objects.publicados().count(), 0)
    economia = 0.0
    for post in _seguro(lambda: list(Post.objects.publicados()[:500]), []):
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
    """
    escolhidas = []
    for item in sorted(ofertas, key=lambda o: -o["post"].discount_pct):
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
    return render(
        request,
        "vitrine/home.html",
        {
            "grupos": grupos,
            "grupo_aberto": next((g for g in grupos if not g.lotado), None),
            "ofertas": ofertas,
            "destaque": destaques[0] if destaques else None,
            "vitrine": destaques[1:4] or destaques[:3],
            "numeros": _numeros(),
            "form": InscricaoForm(),
            "janela_dias": JANELA_DIAS,
            **_cinema(),
        },
    )


def oferta(request, external_id: str):
    """Página de uma oferta, com a curva de preço que prova o desconto."""
    produto = _seguro(
        lambda: Produto.objects.filter(external_id=external_id).first(), None
    )
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
            "grupo_aberto": Grupo.aberto(),
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
        messages.success(
            request, "Pronto. Você recebe o convite assim que abrir vaga."
        )
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

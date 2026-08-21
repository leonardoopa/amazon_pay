"""Metadados que toda página precisa: URL canônica e imagem de preview.

O crescimento da comunidade acontece por link colado no WhatsApp. Link sem
`og:image` aparece como retângulo cinza, e retângulo cinza não é clicado —
então a imagem de preview é infraestrutura de conversão, não enfeite.
"""

from __future__ import annotations

from django.contrib.staticfiles import finders
from django.templatetags.static import static as url_estatica

# Arte própria do preview, se alguém desenhar uma. Mesma convenção dos vídeos
# de produto: basta o arquivo existir para entrar em cena, sem cadastro nem
# alteração de código. Formato ideal: JPG 1200x630, abaixo de 300 KB — acima
# disso o WhatsApp desiste de baixar e mostra o link sem imagem.
ARTE_SOCIAL = "vitrine/img/social.jpg"
# Reserva: o pôster do vídeo do topo, que já está no repositório.
POSTER_DO_VIDEO = "vitrine/video/poster.jpg"


def imagem_padrao() -> str | None:
    """Melhor imagem disponível para o preview, sem tocar no banco."""
    for caminho in (ARTE_SOCIAL, POSTER_DO_VIDEO):
        if finders.find(caminho):
            return url_estatica(caminho)
    return None


def meta(request):
    """Contexto de meta tags disponível em qualquer template.

    `canonical` sai de `request.path`, sem query string: `?utm_source=zap`
    não cria página nova, e deixar a query entrar faria o buscador indexar
    uma URL diferente por campanha.
    """
    padrao = imagem_padrao()
    return {
        "canonical": request.build_absolute_uri(request.path),
        # Absoluta porque WhatsApp, Telegram e buscador não resolvem caminho
        # relativo em meta tag — cada um busca a imagem por conta própria.
        "og_imagem_padrao": request.build_absolute_uri(padrao) if padrao else None,
    }

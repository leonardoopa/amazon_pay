"""Cabeçalhos de segurança que o Django não traz prontos.

Ficam aqui, e não no Caddyfile, por um motivo prático: aqui eles rodam no
`runserver` e no teste. CSP escrita direto no proxy só é exercitada em
produção, e CSP errada não degrada — ela apaga o CSS ou mata o script, na
primeira visita de quem já recebeu o link.

O que o Django já manda sozinho fica com ele: `X-Frame-Options`,
`X-Content-Type-Options`, `Referrer-Policy` (via SECURE_REFERRER_POLICY),
HSTS e os redirects de HTTPS.
"""

from __future__ import annotations

# Fontes permitidas, uma linha por diretiva para o diff mostrar o que mudou.
#
# `style-src` precisa de 'unsafe-inline': os templates usam atributo `style=`
# em alguns lugares (o gráfico SVG posiciona rótulo assim) e a folha do Google
# Fonts entra por link. Sem hash em cada atributo não há como fechar isso, e
# fechar por engano apaga a página.
#
# `img-src https:` é largo de propósito: a foto do produto vem do CDN do
# Mercado Livre, que troca de host (http2.mlstatic.com, mla-s1-p, …) sem
# aviso. Restringir por host aqui viraria card sem foto no dia da mudança.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data: https:",
        "media-src 'self'",
        # Nenhum formulário do site posta para fora, e nada é embutido em
        # iframe — as duas portas ficam fechadas.
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        # Upgrade em vez de bloqueio: se sobrar um `http://` num template, o
        # navegador tenta em HTTPS em vez de recusar o recurso.
        "upgrade-insecure-requests",
    ]
)

# Desliga o que a página não usa. Sem isso um script de terceiro que entre um
# dia (tag de analytics, por exemplo) herda acesso a câmera e microfone.
PERMISSIONS_POLICY = (
    "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
    "magnetometer=(), microphone=(), payment=(), usb=()"
)


def seguranca(get_response):
    """Middleware: acrescenta CSP e Permissions-Policy a toda resposta."""

    def middleware(request):
        resposta = get_response(request)
        # Não sobrescreve o que já veio definido: deixa espaço para uma view
        # relaxar a política num caso específico sem editar este arquivo.
        resposta.setdefault("Content-Security-Policy", CSP)
        resposta.setdefault("Permissions-Policy", PERMISSIONS_POLICY)
        return resposta

    return middleware

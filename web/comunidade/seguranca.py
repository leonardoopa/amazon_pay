"""Decisões de segurança do settings, isoladas para poderem ser testadas.

O settings roda uma vez na importação do processo, então uma regra escrita
lá dentro não tem como ser exercitada por teste. Aqui ela é função pura: o
settings chama, o teste também.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

# Só serve para o `runserver` local não exigir configuração antes da primeira
# execução. O nome diz o que é, porque este arquivo é público no git.
CHAVE_DE_DESENVOLVIMENTO = "django-insecure-apenas-local-nao-usar-em-producao"


def chave_secreta(valor: str, debug: bool) -> str:
    """Resolve a SECRET_KEY, recusando subir em produção sem uma.

    Falha fechada de propósito: com a chave que está publicada no git,
    qualquer pessoa assina cookie de sessão e entra no admin. Errar aqui não
    dá sintoma nenhum — o site sobe normal e fica aberto.

    Gerar uma aleatória a cada boot também não serve: derrubaria toda sessão
    a cada deploy e invalidaria os formulários abertos.
    """
    if valor:
        return valor
    if not debug:
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY ausente com DJANGO_DEBUG=0. "
            "Em produção, gere uma e ponha no .env: openssl rand -hex 32. "
            "Na sua máquina, o que falta é DJANGO_DEBUG=1 no .env."
        )
    return CHAVE_DE_DESENVOLVIMENTO


def hosts_permitidos(valor: str, debug: bool) -> list[str]:
    """Lista de ALLOWED_HOSTS, exigida quando não é desenvolvimento.

    Sem isso o site responderia a qualquer `Host`, o que abre cache poisoning
    e link de recuperação apontando para domínio de terceiro. O Django já
    barra em runtime, mas só na primeira visita — e aí o erro aparece para o
    visitante, não para quem fez o deploy.
    """
    hosts = [host.strip() for host in valor.split(",") if host.strip()]
    if hosts:
        return hosts
    if not debug:
        raise ImproperlyConfigured(
            "DJANGO_ALLOWED_HOSTS vazio com DJANGO_DEBUG=0. "
            "Ponha o domínio do site no .env, separado por vírgula. "
            "Na sua máquina, o que falta é DJANGO_DEBUG=1 no .env."
        )
    return ["localhost", "127.0.0.1"]


# ---------- Limite de tentativas no login do admin ----------

CHAVE_TENTATIVAS = "login:tentativas:{ip}"


def _ip_do_pedido(request) -> str:
    """O IP de quem pede, atrás do Caddy.

    O Caddy é o único que fala com o Django, então `REMOTE_ADDR` é sempre o
    IP dele. O IP real vem no `X-Forwarded-For`, e vale só o PRIMEIRO valor:
    os seguintes são escritos pelo cliente e não são confiáveis.
    """
    encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "desconhecido")


class LimiteDeLoginMiddleware:
    """Barra força bruta no /admin/login/ contando tentativas por IP.

    O /admin/ está exposto na internet e a senha é a única barreira. Sem
    limite, uma lista de senhas comuns roda a noite inteira sem custo para
    quem tenta e sem sintoma nenhum para quem hospeda.

    Conta no cache, e não no banco: a contagem é descartável por natureza —
    perder o contador num restart apenas devolve as tentativas, e não é o que
    protege a conta. Gravar isso em SQLite seria uma escrita por tentativa de
    login num arquivo que a coleta também usa.

    Conta só o que FALHA. Login certo zera o contador, senão quem erra a senha
    duas vezes e acerta na terceira ficaria a um erro do bloqueio pelo resto
    da janela.
    """

    CAMINHO = "/admin/login/"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.conf import settings
        from django.core.cache import cache
        from django.http import HttpResponse

        alvo = request.path == self.CAMINHO and request.method == "POST"
        if not alvo:
            return self.get_response(request)

        limite = getattr(settings, "LOGIN_MAX_TENTATIVAS", 8)
        minutos = getattr(settings, "LOGIN_BLOQUEIO_MINUTOS", 15)
        chave = CHAVE_TENTATIVAS.format(ip=_ip_do_pedido(request))

        if limite > 0 and (cache.get(chave) or 0) >= limite:
            # 429 e não 403: diz a verdade sobre o motivo, e o `Retry-After`
            # faz o cliente honesto (um gerenciador de senhas, por exemplo)
            # esperar em vez de insistir.
            return HttpResponse(
                "Tentativas demais. Espere alguns minutos.",
                status=429,
                headers={"Retry-After": str(minutos * 60)},
            )

        resposta = self.get_response(request)

        # O admin responde 302 quando o login dá certo e 200 (com o formulário
        # e o erro) quando não dá. `request.user` já está resolvido aqui.
        entrou = (
            getattr(request, "user", None) is not None and request.user.is_authenticated
        )
        if entrou:
            cache.delete(chave)
        elif limite > 0:
            # `add` cria com o TTL da janela; o `incr` só anda se já existir.
            # Fazer `set(get() + 1)` renovaria o TTL a cada tentativa, e a
            # janela nunca fecharia para quem insiste.
            cache.add(chave, 0, timeout=minutos * 60)
            try:
                cache.incr(chave)
            except ValueError:
                # A chave expirou entre o `add` e o `incr`. A tentativa
                # seguinte recomeça a contagem; perder uma não muda nada.
                pass

        return resposta

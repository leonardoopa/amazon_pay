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

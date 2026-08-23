"""Atualiza o número de membros de cada grupo pela API da Evolution.

Antes disso, `membros` era digitado à mão no admin. Enquanto ninguém editava,
a barra de ocupação da landing mostrava o número do dia do cadastro e o site
seguia oferecendo entrada num grupo que podia estar cheio — o visitante clica,
o WhatsApp diz que o grupo está lotado, e a visita morre ali.

Roda no container do site, que fala com a Evolution pela rede interna do
compose. No cron:

    0 * * * * cd ~/amazon_pay && docker compose exec -T web \\
        python web/manage.py sincronizar_grupos >> /dev/null 2>&1
"""

from __future__ import annotations

import os

import httpx
from django.core.management.base import BaseCommand
from django.utils import timezone

from vitrine.models import Grupo

# Fora da rede do compose (rodando na sua máquina), o host é localhost e a
# porta é a publicada no loopback.
BASE_PADRAO = "http://evolution:8080"


class Command(BaseCommand):
    help = "Atualiza `membros` de cada grupo com o total que a Evolution reporta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tempo-limite",
            type=float,
            default=15.0,
            help="Segundos de espera por resposta da Evolution (padrão: 15).",
        )

    def handle(self, *args, **options):
        chave = os.getenv("EVOLUTION_API_KEY", "")
        instancia = os.getenv("EVOLUTION_INSTANCE") or "ofertas"
        base = os.getenv("EVOLUTION_BASE_URL") or BASE_PADRAO

        if not chave:
            # Não é erro: quem usa o backend `cloud` não tem Evolution.
            self.stdout.write("EVOLUTION_API_KEY não configurada; nada a sincronizar.")
            return

        grupos = list(Grupo.objects.exclude(jid="").order_by("ordem", "id"))
        if not grupos:
            self.stdout.write(
                "Nenhum grupo com JID cadastrado. Pegue o valor com "
                '`promo wa-groups --search "nome do grupo"` e preencha o campo '
                "JID no admin."
            )
            return

        atualizados = 0
        with httpx.Client(
            base_url=base.rstrip("/"),
            headers={"apikey": chave},
            timeout=options["tempo_limite"],
        ) as cliente:
            for grupo in grupos:
                total = self._contar_membros(cliente, instancia, grupo)
                if total is None:
                    continue
                # Grava sempre o carimbo, mesmo com o número igual: é ele que
                # diz no admin se a sincronização ainda está viva.
                grupo.membros = total
                grupo.membros_atualizados_em = timezone.now()
                grupo.save(update_fields=["membros", "membros_atualizados_em"])
                atualizados += 1
                self.stdout.write(
                    f"{grupo.nome}: {total}/{grupo.capacidade}"
                    f"{' (lotado)' if grupo.lotado else ''}"
                )

        self.stdout.write(
            self.style.SUCCESS(f"{atualizados} de {len(grupos)} grupo(s) atualizados.")
        )

    def _contar_membros(self, cliente, instancia: str, grupo: Grupo) -> int | None:
        """Total de participantes, ou None quando a Evolution não respondeu.

        Falha de rede aqui não pode virar exceção: o comando roda em cron, e
        uma instância desconectada (o pareamento cai sozinho de vez em quando)
        é o caso mais comum de todos. Melhor manter o número antigo do que
        zerar a ocupação e anunciar vaga que não existe.
        """
        try:
            resposta = cliente.get(
                f"/group/findGroupInfos/{instancia}",
                params={"groupJid": grupo.jid},
            )
        except httpx.HTTPError as exc:
            self.stderr.write(f"{grupo.nome}: Evolution inacessível ({exc}).")
            return None

        if resposta.status_code != 200:
            # 404 aqui costuma ser instância fechada (pareamento caiu), não
            # grupo inexistente — a mensagem diz onde olhar.
            self.stderr.write(
                f"{grupo.nome}: Evolution respondeu {resposta.status_code}. "
                "Confira se a instância está conectada no painel."
            )
            return None

        try:
            corpo = resposta.json()
        except ValueError:
            self.stderr.write(f"{grupo.nome}: resposta da Evolution não era JSON.")
            return None

        return self._extrair_total(corpo)

    @staticmethod
    def _extrair_total(corpo) -> int | None:
        """Lê o total de participantes tolerando o formato da resposta.

        A Evolution já mudou esse payload entre versões: às vezes vem `size`,
        às vezes só a lista `participants`. Aceitar os dois evita que uma
        atualização da imagem quebre a sincronização em silêncio.
        """
        if not isinstance(corpo, dict):
            return None
        tamanho = corpo.get("size")
        if isinstance(tamanho, int) and tamanho > 0:
            return tamanho
        participantes = corpo.get("participants")
        if isinstance(participantes, list):
            return len(participantes)
        return None

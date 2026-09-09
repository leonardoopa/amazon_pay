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
                dados = self._ler_grupo(cliente, instancia, grupo)
                if dados is None:
                    continue
                total, nome = dados
                # Grava sempre o carimbo, mesmo com o número igual: é ele que
                # diz no admin se a sincronização ainda está viva.
                campos = ["membros", "membros_atualizados_em"]
                grupo.membros = total
                grupo.membros_atualizados_em = timezone.now()

                # O nome também. Renomear o grupo no WhatsApp não quebra nada
                # — quem identifica é o JID —, mas deixava o admin e a landing
                # mostrando o nome do dia do cadastro. Aconteceu em 09/09/2026:
                # o grupo virou "Comunidade do Desconto - Geral 🇧🇷 03" e o
                # site seguia anunciando "Comunidade do Desconto #3".
                if nome and nome != grupo.nome:
                    self.stdout.write(f"{grupo.nome} agora se chama {nome}.")
                    grupo.nome = nome
                    campos.append("nome")

                grupo.save(update_fields=campos)
                atualizados += 1
                self.stdout.write(
                    f"{grupo.nome}: {total}/{grupo.capacidade}"
                    f"{' (lotado)' if grupo.lotado else ''}"
                )

        self.stdout.write(
            self.style.SUCCESS(f"{atualizados} de {len(grupos)} grupo(s) atualizados.")
        )

    def _ler_grupo(self, cliente, instancia: str, grupo: Grupo):
        """(total de participantes, nome), ou None se a Evolution não respondeu.

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

        total = self._extrair_total(corpo)
        if total is None:
            return None
        nome = corpo.get("subject") if isinstance(corpo, dict) else None
        return total, (nome or "").strip()

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

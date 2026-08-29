"""Cadastra (ou atualiza) um grupo da comunidade pela linha de comando.

O mesmo cadastro existe no admin, mas o admin exige site no ar e superusuário
criado -- e o grupo precisa existir antes disso, senão a primeira visita cai no
"avise-me quando abrir vaga" em vez do convite. Aqui é um comando só, que roda
igual na máquina de quem desenvolve e no container do servidor.

O convite entra por argumento, nunca no código: é um link ativo, e quem o tem
entra no grupo. Em template ele apareceria no HTML de toda visita; em arquivo
versionado ficaria no histórico do git para sempre.

    python web/manage.py cadastrar_grupo \\
        --nome "Comunidade do Desconto #1" \\
        --convite "https://chat.whatsapp.com/XXXXXXXXXXXXXXXXXXXXXX"

Rodar de novo com o mesmo `--nome` atualiza o cadastro em vez de duplicar --
é assim que se troca um convite revogado.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from django.core.management.base import BaseCommand, CommandError

from vitrine.models import Grupo

HOST_CONVITE = "chat.whatsapp.com"


class Command(BaseCommand):
    help = "Cria ou atualiza um grupo da comunidade, identificado pelo nome."

    def add_arguments(self, parser):
        parser.add_argument("--nome", required=True, help="Identifica o cadastro.")
        parser.add_argument(
            "--convite",
            required=True,
            help=f"URL {HOST_CONVITE} gerada dentro do próprio grupo.",
        )
        parser.add_argument(
            "--jid",
            default="",
            help=(
                'Termina em @g.us. Sai do `promo wa-groups --search "nome"`. '
                "Com ele, o `sincronizar_grupos` passa a atualizar `membros` "
                "sozinho."
            ),
        )
        parser.add_argument(
            "--membros",
            type=int,
            default=None,
            help=(
                "Quantos já entraram. Só use no cadastro inicial: o "
                "`sincronizar_grupos` sobrescreve depois. Omitido, mantém o "
                "valor atual (0 num cadastro novo)."
            ),
        )
        parser.add_argument(
            "--capacidade",
            type=int,
            default=None,
            help="Limite do WhatsApp (padrão do modelo: 1024).",
        )
        parser.add_argument(
            "--categoria",
            default=None,
            help="Ex.: Eletrônicos. Vazio = grupo geral.",
        )
        parser.add_argument(
            "--ordem",
            type=int,
            default=None,
            help=(
                "Menor primeiro. Define qual grupo recebe as entradas enquanto "
                "tiver vaga."
            ),
        )
        parser.add_argument(
            "--inativo",
            action="store_true",
            help="Cadastra sem receber entradas. Para preparar o próximo grupo.",
        )

    def handle(self, *args, **options):
        convite = self._normalizar(options["convite"])
        nome = options["nome"].strip()

        if not nome:
            raise CommandError("--nome não pode ser vazio.")

        jid = options["jid"].strip()
        if jid and not jid.endswith("@g.us"):
            raise CommandError(f"JID deve terminar em @g.us, recebido: {jid!r}")

        # Mesmo convite em dois cadastros manda gente para o grupo errado
        # quando o primeiro lotar: a landing escolhe pela ordem, não pelo link.
        conflito = Grupo.objects.filter(convite=convite).exclude(nome=nome).first()
        if conflito:
            raise CommandError(
                f"Este convite já está cadastrado em {conflito.nome!r}. "
                "Gere um convite novo dentro do grupo ou corrija o --nome."
            )

        campos = {"convite": convite, "ativo": not options["inativo"]}
        if jid:
            campos["jid"] = jid
        for chave in ("membros", "capacidade", "categoria", "ordem"):
            if options[chave] is not None:
                campos[chave] = options[chave]

        grupo, criado = Grupo.objects.update_or_create(nome=nome, defaults=campos)

        verbo = "cadastrado" if criado else "atualizado"
        self.stdout.write(
            self.style.SUCCESS(
                f"Grupo {verbo}: {grupo.nome} "
                f"({grupo.membros}/{grupo.capacidade}, ordem {grupo.ordem})"
            )
        )

        # O convite não é ecoado: este comando roda em SSH, e terminal fica em
        # scrollback. O que importa confirmar é qual cadastro recebe entrada.
        aberto = Grupo.aberto()
        if aberto is None:
            self.stdout.write(
                "Nenhum grupo com vaga: a landing vai oferecer o aviso por "
                "e-mail em vez do convite."
            )
        else:
            self.stdout.write(f"Recebendo entradas: {aberto.nome}")

        if not grupo.jid:
            self.stdout.write(
                "Sem JID: `membros` continua manual. Preencha com "
                "`--jid` para o `sincronizar_grupos` cuidar do número."
            )

    @staticmethod
    def _normalizar(bruto: str) -> str:
        """Valida o host e descarta o rastro que o app do WhatsApp anexa.

        O código do convite mora no caminho da URL. O `?mode=gi_t` que vem ao
        copiar pelo aplicativo é marcador de origem do próprio WhatsApp, não
        faz parte do convite, e guardá-lo só faz o link do site carregar um
        parâmetro que não diz nada para quem clica.
        """
        partes = urlparse(bruto.strip())

        if partes.scheme != "https" or partes.netloc != HOST_CONVITE:
            raise CommandError(
                f"--convite deve ser uma URL https://{HOST_CONVITE}/..., "
                f"recebido: {bruto!r}"
            )

        codigo = partes.path.strip("/")
        if not codigo:
            raise CommandError("A URL do convite não tem código depois da barra.")

        return urlunparse(("https", HOST_CONVITE, f"/{codigo}", "", "", ""))

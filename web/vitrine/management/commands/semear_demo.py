"""Popula grupos e ofertas de demonstração para revisar o layout.

Existe porque a landing só faz sentido com dado dentro: card vazio não mostra
se a hierarquia funciona, e o gráfico é o argumento central da página.

Reusa os fixtures do próprio bot em vez de inventar outro catálogo — assim o
que aparece no site tem exatamente a forma do que a coleta real produz.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from vitrine.models import Grupo

SRC = Path(settings.PROJECT_ROOT) / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Sem divisão por nicho: quando um grupo enche, abre o próximo com o mesmo
# conteúdo. Nicho fragmenta a audiência antes de ela existir.
GRUPOS = [
    ("Comunidade do Desconto #1", 1024, 1024, 0),
    ("Comunidade do Desconto #2", 1024, 743, 1),
    ("Comunidade do Desconto #3", 1024, 96, 2),
]


class Command(BaseCommand):
    help = "Cria grupos e ofertas de demonstração para revisar o site."

    def add_arguments(self, parser):
        parser.add_argument(
            "--limpar",
            action="store_true",
            help="Remove os dados de demonstração em vez de criar",
        )

    def handle(self, *args, **options):
        from promo import fixtures
        from promo.db import connect, create_post, init_db, mark_post_sent, now

        if options["limpar"]:
            apagados, _ = Grupo.objects.filter(
                nome__startswith="Comunidade do Desconto"
            ).delete()
            with connect() as conn:
                removidos = fixtures.clear(conn)
            self.stdout.write(f"{apagados} grupos e {removidos} produtos removidos.")
            return

        for nome, capacidade, membros, ordem in GRUPOS:
            Grupo.objects.update_or_create(
                nome=nome,
                defaults={
                    "categoria": "",
                    "capacidade": capacidade,
                    "membros": membros,
                    "ordem": ordem,
                    "ativo": True,
                    "convite": "https://chat.whatsapp.com/EXEMPLO",
                },
            )
        self.stdout.write(f"{len(GRUPOS)} grupos de demonstração prontos.")

        init_db()
        with connect() as conn:
            criados = fixtures.seed(conn, days=60)

            # Rodar o comando duas vezes não pode dobrar o feed nem inflar o
            # contador de "ofertas aprovadas" — número falso na landing é o
            # oposto do que o site promete.
            ids_demo = [oferta.product_id for oferta in fixtures.current_offers()]
            marcas = ",".join("?" * len(ids_demo))
            conn.execute(f"DELETE FROM posts WHERE product_id IN ({marcas})", ids_demo)

            # Um post enviado por produto: é o que alimenta o feed da home.
            # `sent` porque a vitrine só mostra o que realmente foi ao grupo.
            for indice, oferta in enumerate(fixtures.current_offers()):
                baseline = oferta.original_price or oferta.price
                desconto = (baseline - oferta.price) / baseline * 100
                post_id = create_post(
                    conn,
                    oferta.product_id,
                    oferta.price,
                    baseline,
                    desconto,
                    f"Demo: {oferta.title}",
                    oferta.image_url,
                )
                mark_post_sent(conn, post_id)
                # Espalha no tempo para o feed não parecer publicado tudo junto.
                conn.execute(
                    "UPDATE posts SET created_at = ?, sent_at = ? WHERE id = ?",
                    (
                        (now() - timedelta(hours=indice * 7)).isoformat(),
                        (now() - timedelta(hours=indice * 7)).isoformat(),
                        post_id,
                    ),
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"{criados} produtos com 60 dias de histórico e posts enviados."
            )
        )
        self.stdout.write(
            "Produtos e fotos são reais (catálogo do ML). Os PREÇOS são "
            "inventados — servem para produzir uma baseline previsível."
        )

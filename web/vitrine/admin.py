"""Backoffice: cadastrar grupo, marcar lotado, ver quem se inscreveu.

Os modelos do bot ficam de fora de propósito — são somente leitura, e uma
tela de edição em cima deles convidaria a corromper o histórico de preços.
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from .models import Grupo, Inscrito


@admin.register(Grupo)
class GrupoAdmin(admin.ModelAdmin):
    list_display = ("nome", "categoria", "ocupacao", "ativo", "ordem")
    list_editable = ("ativo", "ordem")
    list_filter = ("ativo", "categoria")
    search_fields = ("nome", "categoria")

    @admin.display(description="ocupação")
    def ocupacao(self, obj: Grupo) -> str:
        cor = "#dc2626" if obj.lotado else "#16a34a"
        rotulo = "LOTADO" if obj.lotado else f"{obj.vagas} vagas"
        return format_html(
            '<span style="color:{}">{}/{} — {}</span>',
            cor,
            obj.membros,
            obj.capacidade,
            rotulo,
        )


@admin.register(Inscrito)
class InscritoAdmin(admin.ModelAdmin):
    list_display = ("email", "origem", "criado_em")
    search_fields = ("email",)
    readonly_fields = ("criado_em",)

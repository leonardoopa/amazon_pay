"""Roteador de banco: o site escreve no seu, o do bot é só leitura.

Sem isso o Django tentaria criar as tabelas do bot no banco do site e, pior,
rodar migração em cima do `promos.db` — o que quebraria a coleta.
"""

from __future__ import annotations

APP = "vitrine"
MODELOS_DO_BOT = {"produto", "post", "historicopreco"}
BANCO_DO_BOT = "promos"


class PromosRouter:
    def db_for_read(self, model, **hints):
        if model._meta.app_label == APP and model._meta.model_name in MODELOS_DO_BOT:
            return BANCO_DO_BOT
        return None

    def db_for_write(self, model, **hints):
        # O site nunca escreve no banco do bot. Devolver o banco aqui faria
        # um `.save()` acidental corromper o histórico de preços.
        if model._meta.app_label == APP and model._meta.model_name in MODELOS_DO_BOT:
            return None
        return None

    def allow_relation(self, obj1, obj2, **hints):
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if db == BANCO_DO_BOT:
            return False  # o schema é do bot; o Django não toca
        if model_name in MODELOS_DO_BOT:
            return False  # não recriar as tabelas do bot no banco do site
        return None

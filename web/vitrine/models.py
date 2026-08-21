"""Modelos do site.

Dois bancos: o `default` guarda o que é do site (grupos, inscritos) e o
`promos` é o SQLite que o bot preenche, montado somente para leitura. Os
modelos que apontam para ele usam `managed = False` — o schema é do bot, e
uma migração do Django em cima dele quebraria a coleta.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class Grupo(models.Model):
    """Um grupo de WhatsApp da comunidade.

    O WhatsApp limita membros por grupo, então a comunidade vira vários
    grupos ao longo do tempo. A landing sempre manda para o primeiro que
    ainda tem vaga.
    """

    nome = models.CharField(max_length=80)
    convite = models.URLField(
        "link de convite",
        help_text="URL chat.whatsapp.com gerada no próprio grupo",
    )
    categoria = models.CharField(
        max_length=40,
        blank=True,
        help_text="Ex.: Geral, Eletrônicos, Casa. Vazio = grupo geral",
    )
    membros = models.PositiveIntegerField(default=0)
    capacidade = models.PositiveIntegerField(default=1024)
    ativo = models.BooleanField(default=True)
    ordem = models.PositiveSmallIntegerField(
        default=0, help_text="Menor primeiro. Define qual grupo recebe as entradas."
    )
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["ordem", "id"]
        verbose_name_plural = "grupos"

    def __str__(self) -> str:
        return f"{self.nome} ({self.membros}/{self.capacidade})"

    @property
    def lotado(self) -> bool:
        return self.membros >= self.capacidade

    @property
    def ocupacao_pct(self) -> int:
        if not self.capacidade:
            return 0
        return min(100, round(self.membros / self.capacidade * 100))

    @property
    def vagas(self) -> int:
        return max(0, self.capacidade - self.membros)

    @classmethod
    def aberto(cls) -> "Grupo | None":
        """Primeiro grupo com vaga — para onde o botão principal aponta."""
        for grupo in cls.objects.filter(ativo=True):
            if not grupo.lotado:
                return grupo
        return None


class Inscrito(models.Model):
    """Captura de contato para quando todos os grupos estiverem lotados.

    Fallback de conversão: sem isso, visitante que chega com tudo cheio vai
    embora e não volta.
    """

    email = models.EmailField(unique=True)
    origem = models.CharField(max_length=40, blank=True)
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-criado_em"]

    def __str__(self) -> str:
        return self.email


# ---------- Espelho do banco do bot (somente leitura) ----------


class ProdutoManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().using("promos")


class Produto(models.Model):
    """Tabela `products` do bot."""

    id = models.TextField(primary_key=True)
    source = models.TextField()
    external_id = models.TextField()
    title = models.TextField()
    url = models.TextField()
    image_url = models.TextField(null=True)
    category = models.TextField(null=True)
    first_seen_at = models.TextField()
    last_seen_at = models.TextField()

    objects = ProdutoManager()

    class Meta:
        managed = False
        db_table = "products"


class PostManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().using("promos")

    def publicados(self):
        """Ofertas que realmente foram enviadas — é o que vira vitrine."""
        return self.get_queryset().filter(status="sent").order_by("-created_at")


class Post(models.Model):
    """Tabela `posts` do bot: uma oferta que passou no filtro e foi enviada."""

    id = models.AutoField(primary_key=True)
    product_id = models.TextField()
    price = models.FloatField()
    baseline = models.FloatField()
    discount_pct = models.FloatField()
    copy = models.TextField()
    image_url = models.TextField(null=True)
    attempts = models.IntegerField(default=0)
    status = models.TextField()
    error = models.TextField(null=True)
    created_at = models.TextField()
    sent_at = models.TextField(null=True)

    objects = PostManager()

    class Meta:
        managed = False
        db_table = "posts"

    @property
    def economia(self) -> float:
        return max(0.0, self.baseline - self.price)


class HistoricoPreco(models.Model):
    """Tabela `price_history`: um preço por produto por dia.

    É o diferencial do site — dá para desenhar a curva de 60 dias e provar
    que a queda é real, em vez de repetir o "de R$X por R$Y" da loja.
    """

    product_id = models.TextField(primary_key=True)  # PK composta; ver Meta
    observed_on = models.TextField()
    price = models.FloatField()
    original_price = models.FloatField(null=True)
    available = models.IntegerField(default=1)

    class Meta:
        managed = False
        db_table = "price_history"
        # A PK real é (product_id, observed_on). O Django não modela PK
        # composta; como o acesso aqui é só leitura e sempre filtrado por
        # produto, declarar product_id como PK basta e não gera migração.

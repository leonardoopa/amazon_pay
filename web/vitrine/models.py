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
    jid = models.CharField(
        "JID do WhatsApp",
        max_length=80,
        blank=True,
        help_text=(
            "Termina em @g.us. Sai do `promo wa-groups`. Com ele preenchido, o "
            "comando `sincronizar_grupos` atualiza o número de membros sozinho."
        ),
    )
    membros_atualizados_em = models.DateTimeField(null=True, blank=True)
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


class Clique(models.Model):
    """Um clique no botão de entrar no grupo.

    O site existe para uma coisa: levar gente para o grupo. Antes disso o
    botão apontava direto para o `chat.whatsapp.com`, e o número de cliques
    não existia em lugar nenhum — não havia como saber se a página convertia,
    nem qual chamada convertia mais. Uma linha por clique responde as duas
    perguntas e ainda permite trocar o convite sem editar template.

    Uma linha por clique, e não um contador: contador diz "500" e não diz de
    onde. `origem` é o que separa o botão do topo do botão do fim da página.
    """

    ORIGENS = [
        ("topo", "barra do topo"),
        ("cinema", "filme de abertura"),
        ("hero", "chamada principal"),
        ("grupos", "cartão do grupo"),
        ("oferta", "página de oferta"),
        # Não vem do site: é a assinatura no fim de cada post do WhatsApp.
        # Separada das demais porque mede outra coisa — quanto o grupo cresce
        # por encaminhamento, e não quanto a landing converte.
        ("post", "assinatura do post no grupo"),
        ("desconhecida", "sem origem declarada"),
    ]

    grupo = models.ForeignKey(
        "Grupo",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cliques",
        db_constraint=False,
    )
    origem = models.CharField(max_length=20, choices=ORIGENS, default="desconhecida")
    criado_em = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name_plural = "cliques"
        indexes = [models.Index(fields=["criado_em"])]

    def __str__(self) -> str:
        return f"{self.origem} em {self.criado_em:%d/%m/%Y %H:%M}"


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

    def medidos(self):
        """Só o que foi medido contra a nossa mediana.

        O bot também repassa oferta da vitrine do ML, onde o desconto vem do
        preço riscado da loja (`verified=0`). Repasse serve para o grupo não
        ficar mudo nos primeiros dias, mas não é prova de nada — então tudo
        que o site apresenta como prova (contadores, produto em destaque,
        curva) sai daqui, e não de `publicados()`.
        """
        return self.publicados().filter(verified=True)


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
    # 1 = desconto medido contra a nossa mediana; 0 = repasse da vitrine do ML,
    # medido contra o "de/por" da loja. Quem escreve é o bot (ver
    # promo.db.create_post); aqui é só leitura.
    verified = models.BooleanField(default=True)
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
        """Quanto abaixo da baseline. Só significa "economia" se for medido.

        Num repasse a baseline é o preço riscado do anúncio, e a loja escolhe
        esse número — somar isso como economia seria repetir a conta da loja.
        """
        if not self.verified:
            return 0.0
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

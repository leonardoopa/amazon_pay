"""Testes do que só aparece quando o site vai para o ar.

Duas classes de falha silenciosa moram aqui:

- Meta tag de preview errada. O site continua abrindo normal, mas o link
  colado no WhatsApp vira retângulo cinza e ninguém clica. Não há erro,
  nenhum log, e o canal de crescimento simplesmente não funciona.
- Credencial de produção ausente. O site sobe, responde 200, e fica com a
  chave de assinatura que está publicada no git.
"""

from __future__ import annotations

from comunidade.seguranca import (
    CHAVE_DE_DESENVOLVIMENTO,
    chave_secreta,
    hosts_permitidos,
)
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, TestCase
from django.urls import reverse

from .views import _absoluta


class PreviewDeLinkTests(TestCase):
    databases = {"default", "promos"}

    def test_canonical_e_og_url_sao_a_mesma_url_absoluta(self):
        html = self.client.get(reverse("vitrine:home")).content.decode()
        self.assertIn('<link rel="canonical" href="http://testserver/">', html)
        self.assertIn('<meta property="og:url" content="http://testserver/">', html)

    def test_query_de_campanha_nao_entra_na_canonical(self):
        """`?utm_source=zap` não cria página nova; indexar por campanha
        espalharia a autoridade da mesma página em várias URLs."""
        html = self.client.get("/?utm_source=zap").content.decode()
        self.assertIn('href="http://testserver/">', html)
        self.assertNotIn("utm_source", html)

    def test_home_tem_imagem_de_preview_absoluta(self):
        """Sem banco do bot não há produto em destaque, e é justamente aí que
        a imagem padrão precisa entrar — o dia 1 é quando mais se divulga."""
        html = self.client.get(reverse("vitrine:home")).content.decode()
        self.assertIn('property="og:image" content="http://testserver/static/', html)
        self.assertIn('name="twitter:card"', html)

    def test_url_de_cdn_da_loja_passa_inteira(self):
        """A foto do produto vem do CDN do Mercado Livre, já absoluta. Se
        `build_absolute_uri` a reescrevesse, o preview apontaria para um
        caminho inexistente no nosso domínio."""
        pedido = RequestFactory().get("/")
        externa = "https://http2.mlstatic.com/D_NQ_NP_2X_123-MLB.jpg"
        self.assertEqual(_absoluta(pedido, externa), externa)

    def test_caminho_relativo_vira_absoluto(self):
        pedido = RequestFactory().get("/")
        self.assertEqual(
            _absoluta(pedido, "/static/vitrine/video/poster.jpg"),
            "http://testserver/static/vitrine/video/poster.jpg",
        )

    def test_sem_imagem_nao_inventa_meta_tag(self):
        self.assertIsNone(_absoluta(RequestFactory().get("/"), None))

    def test_comentario_de_template_nao_vaza_para_a_pagina(self):
        """`{# #}` comenta UMA linha. Quebrado em duas, o texto do comentário
        aparece no topo da página como conteúdo — e o site segue respondendo
        200, então nada acusa o erro além de olhar a tela."""
        html = self.client.get(reverse("vitrine:home")).content.decode()
        for marca in ("{#", "#}", "{%", "%}"):
            self.assertNotIn(marca, html)


class CredencialDeProducaoTests(TestCase):
    """A regra é falhar fechado: subir sem chave tem que doer no deploy, não
    depois, com sessão de admin assinável por qualquer pessoa."""

    def test_producao_sem_chave_nao_sobe(self):
        with self.assertRaises(ImproperlyConfigured):
            chave_secreta("", debug=False)

    def test_chave_do_ambiente_e_usada_como_esta(self):
        self.assertEqual(chave_secreta("abc123", debug=False), "abc123")

    def test_local_sem_chave_usa_a_de_desenvolvimento(self):
        self.assertEqual(chave_secreta("", debug=True), CHAVE_DE_DESENVOLVIMENTO)

    def test_producao_sem_hosts_nao_sobe(self):
        with self.assertRaises(ImproperlyConfigured):
            hosts_permitidos("", debug=False)

    def test_hosts_saem_limpos_da_lista(self):
        self.assertEqual(
            hosts_permitidos(
                " ofertas.exemplo.com.br , www.exemplo.com.br ", debug=False
            ),
            ["ofertas.exemplo.com.br", "www.exemplo.com.br"],
        )

    def test_local_sem_hosts_cai_no_localhost(self):
        self.assertEqual(hosts_permitidos("", debug=True), ["localhost", "127.0.0.1"])

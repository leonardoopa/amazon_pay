"""O site não pode apresentar repasse da loja como medição nossa.

O bot manda dois tipos de oferta para o grupo: a que ele mediu contra a
mediana de 60 dias (`verified=1`) e o repasse da vitrine do Mercado Livre,
onde o desconto é contra o preço que a própria loja riscou (`verified=0`). O
segundo existe para o grupo não ficar mudo nos primeiros dias.

A distinção vivia só na memória do pipeline e morria no INSERT. O site, sem
ela, chamava o "de/por" da loja de "média", somava isso na economia e podia
pôr um repasse no lugar de maior destaque da página — a mesma prática que a
landing acusa a loja de ter.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from . import views
from .forms import InscricaoForm
from .models import Post
from .precos import montar_curva


def _post(verificado: bool, preco=100.0, baseline=150.0, desconto=33.0):
    return SimpleNamespace(
        product_id="mercadolivre:MLB1",
        price=preco,
        baseline=baseline,
        discount_pct=desconto,
        verified=verificado,
        sent_at="2026-08-20T10:00:00+00:00",
        created_at="2026-08-20T09:00:00+00:00",
        economia=Post.economia.fget(
            SimpleNamespace(baseline=baseline, price=preco, verified=verificado)
        ),
    )


def _item(verificado: bool, external_id="MLB1", desconto=33.0):
    return {
        "post": _post(verificado, desconto=desconto),
        "produto": SimpleNamespace(
            id=f"mercadolivre:{external_id}",
            external_id=external_id,
            title=f"Produto {external_id}",
            image_url="https://http2.mlstatic.com/foto.jpg",
        ),
        "external_id": external_id,
        "quando": None,
        "verificado": verificado,
    }


class EconomiaTests(TestCase):
    def test_repasse_nao_soma_economia(self):
        """A baseline do repasse é o número que a loja escolheu. Somar isso
        como economia é repetir a conta da loja com a nossa assinatura."""
        post = SimpleNamespace(baseline=150.0, price=100.0, verified=False)
        self.assertEqual(Post.economia.fget(post), 0.0)

    def test_oferta_medida_soma_a_diferenca(self):
        post = SimpleNamespace(baseline=150.0, price=100.0, verified=True)
        self.assertEqual(Post.economia.fget(post), 50.0)


class DestaqueTests(TestCase):
    """O produto do topo e a vitrine que rola são os dois lugares onde a
    página afirma medição com mais ênfase: número grande e curva ao lado."""

    databases = {"default", "promos"}

    def test_repasse_nunca_vira_destaque(self):
        with mock.patch.object(views, "_historico", return_value=[100.0, 90.0, 80.0]):
            escolhidas = views._com_curva([_item(verificado=False)], 4)

        self.assertEqual(escolhidas, [])

    def test_repasse_nao_desloca_oferta_medida_mesmo_com_desconto_maior(self):
        """Ordenar por desconto punha o repasse na frente: o "de/por" da loja
        produz percentual maior justamente porque o preço riscado é escolhido
        por ela."""
        itens = [
            _item(verificado=False, external_id="MLB9", desconto=70.0),
            _item(verificado=True, external_id="MLB1", desconto=20.0),
        ]
        with mock.patch.object(views, "_historico", return_value=[100.0, 90.0, 80.0]):
            escolhidas = views._com_curva(itens, 4)

        self.assertEqual([i["external_id"] for i in escolhidas], ["MLB1"])


class ContadoresTests(TestCase):
    databases = {"default", "promos"}

    def setUp(self):
        # Os contadores são guardados em cache por um minuto. Sem limpar, o
        # teste leria o número que outro teste deixou lá.
        cache.clear()

    def test_contadores_de_oferta_usam_so_o_medido(self):
        """Os rótulos na tela dizem "aprovadas no filtro" e "abaixo da
        mediana". Repasse não passou por filtro nosso."""
        medidos = mock.Mock()
        medidos.count.return_value = 3
        # A economia sai de um SUM no banco, não de objetos carregados aqui.
        medidos.filter.return_value.aggregate.return_value = {"total": 110.0}
        gerente = mock.Mock()
        gerente.medidos.return_value = medidos

        # Os outros dois contadores precisam devolver número de verdade: o
        # resultado vai para o cache, e cache guarda o valor por pickle — um
        # Mock ali estoura na serialização, não na asserção.
        catalogo = mock.Mock()
        catalogo.objects.exclude.return_value.count.return_value = 7
        historico = mock.Mock()
        historico.objects.using.return_value.exclude.return_value.count.return_value = 9

        with (
            mock.patch.object(views, "Post", SimpleNamespace(objects=gerente)),
            mock.patch.object(views, "Produto", catalogo),
            mock.patch.object(views, "HistoricoPreco", historico),
        ):
            numeros = views._numeros()

        self.assertEqual(numeros["ofertas_enviadas"], 3)
        self.assertEqual(numeros["economia_total"], 110.0)
        gerente.publicados.assert_not_called()


class RotuloDoCardTests(TestCase):
    """Mesmo layout com rótulo diferente. O card não pode dizer "média" em
    cima de um número que a loja escolheu."""

    def _html(self, itens):
        return render_to_string(
            "vitrine/home.html",
            {
                "ofertas": itens,
                "esteira": [i for i in itens if i["verificado"]],
                "tem_repasse": any(not i["verificado"] for i in itens),
                "grupos": [],
                "numeros": {
                    "produtos": 1,
                    "observacoes": 1,
                    "ofertas_enviadas": 1,
                    "economia_total": 1.0,
                },
                "janela_dias": 60,
                "form": InscricaoForm(),
            },
        )

    def test_oferta_medida_diz_media(self):
        html = self._html([_item(verificado=True)])
        self.assertIn("média", html)
        self.assertIn("economia de R$", html)
        self.assertNotIn("preço da loja", html)

    def test_repasse_diz_preco_da_loja(self):
        html = self._html([_item(verificado=False)])
        self.assertIn("preço da loja", html)
        self.assertIn("desconto anunciado pela loja", html)
        self.assertNotIn("economia de R$", html)

    def test_esteira_nao_leva_repasse(self):
        """A pílula passa rápido e não tem espaço para a ressalva."""
        html = self._html([_item(verificado=False, external_id="MLB9")])
        self.assertNotIn("pilula", html)

    def test_esteira_leva_oferta_medida(self):
        html = self._html([_item(verificado=True, external_id="MLB1")])
        self.assertIn("pilula", html)


class PaginaDaOfertaTests(TestCase):
    def _html(self, verificado: bool):
        return render_to_string(
            "vitrine/oferta.html",
            {
                "produto": SimpleNamespace(
                    title="Monitor", image_url="https://http2.mlstatic.com/f.jpg"
                ),
                "post": _post(verificado),
                "curva": montar_curva([150.0, 120.0, 100.0]),
                "dias": 3,
                "verificado": verificado,
                "demonstracao": False,
            },
        )

    def test_medida_afirma_mediana(self):
        html = self._html(True)
        self.assertIn("mediana", html)
        self.assertNotIn("o que a loja anuncia", html)

    def test_repasse_diz_de_onde_veio_o_desconto(self):
        html = self._html(False)
        self.assertIn("preço da loja", html)
        self.assertIn("o que a loja anuncia", html)


class CabecalhosTests(TestCase):
    databases = {"default", "promos"}

    def test_resposta_leva_csp(self):
        resposta = self.client.get(reverse("vitrine:home"))

        csp = resposta.headers["Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        # A foto do produto vem do CDN da loja, que troca de host sem aviso.
        self.assertIn("img-src 'self' data: https:", csp)
        # A folha do Google Fonts precisa passar, senão a página perde a fonte.
        self.assertIn("https://fonts.googleapis.com", csp)

    @override_settings(HTTPS=False)
    def test_sem_tls_a_csp_nao_manda_upgrade_de_subrecurso(self):
        """Servido por HTTP, `upgrade-insecure-requests` apaga a página.

        A diretiva reescreve todo subrecurso `http://` para `https://`. Sem
        certificado na frente — o modo por IP que o DEPLOY.md documenta para
        quem ainda não tem domínio — o navegador vai buscar o CSS na 443, leva
        ERR_CONNECTION_REFUSED e renderiza o HTML cru, sem estilo e sem os
        filmes. Aconteceu em produção no primeiro deploy.
        """
        resposta = self.client.get(reverse("vitrine:home"))

        csp = resposta.headers["Content-Security-Policy"]
        self.assertNotIn("upgrade-insecure-requests", csp)
        # O resto da política continua valendo: o que muda é só a diretiva
        # que depende de haver TLS.
        self.assertIn("frame-ancestors 'none'", csp)

    @override_settings(HTTPS=True)
    def test_com_tls_a_csp_volta_a_mandar_o_upgrade(self):
        resposta = self.client.get(reverse("vitrine:home"))

        self.assertIn(
            "upgrade-insecure-requests",
            resposta.headers["Content-Security-Policy"],
        )

    def test_resposta_desliga_o_que_a_pagina_nao_usa(self):
        resposta = self.client.get(reverse("vitrine:home"))

        politica = resposta.headers["Permissions-Policy"]
        self.assertIn("camera=()", politica)
        self.assertIn("microphone=()", politica)

    def test_script_inline_nao_existe_na_pagina(self):
        """`script-src 'self'` mata script inline. Se um aparecer no template,
        a página quebra em produção e passa no teste — a não ser este."""
        html = self.client.get(reverse("vitrine:home")).content.decode()

        self.assertNotIn("<script>", html)
        self.assertNotIn("javascript:", html)


class SaudeSobHTTPSTests(TestCase):
    """O healthcheck não pode ser vítima do redirect de HTTPS.

    O compose chama `http://127.0.0.1:8001/saude/` de dentro do container,
    onde não há TLS -- o certificado termina no Caddy, fora dele. Sem a isenção
    a sonda recebe 301, segue para https na mesma porta e morre em handshake;
    o container fica `unhealthy` para sempre e o portão de saúde do deploy
    reprova uma publicação que está no ar e funcionando.
    """

    databases = {"default", "promos"}

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_saude_responde_sem_redirecionar_para_https(self):
        resposta = self.client.get(reverse("vitrine:saude"))

        self.assertEqual(resposta.status_code, 200)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_o_resto_do_site_continua_redirecionando(self):
        """A isenção vale para o healthcheck e para mais nada."""
        resposta = self.client.get(reverse("vitrine:home"))

        self.assertEqual(resposta.status_code, 301)
        self.assertTrue(resposta["Location"].startswith("https://"))

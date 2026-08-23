"""O caminho do visitante até o grupo, e as defesas em volta dele.

Tudo aqui protege coisa que falha em silêncio: clique que não é contado (o
site parece não converter), formulário que aceita robô (a lista de e-mails
vira lixo), número que o script lê errado (o contador anima até 1), e vídeo
de 12MB baixado em rede de celular (a página parece travada).
"""

from __future__ import annotations

from unittest import mock
from xml.etree import ElementTree

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from . import views
from .forms import CAMPO_ISCA, InscricaoForm
from .models import Clique, Grupo, Inscrito

CONVITE = "https://chat.whatsapp.com/ABC"


class EntrarTests(TestCase):
    databases = {"default", "promos"}

    def setUp(self):
        cache.clear()
        self.grupo = Grupo.objects.create(
            nome="Comunidade #1", convite=CONVITE, membros=10, capacidade=1024
        )

    def test_redireciona_para_o_convite(self):
        resposta = self.client.get(reverse("vitrine:entrar"))

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(resposta["Location"], CONVITE)

    def test_registra_o_clique_com_a_origem(self):
        self.client.get(reverse("vitrine:entrar") + "?de=hero")

        clique = Clique.objects.get()
        self.assertEqual(clique.origem, "hero")
        self.assertEqual(clique.grupo, self.grupo)

    def test_origem_desconhecida_nao_entra_como_veio(self):
        """`?de=` é parâmetro de URL, ou seja, texto de fora. Só o que está em
        ORIGENS é gravado — o resto vira "desconhecida"."""
        self.client.get(reverse("vitrine:entrar") + "?de=<script>alert(1)</script>")

        self.assertEqual(Clique.objects.get().origem, "desconhecida")

    def test_sem_grupo_com_vaga_manda_para_a_captura(self):
        """Quem clicou já demonstrou interesse: é a hora de pedir o e-mail,
        não de mostrar erro."""
        self.grupo.membros = self.grupo.capacidade
        self.grupo.save()

        resposta = self.client.get(reverse("vitrine:entrar") + "?de=topo")

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(resposta["Location"].endswith("#vaga"))
        # O clique continua contando: é demanda sem vaga, o número que diz
        # quando abrir o próximo grupo.
        self.assertEqual(Clique.objects.get().origem, "topo")


class IscaTests(TestCase):
    databases = {"default", "promos"}

    def setUp(self):
        cache.clear()

    def test_formulario_recusa_quando_a_isca_vem_preenchida(self):
        form = InscricaoForm({"email": "a@b.com", CAMPO_ISCA: "http://spam"})

        self.assertFalse(form.is_valid())

    def test_formulario_aceita_com_a_isca_vazia(self):
        form = InscricaoForm({"email": "a@b.com", CAMPO_ISCA: ""})

        self.assertTrue(form.is_valid(), form.errors)

    def test_post_com_isca_nao_grava_inscrito(self):
        self.client.post(
            reverse("vitrine:inscrever"),
            {"email": "robo@spam.com", CAMPO_ISCA: "http://spam"},
        )

        self.assertFalse(Inscrito.objects.filter(email="robo@spam.com").exists())

    def test_a_isca_esta_no_html_fora_da_tela(self):
        html = render_to_string(
            "vitrine/home.html",
            {
                "form": InscricaoForm(),
                "grupos": [],
                "ofertas": [],
                "esteira": [],
                "numeros": {
                    "produtos": 0,
                    "observacoes": 0,
                    "ofertas_enviadas": 0,
                    "economia_total": 0,
                },
                "janela_dias": 60,
            },
        )

        self.assertIn('name="url"', html)
        self.assertIn("isca-caixa", html)
        # Sem rótulo visível e fora da ordem de tabulação: para quem usa o
        # site, o campo não existe.
        self.assertIn('tabindex="-1"', html)

    def test_get_no_inscrever_nao_e_permitido(self):
        """Formulário é POST. GET aqui só apareceria por robô varrendo URL."""
        self.assertEqual(self.client.get(reverse("vitrine:inscrever")).status_code, 405)


class LimiteDeEnvioTests(TestCase):
    databases = {"default", "promos"}

    def setUp(self):
        cache.clear()

    def test_passar_do_limite_bloqueia_o_envio(self):
        for i in range(views.LIMITE_ENVIOS):
            self.client.post(reverse("vitrine:inscrever"), {"email": f"a{i}@b.com"})

        self.client.post(reverse("vitrine:inscrever"), {"email": "ultimo@b.com"})

        self.assertEqual(Inscrito.objects.count(), views.LIMITE_ENVIOS)
        self.assertFalse(Inscrito.objects.filter(email="ultimo@b.com").exists())

    def test_ip_do_visitante_vem_do_cabecalho_do_proxy(self):
        """Atrás do Caddy o REMOTE_ADDR é o container, igual para todo mundo —
        limitar por ele barraria o site inteiro por causa de um robô."""
        pedido = mock.Mock()
        pedido.META = {
            "HTTP_X_FORWARDED_FOR": "203.0.113.7, 10.0.0.1",
            "REMOTE_ADDR": "172.18.0.5",
        }

        self.assertEqual(views._ip_do_visitante(pedido), "203.0.113.7")


class RobotsESitemapTests(TestCase):
    databases = {"default", "promos"}

    def setUp(self):
        cache.clear()

    def test_robots_aponta_para_o_sitemap_no_dominio_de_quem_pediu(self):
        resposta = self.client.get("/robots.txt")

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta["Content-Type"].startswith("text/plain"))
        corpo = resposta.content.decode()
        self.assertIn("http://testserver/sitemap.xml", corpo)

    def test_robots_mantem_o_redirecionamento_fora_do_indice(self):
        """`/entrar/` é redirecionamento para o WhatsApp, não conteúdo — e
        cada visita de robô ali viraria clique falso na medição."""
        corpo = self.client.get("/robots.txt").content.decode()

        self.assertIn("Disallow: /entrar/", corpo)
        self.assertIn("Disallow: /admin/", corpo)

    def test_sitemap_e_xml_valido_com_a_home(self):
        resposta = self.client.get("/sitemap.xml")

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta["Content-Type"], "application/xml")
        raiz = ElementTree.fromstring(resposta.content)
        enderecos = [
            no.text
            for no in raiz.iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")
        ]
        self.assertIn("http://testserver/", enderecos)


class CacheDosNumerosTests(TestCase):
    databases = {"default", "promos"}

    def setUp(self):
        cache.clear()

    def test_segunda_visita_nao_consulta_o_banco_de_novo(self):
        """A coleta muda esse dado a cada duas horas; consultar por visita era
        ler o mesmo arquivo que o bot está escrevendo ao lado."""
        with mock.patch.object(
            views, "_numeros_do_banco", return_value={"produtos": 1}
        ) as consulta:
            views._numeros()
            views._numeros()

        self.assertEqual(consulta.call_count, 1)


class FormatacaoDeNumeroTests(TestCase):
    databases = {"default", "promos"}

    def setUp(self):
        cache.clear()

    def _html(self):
        return render_to_string(
            "vitrine/home.html",
            {
                "form": InscricaoForm(),
                "grupos": [],
                "ofertas": [],
                "esteira": [],
                "numeros": {
                    "produtos": 1234,
                    "observacoes": 56789,
                    "ofertas_enviadas": 42,
                    "economia_total": 9876.0,
                },
                "janela_dias": 60,
            },
        )

    def test_numero_visivel_leva_separador_de_milhar(self):
        html = self._html()

        self.assertIn(">1.234<", html)
        self.assertIn("R$ 9.876<", html)

    def test_numero_lido_por_script_sai_cru(self):
        """`Number("1.234")` em JavaScript é 1,234 — o contador animaria de 0
        a 1 e o painel viraria uma fileira de números errados."""
        html = self._html()

        self.assertIn('data-contar="1234"', html)
        self.assertIn('data-contar="56789"', html)
        self.assertIn('data-contar="9876"', html)


class VideoEmRedeCaraTests(TestCase):
    """12MB no topo e ~6MB por produto. Em 4G isso é franquia de dados do
    visitante gasta numa animação, e segundos de tela preta antes da primeira
    frase."""

    def _arquivo(self, *partes) -> str:
        from pathlib import Path

        return (Path(__file__).resolve().parent.joinpath(*partes)).read_text(
            encoding="utf-8"
        )

    def test_o_filme_de_abertura_nao_tem_src_no_html(self):
        html = self._arquivo("templates", "vitrine", "_scroll_cinema.html")

        self.assertIn("data-fonte=", html)
        self.assertNotIn('src="{{ cinema_video }}"', html)
        self.assertIn('preload="none"', html)

    def test_os_dois_scripts_consultam_a_mesma_regra(self):
        """Metade da página economizando dados e a outra metade não é pior do
        que qualquer das duas escolhas inteiras."""
        cinema = self._arquivo("static", "vitrine", "js", "scroll-cinema.js")
        vitrine = self._arquivo("static", "vitrine", "js", "scroll.js")

        self.assertIn("window.RedeCara", cinema)
        self.assertIn("window.RedeCara", vitrine)

    def test_a_regra_respeita_economia_de_dados_e_rede_inviavel(self):
        """`3g` fica fora da lista de propósito: `effectiveType` é estimativa
        de RTT e vazão, não o rádio, e o Chrome reporta "3g" em 4G comum de
        celular. Com "3g" ali, quem tem 4G mediano perdia os três filmes e
        via só a foto."""
        rede = self._arquivo("static", "vitrine", "js", "rede.js")

        self.assertIn("saveData", rede)
        self.assertIn("effectiveType", rede)
        inicio = rede.index("const INVIAVEIS")
        lista = rede[inicio : rede.index(";", inicio)]
        self.assertIn("slow-2g", lista)
        self.assertIn('"2g"', lista)
        self.assertNotIn('"3g"', lista)
        self.assertNotIn('"4g"', lista)

    def test_sem_filme_a_secao_mantem_a_altura(self):
        """Zerar a altura zeraria o curso de rolagem: o progresso travaria em 0
        e a página pareceria quebrada — o oposto de economizar dados."""
        css = self._arquivo("static", "vitrine", "css", "app.css")

        self.assertIn(".sc-hero {", css)
        self.assertNotIn("RedeCara", css)


class TemaTests(TestCase):
    def test_nao_ha_regra_de_tema_claro_inalcancavel(self):
        """Havia um bloco `prefers-color-scheme: light` sob o seletor
        `:root:not([data-theme="dark"])`, e o base.html fixa `data-theme="dark"`
        — o seletor nunca casava. Código morto que dava a impressão de haver
        tema claro suportado.

        Para ligar tema claro de verdade: tirar o atributo do base.html e
        conferir o Scroll Cinema, que é escuro por conta própria e não segue
        token. Enquanto isso não acontecer, a regra não volta."""
        from pathlib import Path

        css = (
            Path(__file__).resolve().parent / "static" / "vitrine" / "css" / "app.css"
        ).read_text(encoding="utf-8")

        self.assertNotIn("prefers-color-scheme: light", css)
        # O atributo continua fixo: é ele que define o tema único da página.
        base = (
            Path(__file__).resolve().parent / "templates" / "vitrine" / "base.html"
        ).read_text(encoding="utf-8")
        self.assertIn('data-theme="dark"', base)


class SincronizarGruposTests(TestCase):
    """A ocupação do grupo deixou de ser número digitado à mão.

    O que precisa ser travado aqui é a tolerância ao payload: a Evolution já
    mudou o formato dessa resposta entre versões, e uma atualização de imagem
    não pode parar a sincronização em silêncio.
    """

    def _comando(self):
        from vitrine.management.commands.sincronizar_grupos import Command

        return Command()

    def test_le_o_campo_size(self):
        self.assertEqual(self._comando()._extrair_total({"size": 412}), 412)

    def test_cai_para_a_lista_de_participantes(self):
        corpo = {"participants": [{"id": "1"}, {"id": "2"}, {"id": "3"}]}
        self.assertEqual(self._comando()._extrair_total(corpo), 3)

    def test_resposta_sem_o_que_contar_devolve_none(self):
        """None significa "não sei", e quem não sabe mantém o número anterior —
        ocupação zerada anunciaria vaga em grupo cheio."""
        self.assertIsNone(self._comando()._extrair_total({"subject": "Grupo"}))
        self.assertIsNone(self._comando()._extrair_total("erro"))

    def test_sem_grupo_com_jid_nao_chama_a_evolution(self):
        from django.core.management import call_command

        Grupo.objects.create(nome="Sem JID", convite=CONVITE, jid="")
        with mock.patch("httpx.Client") as cliente:
            call_command("sincronizar_grupos", verbosity=0)

        cliente.assert_not_called()


class CacheDoHtmlTests(TestCase):
    """HTML em cache com estático de hash novo é página sem CSS e sem script.

    Os dois andam juntos: arquivo com hash no nome pode ficar um ano no
    navegador justamente porque o HTML que aponta para ele é revalidado.
    """

    databases = {"default", "promos"}

    def setUp(self):
        cache.clear()

    def test_html_pede_revalidacao(self):
        resposta = self.client.get(reverse("vitrine:home"))

        self.assertIn("no-cache", resposta.headers["Cache-Control"])

    def test_resposta_que_nao_e_html_fica_como_esta(self):
        """`/saude/` é JSON de monitoração e o sitemap é XML: nenhum dos dois
        depende de hash de estático, e o middleware não inventa política."""
        for caminho in ("/saude/", "/sitemap.xml"):
            with self.subTest(caminho=caminho):
                resposta = self.client.get(caminho)
                self.assertNotIn("Cache-Control", resposta.headers)

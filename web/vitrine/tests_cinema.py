"""Scroll Cinema: o que precisa estar certo antes de existir vídeo.

Os dois erros mais caros do manual são estruturais, não visuais: colapsar
a altura da seção (zera o curso, o progresso trava em 0 e a página parece
quebrada) e mostrar a cena sem o arquivo (spinner eterno sobre tela preta).
Ambos dá para travar por teste hoje.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

ESTATICOS = Path(__file__).resolve().parent / "static" / "vitrine"
CSS = (ESTATICOS / "css" / "app.css").read_text(encoding="utf-8")
JS = (ESTATICOS / "js" / "scroll-cinema.js").read_text(encoding="utf-8")


class CenaTests(TestCase):
    """Os dois ramos, forçados.

    Amarrar o teste ao arquivo existir ou não torna o resultado refém de
    quem rodou o pipeline por último — ele quebra sozinho no dia em que o
    vídeo entra, sem nada ter regredido.
    """

    databases = {"default", "promos"}

    def test_sem_video_a_secao_nao_entra(self):
        """Sem arquivo, a cena viraria tela preta com spinner para sempre."""
        with patch("django.contrib.staticfiles.finders.find", return_value=None):
            resposta = self.client.get(reverse("vitrine:home"))
        self.assertNotContains(resposta, "sc-hero")
        self.assertNotContains(resposta, "data-sc-video")

    def test_com_video_a_secao_entra(self):
        with patch(
            "django.contrib.staticfiles.finders.find",
            return_value="/qualquer/hero.mp4",
        ):
            resposta = self.client.get(reverse("vitrine:home"))
        self.assertContains(resposta, "data-sc-video")
        self.assertContains(resposta, "vitrine/video/hero.mp4")

    def test_o_hero_normal_continua_no_lugar(self):
        """A cena é acréscimo, não substituição: o argumento escrito fica."""
        resposta = self.client.get(reverse("vitrine:home"))
        self.assertContains(resposta, "Desconto de verdade")


class CssTests(TestCase):
    def test_movimento_reduzido_nao_colapsa_a_altura(self):
        """Zerar a altura zera o curso de rolagem: o progresso trava em 0 e
        o site inteiro parece quebrado. Corta-se o que se move, não a
        estrutura."""
        # Ancora no bloco do cinema, não no `prefers-reduced-motion` global
        # que aparece antes no arquivo.
        inicio = CSS.index("/* ---------- movimento reduzido ---------- */")
        bloco = CSS[inicio : CSS.index("/* ---------- telas pequenas", inicio)]
        self.assertIn("prefers-reduced-motion", bloco)
        self.assertNotIn(".sc-hero", bloco)
        self.assertNotIn("height", bloco)

    def test_a_secao_tem_curso_de_rolagem(self):
        """A altura da seção É a duração da animação."""
        inicio = CSS.index(".sc-hero {")
        self.assertIn("500vh", CSS[inicio : inicio + 120])

    def test_o_video_comeca_invisivel_mas_a_classe_o_revela(self):
        self.assertIn(".sc-video.pronto { opacity: 1; }", CSS)


class BindingTests(TestCase):
    def test_le_o_readystate_alem_do_evento(self):
        """`loadedmetadata` pode disparar antes do script rodar. Confiar só
        no evento deixa a duração em 0 e o efeito parece morto."""
        self.assertIn("video.readyState >= 1", JS)

    def test_aplica_direto_quando_a_aba_esta_oculta(self):
        """Painel de preview embutido não entrega requestAnimationFrame."""
        self.assertIn("document.hidden", JS)

    def test_tem_saida_para_rede_lenta(self):
        """Sem isso, rede ruim deixa o visitante no spinner e ele vai embora."""
        self.assertIn("ESPERA_MAXIMA", JS)
        self.assertIn("setTimeout(liberar", JS)

    def test_o_progresso_fica_entre_zero_e_um(self):
        self.assertIn("Math.min(1, Math.max(0,", JS)

    def test_o_mesmo_progresso_alimenta_video_e_enfeites(self):
        """Uma variável só comanda a cena inteira — é o ponto do método."""
        self.assertIn("video.currentTime = alvo * duracao", JS)
        for enfeite in ("contador", "barra", "altura", "giro"):
            self.assertIn(f'"{enfeite}"', JS)

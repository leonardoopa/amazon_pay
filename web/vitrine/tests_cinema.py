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
JS_SCROLL = (ESTATICOS / "js" / "scroll.js").read_text(encoding="utf-8")


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


class FilmeDoProdutoTests(TestCase):
    """O produto troca a foto por vídeo quando existe arquivo com o id dele."""

    databases = {"default", "promos"}

    @staticmethod
    def _finder(existentes: set[str]):
        """Finge um diretório de estáticos com exatamente estes arquivos."""
        return lambda caminho, **kw: (
            f"/fingido/{caminho}" if caminho in existentes else None
        )

    def test_sem_arquivo_o_produto_fica_com_a_foto(self):
        from vitrine.views import _video_do_produto

        with patch(
            "django.contrib.staticfiles.finders.find", side_effect=self._finder(set())
        ):
            self.assertIsNone(_video_do_produto("MLB46211942"))

    def test_o_id_do_produto_e_o_nome_do_arquivo(self):
        """É essa convenção que dispensa tabela de-para no código."""
        from vitrine.views import _video_do_produto

        alvo = "vitrine/video/produto/MLB46211942.mp4"
        with patch(
            "django.contrib.staticfiles.finders.find",
            side_effect=self._finder({alvo}),
        ):
            self.assertEqual(_video_do_produto("MLB46211942"), f"/static/{alvo}")
            # Outro produto não pega carona no arquivo do vizinho.
            self.assertIsNone(_video_do_produto("MLB59090080"))

    def test_o_filme_nasce_sem_src_para_nao_baixar_na_abertura(self):
        """São megabytes por produto, numa seção que a maioria não alcança.
        Se o `src` vier no HTML o navegador baixa tudo na abertura, e o
        atraso cai justo em quem ainda está decidindo se fica."""
        marcacao = """<video class="palco-filme" data-vitrine-video
                     data-fonte="{{ item.video }}"
                     muted playsinline preload="none" aria-hidden="true">"""
        gabarito = (
            Path(__file__).resolve().parent / "templates" / "vitrine" / "home.html"
        ).read_text(encoding="utf-8")
        self.assertIn(marcacao, gabarito)
        self.assertIn("video.src = video.dataset.fonte", JS_SCROLL)
        self.assertIn('video.preload = "auto"', JS_SCROLL)

    def test_os_filmes_baixam_em_fila(self):
        """Três arquivos de ~6MB disparados juntos dividem a banda e chegam
        juntos no fim. O visitante precisa do primeiro primeiro."""
        self.assertIn("const enfileirar = (i)", JS_SCROLL)
        self.assertIn('video.addEventListener("canplaythrough", seguir', JS_SCROLL)
        # Sem prazo, rede ruim trava a fila no primeiro para sempre.
        self.assertIn("setTimeout(seguir, ESPERA_FILA)", JS_SCROLL)

    def test_a_foto_so_apaga_quando_ha_quadro_decodificado(self):
        """`loadedmetadata` já responde com o vídeo ainda sem imagem. Apagar
        a foto ali deixa um retângulo vazio no lugar do produto."""
        self.assertIn("loadeddata", JS_SCROLL)
        self.assertIn("video.readyState >= 2", JS_SCROLL)
        self.assertIn(".palco-com-filme.filme-pronto img { opacity: 0; }", CSS)


class VitrineFixadaTests(TestCase):
    def test_o_modo_fixado_respeita_o_ponto_de_quebra_do_css(self):
        """Abaixo de 861px os itens voltam a ser blocos empilhados. Escrever
        `opacity: 0` neles ali apaga metade da seção no meio da página, e
        estilo embutido ainda vence a media query que deveria consertar."""
        self.assertIn('matchMedia("(min-width: 861px)")', JS_SCROLL)
        self.assertIn("if (!fixado.matches) return;", JS_SCROLL)
        # Sair do modo fixado precisa desfazer o que ele escreveu.
        self.assertIn('el.style.visibility = "";', JS_SCROLL)

    def test_todo_filme_corre_na_mesma_velocidade(self):
        """Janela de uma unidade para todos. Com janela proporcional ao
        número de vizinhos, o item do meio recebia o dobro de curso e o
        mesmo gesto de rolagem avançava metade do clipe."""
        self.assertIn(
            "const abre = Math.min(Math.max(indice - 0.5, 0), ultimo - 1);", JS_SCROLL
        )
        self.assertIn("Math.min(1, Math.max(0, posicao - abre))", JS_SCROLL)

    def test_a_altura_do_marcador_e_a_duracao_da_cena(self):
        """80svh vinham de quando a vitrine era foto parada; com filme no
        lugar, o clipe inteiro cabia num passar de dedo. 350svh resolveu isso
        e criou outro problema: 11 telas presas na mesma seção. 175svh é o
        meio — cena inteira visível, sem rolagem morta dentro do produto."""
        inicio = CSS.index(".js-vitrine .vitrine-marca")
        self.assertIn("175svh", CSS[inicio : inicio + 80])

    def test_o_palco_do_filme_e_quadrado_pela_largura(self):
        """`height: 100%` e `max-width: 100%` juntos anulavam o
        `aspect-ratio`: o palco saía 502x603 e o filme virava recorte
        retrato de um vídeo deitado. E a linha do grid não pode crescer com
        a foto dentro — uma imagem 393x500 esticava o quadrado sozinha."""
        inicio = CSS.index(".js-vitrine .vitrine-item .palco-produto {")
        bloco = CSS[inicio : CSS.index("}", inicio)]
        self.assertIn("width: min(100%, 78svh)", bloco)
        self.assertIn("aspect-ratio: 1", bloco)
        self.assertIn("grid-template-rows: minmax(0, 1fr)", bloco)
        self.assertNotIn("height: 100%", bloco)

    def test_o_filme_ocupa_o_palco_inteiro(self):
        """A foto precisa de ar porque é recorte em fundo branco; o filme
        tem cena própria, e ar em volta dele é só quadro desperdiçado."""
        inicio = CSS.index(".palco-filme {")
        bloco = CSS[inicio : CSS.index("}", inicio)]
        self.assertIn("width: 100%; height: 100%;", bloco)


class EsteiraTests(TestCase):
    def test_o_nome_do_produto_trunca_de_verdade(self):
        """`overflow` e `text-overflow` não se aplicam a elemento inline
        não-substituído, e o gabarito emite `span`. Sem `display: block` as
        três declarações são inertes e o nome inteiro é pintado fora da
        pílula, por cima da vizinha."""
        inicio = CSS.index(".pilula-nome {")
        bloco = CSS[inicio : CSS.index("}", inicio)]
        self.assertIn("display: block;", bloco)
        self.assertIn("text-overflow: ellipsis;", bloco)

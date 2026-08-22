"""Testes da vitrine.

Foco no que quebra em silêncio: coordenada de SVG localizada (o gráfico some
sem erro visível na página) e leitura do banco do bot quando ele ainda não
existe (500 numa landing custa a visita inteira).
"""

from __future__ import annotations

from pathlib import Path

from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from .forms import InscricaoForm
from .models import Grupo, Inscrito
from .precos import montar_curva


class CurvaTests(TestCase):
    def test_coordenadas_saem_com_ponto_decimal(self):
        """Locale pt-BR renderiza float com vírgula, e `y1="18,0"` não desenha."""
        curva = montar_curva([100.0, 90.0, 80.0])
        for valor in (curva.y_mediana, curva.x_atual, curva.y_atual, curva.x_fim):
            self.assertNotIn(",", valor)

    def test_menos_de_dois_pontos_nao_vira_curva(self):
        """Linha de um ponto só sugeriria tendência inexistente."""
        self.assertIsNone(montar_curva([100.0]))
        self.assertIsNone(montar_curva([]))

    def test_preco_estavel_nao_vira_queda_visual(self):
        curva = montar_curva([100.0, 100.0, 100.0])
        self.assertIsNotNone(curva)
        self.assertEqual(curva.desconto_pct, 0)

    def test_rotulo_da_mediana_fica_dentro_do_quadro(self):
        """Com preço estável a mediana cola no topo e o rótulo sairia fora."""
        curva = montar_curva([100.0, 100.0, 100.0])
        self.assertGreaterEqual(float(curva.y_rotulo_mediana), 0)
        self.assertLessEqual(float(curva.y_rotulo_mediana), curva.altura)

    def test_desconto_e_medido_contra_a_mediana(self):
        curva = montar_curva([200.0, 200.0, 200.0, 100.0])
        self.assertEqual(round(curva.desconto_pct), 50)

    def test_marca_menor_da_serie(self):
        self.assertTrue(montar_curva([200.0, 150.0, 100.0]).menor_da_serie)
        self.assertFalse(montar_curva([100.0, 150.0, 200.0]).menor_da_serie)


class GrupoTests(TestCase):
    def test_aberto_devolve_o_primeiro_com_vaga(self):
        Grupo.objects.create(
            nome="1", convite="https://x", membros=10, capacidade=10, ordem=0
        )
        vago = Grupo.objects.create(
            nome="2", convite="https://y", membros=3, capacidade=10, ordem=1
        )
        self.assertEqual(Grupo.aberto(), vago)

    def test_sem_vaga_devolve_none(self):
        Grupo.objects.create(nome="1", convite="https://x", membros=10, capacidade=10)
        self.assertIsNone(Grupo.aberto())

    def test_grupo_inativo_nao_recebe_entrada(self):
        Grupo.objects.create(
            nome="1", convite="https://x", membros=0, capacidade=10, ativo=False
        )
        self.assertIsNone(Grupo.aberto())

    def test_ocupacao_nao_passa_de_cem(self):
        grupo = Grupo(nome="1", convite="https://x", membros=2000, capacidade=1024)
        self.assertEqual(grupo.ocupacao_pct, 100)
        self.assertEqual(grupo.vagas, 0)


class PaginasTests(TestCase):
    """O banco do bot não existe no ambiente de teste — é o cenário do dia 1.

    Declarar `promos` aqui é o ponto: as views precisam continuar respondendo
    200 quando a tabela não existe, e é justamente essa degradação que estes
    testes exercitam.
    """

    databases = {"default", "promos"}

    def test_home_responde_sem_o_banco_do_bot(self):
        resposta = self.client.get(reverse("vitrine:home"))
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Comunidade")

    def test_home_mostra_grupo_com_vaga(self):
        Grupo.objects.create(
            nome="Comunidade do Desconto #1",
            convite="https://chat.whatsapp.com/ABC",
            membros=10,
            capacidade=1024,
        )
        resposta = self.client.get(reverse("vitrine:home"))
        self.assertContains(resposta, "https://chat.whatsapp.com/ABC")

    def test_grupo_lotado_nao_expoe_o_convite(self):
        """Mandar alguém para grupo cheio queima a visita."""
        Grupo.objects.create(
            nome="Cheio",
            convite="https://chat.whatsapp.com/CHEIO",
            membros=1024,
            capacidade=1024,
        )
        resposta = self.client.get(reverse("vitrine:home"))
        self.assertNotContains(resposta, "https://chat.whatsapp.com/CHEIO")
        self.assertContains(resposta, "Grupo lotado")

    def test_oferta_inexistente_da_404(self):
        self.assertEqual(self.client.get("/oferta/MLB000/").status_code, 404)

    def test_inscricao_salva_o_email(self):
        self.client.post(reverse("vitrine:inscrever"), {"email": "a@b.com"})
        self.assertTrue(Inscrito.objects.filter(email="a@b.com").exists())

    def test_inscricao_repetida_nao_estoura(self):
        Inscrito.objects.create(email="a@b.com")
        resposta = self.client.post(reverse("vitrine:inscrever"), {"email": "a@b.com"})
        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(Inscrito.objects.count(), 1)

    def test_divulgacao_de_afiliado_esta_na_pagina(self):
        """Obrigatória pelo programa; não pode sumir num redesign."""
        resposta = self.client.get(reverse("vitrine:home"))
        self.assertContains(resposta, "afiliado")

    def test_contadores_trazem_o_valor_real_no_html(self):
        """Sem JavaScript — ou com a aba em segundo plano, onde o
        requestAnimationFrame congela — o número tem que estar no HTML. Um
        painel de zeros é pior do que painel nenhum."""
        html = render_to_string(
            "vitrine/home.html",
            {
                "numeros": {
                    "produtos": 1234,
                    "observacoes": 56789,
                    "ofertas_enviadas": 42,
                    "economia_total": 9876.0,
                },
                "grupos": [],
                "ofertas": [],
                "janela_dias": 60,
                "form": InscricaoForm(),
            },
        )
        self.assertIn('data-contar="1234">1.234<', html)
        self.assertIn('data-contar="42">42<', html)
        self.assertIn("R$ 9.876<", html)

    def test_conteudo_nao_depende_de_javascript(self):
        """Nada pode nascer escondido: quem esconde é a classe que o JS põe."""
        conteudo = self.client.get(reverse("vitrine:home")).content.decode()
        self.assertIn("data-revelar", conteudo)
        self.assertNotIn("js-revelar", conteudo)
        self.assertNotIn("js-vitrine", conteudo)

    def test_vitrine_empilhada_e_o_estado_padrao_do_css(self):
        """Se o script não subir, a seção fixada não pode virar tela vazia.

        Checa as regras base em vez de fatiar o arquivo por posição: fatiar
        quebrava a cada bloco novo no fim do CSS, sem que nada tivesse
        regredido de verdade.
        """
        css = (
            Path(__file__).resolve().parent / "static" / "vitrine" / "css" / "app.css"
        ).read_text(encoding="utf-8")

        # A regra base do quadro não pode fixar nada.
        inicio = css.index(".vitrine-quadro {")
        base = css[inicio : css.index("}", inicio)]
        self.assertNotIn("sticky", base)

        # Marcadores e indicadores só existem no modo com script.
        self.assertIn(".vitrine-marca { display: none; }", css)
        self.assertIn(".vitrine-pontos { display: none; }", css)
        self.assertIn(".js-vitrine .vitrine-marca", css)

        # E o item não nasce invisível.
        inicio_item = css.index(".vitrine-item {")
        self.assertNotIn("opacity: 0", css[inicio_item : css.index("}", inicio_item)])

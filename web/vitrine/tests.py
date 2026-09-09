"""Testes da vitrine.

Foco no que quebra em silêncio: coordenada de SVG localizada (o gráfico some
sem erro visível na página) e leitura do banco do bot quando ele ainda não
existe (500 numa landing custa a visita inteira).
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from . import views
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

    def test_home_oferece_entrada_quando_ha_vaga(self):
        """O botão aponta para `/entrar/`, que conta o clique e redireciona.

        O convite não aparece mais no HTML — quem quiser o link passa pela
        contagem, e raspador de página não sai daqui com o endereço do grupo.
        """
        Grupo.objects.create(
            nome="Comunidade do Desconto #1",
            convite="https://chat.whatsapp.com/ABC",
            membros=10,
            capacidade=1024,
        )
        resposta = self.client.get(reverse("vitrine:home"))
        self.assertContains(resposta, reverse("vitrine:entrar"))
        self.assertNotContains(resposta, "https://chat.whatsapp.com/ABC")

    def test_grupo_lotado_nao_oferece_entrada(self):
        """Mandar alguém para grupo cheio queima a visita."""
        Grupo.objects.create(
            nome="Cheio",
            convite="https://chat.whatsapp.com/CHEIO",
            membros=1024,
            capacidade=1024,
        )
        resposta = self.client.get(reverse("vitrine:home"))
        self.assertNotContains(resposta, "https://chat.whatsapp.com/CHEIO")
        self.assertNotContains(resposta, reverse("vitrine:entrar"))
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


class VitrineFixaTests(TestCase):
    """A vitrine com filme não pode ser trocada pela oferta do dia.

    O filme de cada produto é feito à mão. Quando a seção seguia o post de
    maior desconto, bastava uma coleta nova para o trabalho sumir da página
    — e é justamente essa regressão que estes testes travam.
    """

    databases = {"default", "promos"}

    def _produto(self, external_id, source="mercadolivre"):
        return SimpleNamespace(
            id=f"{source}:{external_id}",
            source=source,
            external_id=external_id,
            title=f"Produto {external_id}",
            image_url="https://exemplo/foto.jpg",
        )

    def _catalogo(self, presentes, source="mercadolivre", tambem_demo=False):
        """Um `Produto` de mentira que só conhece os ids informados.

        Reproduz o encadeamento que a view usa: `filter(...)` devolve tudo,
        `filter(...).exclude(source="demo")` devolve só o coletado.
        """

        def consulta(external_id):
            if external_id not in presentes:
                return SimpleNamespace(
                    first=lambda: None,
                    exclude=lambda **_: SimpleNamespace(
                        order_by=lambda *_a: SimpleNamespace(first=lambda: None)
                    ),
                )
            achado = self._produto(external_id, source=source)
            # Quem responde ao `.exclude(source="demo")`: existe só quando o
            # produto veio da coleta, ou quando o teste pede os dois.
            real = (
                self._produto(external_id)
                if tambem_demo
                else (achado if source != views.FONTE_DEMO else None)
            )
            return SimpleNamespace(
                first=lambda: achado,
                exclude=lambda **_: SimpleNamespace(
                    order_by=lambda *_a: SimpleNamespace(first=lambda: real)
                ),
            )

        falso = mock.Mock()
        falso.objects.filter.side_effect = consulta
        return falso

    def _item(self, external_id):
        return {
            "produto": self._produto(external_id),
            "external_id": external_id,
            "curva": montar_curva([100.0, 90.0, 80.0]),
            "dias": 3,
            "video": None,
        }

    def test_a_vitrine_sai_na_ordem_declarada(self):
        with (
            mock.patch.object(views, "Produto", self._catalogo(views.VITRINE_FIXA)),
            mock.patch.object(views, "_historico", return_value=[100.0, 90.0, 80.0]),
            mock.patch.object(
                views, "_video_do_produto", side_effect=lambda id: f"/static/{id}.mp4"
            ),
        ):
            itens = views._fixos()

        self.assertEqual(
            [item["external_id"] for item in itens], list(views.VITRINE_FIXA)
        )
        self.assertEqual(itens[0]["video"], f"/static/{views.VITRINE_FIXA[0]}.mp4")

    def test_produto_fora_do_catalogo_e_pulado_sem_quebrar(self):
        presentes = views.VITRINE_FIXA[1:]
        with (
            mock.patch.object(views, "Produto", self._catalogo(presentes)),
            mock.patch.object(views, "_historico", return_value=[100.0, 90.0]),
            mock.patch.object(views, "_video_do_produto", return_value=None),
        ):
            itens = views._fixos()

        self.assertEqual([item["external_id"] for item in itens], list(presentes))

    def test_sem_historico_suficiente_o_produto_nao_entra(self):
        """Um ponto só não vira curva, e card sem gráfico não prova nada."""
        with (
            mock.patch.object(views, "Produto", self._catalogo(views.VITRINE_FIXA)),
            mock.patch.object(views, "_historico", return_value=[100.0]),
            mock.patch.object(views, "_video_do_produto", return_value=None),
        ):
            self.assertEqual(views._fixos(), [])

    def test_home_mostra_os_fixos_e_ignora_o_desconto_do_dia(self):
        with (
            mock.patch.object(views, "_fixos", return_value=[self._item("FIXO")]),
            mock.patch.object(
                views,
                "_com_curva",
                return_value=[self._item("DINAMICO1"), self._item("DINAMICO2")],
            ),
        ):
            resposta = self.client.get(reverse("vitrine:home"))

        self.assertContains(resposta, "Produto FIXO")
        self.assertNotContains(resposta, "Produto DINAMICO2")

    def test_sem_os_fixos_a_vitrine_cai_para_a_lista_do_dia(self):
        """Seção vazia numa página que promete prova é pior do que outro produto."""
        with (
            mock.patch.object(views, "_fixos", return_value=[]),
            mock.patch.object(
                views,
                "_com_curva",
                return_value=[self._item("DINAMICO1"), self._item("DINAMICO2")],
            ),
        ):
            resposta = self.client.get(reverse("vitrine:home"))

        self.assertContains(resposta, "Produto DINAMICO2")

    def test_produto_de_demonstracao_e_marcado(self):
        """A etiqueta do card e as frases do detalhe dependem desta marca."""
        falso = self._catalogo(views.VITRINE_FIXA, source=views.FONTE_DEMO)
        with (
            mock.patch.object(views, "Produto", falso),
            mock.patch.object(views, "_historico", return_value=[100.0, 90.0]),
            mock.patch.object(views, "_video_do_produto", return_value=None),
        ):
            itens = views._fixos()

        self.assertTrue(all(item["demonstracao"] for item in itens))

    def test_card_de_demonstracao_nao_diz_prova(self):
        """Preço inventado não pode sair sob a palavra que o site vende."""
        item = self._item("DEMO")
        item["demonstracao"] = True
        with (
            mock.patch.object(views, "_fixos", return_value=[item]),
            mock.patch.object(views, "_com_curva", return_value=[]),
        ):
            resposta = self.client.get(reverse("vitrine:home"))

        self.assertContains(resposta, "Exemplo")
        self.assertNotContains(resposta, "Prova em")

    def test_quando_a_coleta_traz_o_mesmo_id_o_real_ganha_do_demo(self):
        """A watchlist tem 'smartwatch', 'notebook' e 'air fryer'. No dia em
        que a coleta trouxer um deles, a página tem que mostrar o preço real,
        não o semeado."""
        falso = self._catalogo(
            views.VITRINE_FIXA, source=views.FONTE_DEMO, tambem_demo=True
        )
        with (
            mock.patch.object(views, "Produto", falso),
            mock.patch.object(views, "_historico", return_value=[100.0, 90.0]),
            mock.patch.object(views, "_video_do_produto", return_value=None),
        ):
            itens = views._fixos()

        self.assertTrue(itens)
        self.assertFalse(any(item["demonstracao"] for item in itens))
        self.assertTrue(all(item["produto"].source == "mercadolivre" for item in itens))


class SincronizaONome(TestCase):
    """Renomear o grupo no WhatsApp não pode deixar a landing desatualizada.

    Aconteceu em 09/09/2026: o grupo virou "Comunidade do Desconto - Geral
    🇧🇷 03" e o site seguia anunciando "Comunidade do Desconto #3", o nome do
    dia do cadastro. Nada quebrou — quem identifica o grupo é o JID —, mas
    quem chegava pela landing via um nome que não existe mais.
    """

    def setUp(self):
        self.grupo = Grupo.objects.create(
            nome="Comunidade do Desconto #3",
            convite="https://chat.whatsapp.com/x",
            jid="120363429710613779@g.us",
            membros=3,
        )

    def responde(self, corpo):
        """Finge a Evolution devolvendo `corpo` em findGroupInfos."""
        resposta = mock.Mock(status_code=200)
        resposta.json.return_value = corpo
        cliente = mock.MagicMock()
        cliente.__enter__.return_value.get.return_value = resposta
        return cliente

    def sincronizar(self, corpo):
        with mock.patch.dict(os.environ, {"EVOLUTION_API_KEY": "x"}):
            with mock.patch("httpx.Client", return_value=self.responde(corpo)):
                call_command("sincronizar_grupos", stdout=StringIO())
        self.grupo.refresh_from_db()

    def test_o_nome_novo_e_gravado(self):
        self.sincronizar(
            {"size": 141, "subject": "Comunidade do Desconto - Geral 🇧🇷 03"}
        )

        self.assertEqual(self.grupo.nome, "Comunidade do Desconto - Geral 🇧🇷 03")
        self.assertEqual(self.grupo.membros, 141)

    def test_sem_nome_na_resposta_o_antigo_fica(self):
        """Versão da Evolution que não manda `subject` não pode apagar o nome."""
        self.sincronizar({"size": 141})

        self.assertEqual(self.grupo.nome, "Comunidade do Desconto #3")
        self.assertEqual(self.grupo.membros, 141)

    def test_nome_vazio_nao_apaga_o_atual(self):
        self.sincronizar({"size": 141, "subject": "   "})

        self.assertEqual(self.grupo.nome, "Comunidade do Desconto #3")

    def test_membros_ilegiveis_nao_gravam_nada(self):
        """Falha de leitura mantém o estado, em vez de zerar a ocupação e
        anunciar vaga que não existe."""
        self.sincronizar({"subject": "Nome Novo"})

        self.assertEqual(self.grupo.membros, 3)
        self.assertEqual(self.grupo.nome, "Comunidade do Desconto #3")

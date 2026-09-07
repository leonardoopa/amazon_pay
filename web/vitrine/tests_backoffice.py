"""Backoffice: a tela de oferta da Amazon e o limite de login no admin.

A tela existe porque a Creators API da Amazon ainda não liberou — ela pede
vendas qualificadas na conta Associates — e raspar não é alternativa: o
Operating Agreement proíbe ferramenta de extração e exige que preço exibido
venha da API. Então o preço é digitado, e o resto é automático.

O ponto de arquitetura que os testes travam: **a tela não escreve no banco do
bot**. Ela chama o `POST /amazon-add` da API. O roteador em
`comunidade/routers.py` proíbe essa escrita porque um `.save()` acidental
corromperia o histórico de preço, e a regra não abre exceção para o dono.

O limite de login existe porque o /admin/ está exposto na internet e a senha é
a única barreira. Sem ele, uma lista de senhas comuns roda a noite inteira sem
custo para quem tenta e sem sintoma para quem hospeda.
"""

from __future__ import annotations

from unittest import mock

import httpx
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

RESPOSTA_OK = {
    "post_id": 42,
    "asin": "B07DVJC66X",
    "link": "https://www.amazon.com.br/dp/B07DVJC66X?tag=comunidadedodesconto-20",
    "desconto_pct": 30.8,
    "texto": "Creatina Max Titanium\n\nDe R$ 129,90 por *R$ 89,90*",
}

FORMULARIO = {
    "produto": "https://www.amazon.com.br/dp/B07DVJC66X",
    "titulo": "Creatina Monohidratada 300g Max Titanium",
    "preco": "89.90",
    "de": "129.90",
    "imagem": "",
}


def resposta_falsa(status_code=201, corpo=None):
    return httpx.Response(
        status_code,
        json=corpo if corpo is not None else RESPOSTA_OK,
        request=httpx.Request("POST", "http://amazon_pay:8000/amazon-add"),
    )


class TelaDaAmazon(TestCase):
    def setUp(self):
        self.url = reverse("vitrine:amazon_add")
        self.staff = get_user_model().objects.create_user(
            "dono", password="uma-senha-longa-de-teste", is_staff=True, is_superuser=True
        )

    # ---------- quem pode entrar ----------

    def test_visitante_anonimo_nao_entra(self):
        """A tela enfileira post no grupo. Aberta, qualquer um publica."""
        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn("login", resposta["Location"])

    def test_usuario_comum_sem_staff_nao_entra(self):
        get_user_model().objects.create_user("comum", password="outra-senha-longa")
        self.client.login(username="comum", password="outra-senha-longa")

        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 302)

    def test_staff_entra(self):
        self.client.force_login(self.staff)

        self.assertEqual(self.client.get(self.url).status_code, 200)

    # ---------- o caminho feliz ----------

    def test_envia_para_a_api_do_bot(self):
        self.client.force_login(self.staff)

        with mock.patch("httpx.post", return_value=resposta_falsa()) as chamada:
            resposta = self.client.post(self.url, FORMULARIO)

        self.assertEqual(resposta.status_code, 200)
        _, kwargs = chamada.call_args
        self.assertEqual(kwargs["json"]["produto"], FORMULARIO["produto"])
        self.assertEqual(kwargs["json"]["preco"], 89.90)
        self.assertEqual(kwargs["json"]["de"], 129.90)

    def test_manda_a_chave_da_api(self):
        """Sem o header o bot responde 401, e a tela não teria como saber por
        quê — o 401 do FastAPI não diz que falta configuração."""
        self.client.force_login(self.staff)

        with override_settings(BOT_API_SECRET="chave-de-teste"):
            with mock.patch("httpx.post", return_value=resposta_falsa()) as chamada:
                self.client.post(self.url, FORMULARIO)

        _, kwargs = chamada.call_args
        self.assertEqual(kwargs["headers"]["X-API-Key"], "chave-de-teste")

    def test_mostra_o_texto_que_vai_sair(self):
        self.client.force_login(self.staff)

        with mock.patch("httpx.post", return_value=resposta_falsa()):
            resposta = self.client.post(self.url, FORMULARIO)

        self.assertContains(resposta, "De R$ 129,90")

    def test_confirma_o_post_enfileirado(self):
        self.client.force_login(self.staff)

        with mock.patch("httpx.post", return_value=resposta_falsa()):
            resposta = self.client.post(self.url, FORMULARIO, follow=True)

        self.assertContains(resposta, "42")

    # ---------- a tela nunca escreve no banco do bot ----------

    def test_nao_toca_no_banco_do_bot(self):
        """O roteador proíbe, e a tela respeita: tudo passa pela API."""
        self.client.force_login(self.staff)

        with mock.patch("httpx.post", return_value=resposta_falsa()) as chamada:
            self.client.post(self.url, FORMULARIO)

        self.assertTrue(chamada.called)
        args, _ = chamada.call_args
        self.assertTrue(args[0].endswith("/amazon-add"))

    # ---------- erro ----------

    def test_bot_fora_do_ar_vira_mensagem(self):
        """Container reiniciando não pode virar 500 na cara de quem digitou."""
        self.client.force_login(self.staff)

        with mock.patch("httpx.post", side_effect=httpx.ConnectError("recusado")):
            resposta = self.client.post(self.url, FORMULARIO, follow=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "Não consegui falar com o bot")

    def test_recusa_do_bot_mostra_o_motivo(self):
        """O 422 traz a mensagem escrita para quem digitou; jogá-la fora
        deixaria a pessoa sem saber o que consertar."""
        self.client.force_login(self.staff)
        erro = resposta_falsa(422, {"detail": "Nao achei o ASIN"})

        with mock.patch("httpx.post", return_value=erro):
            resposta = self.client.post(self.url, FORMULARIO, follow=True)

        self.assertContains(resposta, "Nao achei o ASIN")

    def test_preco_maior_que_o_de_nao_chega_na_api(self):
        """Validação local poupa a ida até o bot para um erro óbvio."""
        self.client.force_login(self.staff)
        invalido = {**FORMULARIO, "preco": "199.90", "de": "129.90"}

        with mock.patch("httpx.post") as chamada:
            resposta = self.client.post(self.url, invalido)

        self.assertFalse(chamada.called)
        self.assertContains(resposta, "menor que o de")

    def test_campo_vazio_nao_chega_na_api(self):
        self.client.force_login(self.staff)

        with mock.patch("httpx.post") as chamada:
            self.client.post(self.url, {**FORMULARIO, "produto": ""})

        self.assertFalse(chamada.called)


@override_settings(LOGIN_MAX_TENTATIVAS=3, LOGIN_BLOQUEIO_MINUTOS=15)
class LimiteDeLogin(TestCase):
    def setUp(self):
        cache.clear()
        self.url = "/admin/login/"
        get_user_model().objects.create_user(
            "dono", password="uma-senha-longa-de-teste", is_staff=True, is_superuser=True
        )

    def errar(self, cliente=None):
        return (cliente or self.client).post(
            self.url, {"username": "dono", "password": "errada"}
        )

    def test_bloqueia_depois_do_limite(self):
        for _ in range(3):
            self.errar()

        self.assertEqual(self.errar().status_code, 429)

    def test_antes_do_limite_nao_bloqueia(self):
        for _ in range(2):
            self.assertNotEqual(self.errar().status_code, 429)

    def test_diz_quanto_esperar(self):
        """429 com Retry-After faz o cliente honesto esperar em vez de
        insistir."""
        for _ in range(3):
            self.errar()

        self.assertEqual(self.errar()["Retry-After"], "900")

    def test_login_certo_zera_a_contagem(self):
        """Quem erra duas vezes e acerta na terceira não pode ficar a um erro
        do bloqueio pelo resto da janela."""
        self.errar()
        self.errar()

        self.client.post(
            self.url, {"username": "dono", "password": "uma-senha-longa-de-teste"}
        )

        self.client.logout()
        self.assertNotEqual(self.errar().status_code, 429)

    def test_outro_ip_nao_e_afetado(self):
        """A contagem é por IP: bloquear todo mundo por causa de um atacante
        trancaria o dono para fora."""
        for _ in range(4):
            self.errar()

        outro = Client(HTTP_X_FORWARDED_FOR="203.0.113.9")

        self.assertNotEqual(self.errar(outro).status_code, 429)

    def test_so_o_primeiro_valor_do_forwarded_for_conta(self):
        """Os seguintes são escritos pelo cliente. Confiar neles deixaria
        qualquer um trocar de identidade a cada tentativa."""
        atacante = Client(HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.1")
        for _ in range(3):
            self.errar(atacante)

        disfarcado = Client(HTTP_X_FORWARDED_FOR="203.0.113.9, 10.0.0.99")

        self.assertEqual(self.errar(disfarcado).status_code, 429)

    def test_get_da_pagina_de_login_nao_conta(self):
        """Abrir a tela não é tentativa. Contar o GET trancaria quem só
        recarregou a página."""
        for _ in range(10):
            self.client.get(self.url)

        self.assertNotEqual(self.errar().status_code, 429)

    def test_outras_rotas_nao_sao_afetadas(self):
        """Bloquear o login não pode derrubar o site para quem só navega.

        A rota do teste é o robots, e não a home, de propósito: a home lê o
        banco do bot, e declarar dois SQLite no `databases` do TestCase põe os
        dois na mesma transação e trava o `auth_user`.
        """
        for _ in range(5):
            self.errar()

        self.assertEqual(self.client.get(reverse("vitrine:robots")).status_code, 200)

    @override_settings(LOGIN_MAX_TENTATIVAS=0)
    def test_zero_desliga_o_limite(self):
        for _ in range(10):
            self.assertNotEqual(self.errar().status_code, 429)


class CacheCompartilhado(TestCase):
    """O cache precisa valer para todos os workers, não para um processo.

    Medido em produção em 07/09/2026, com LocMemCache e o gunicorn em vários
    workers: 30 tentativas seguidas de login erradas no mesmo IP passaram
    todas, nenhuma 429. A lógica do middleware está certa — testada isolada,
    ela bloqueia na nona — mas cada worker tinha o seu contador e nenhum
    chegou ao limite.

    Os testes de `LimiteDeLogin` não pegam isso: o test client do Django roda
    tudo num processo só, então o contador sempre soma. Por isso a garantia
    aqui é sobre a CONFIGURAÇÃO, que é onde estava o defeito.
    """

    def test_o_cache_nao_e_local_ao_processo(self):
        from django.conf import settings

        backend = settings.CACHES["default"]["BACKEND"]

        self.assertNotIn("locmem", backend.lower())

    def test_o_cache_persiste_fora_do_processo(self):
        """Sem isto, um contador de tentativas some a cada reinício de worker
        e o limite vira decorativo."""
        from django.conf import settings

        backend = settings.CACHES["default"]["BACKEND"]

        self.assertTrue(
            any(k in backend.lower() for k in ("filebased", "db", "memcached", "redis")),
            f"Backend {backend} não é compartilhado entre workers.",
        )

    def test_o_diretorio_do_cache_fica_no_volume_persistente(self):
        """`data/` é o único diretório que sobrevive a `up --build`."""
        from django.conf import settings

        local = settings.CACHES["default"].get("LOCATION", "")

        self.assertIn("data", str(local))

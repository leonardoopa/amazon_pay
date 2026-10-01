"""Entrega direto no grupo do WhatsApp via Evolution API (nao oficial).

O que isso compra: o post cai no grupo sozinho, sem voce encaminhar. A Cloud
API oficial nao faz isso -- a Groups API da Meta so alcanca grupo criado pelo
proprio bot e com teto de 8 participantes, o que nao e grupo de ofertas.

O que isso custa: a Evolution roda em cima do Baileys, que e um cliente
WhatsApp Web nao oficial. Isso viola os Termos da Meta e o numero pode ser
banido -- por isso a recomendacao e parear um chip secundario, nunca o seu
numero pessoal. O risco cresce com volume e com denuncia de membro do grupo,
entao MAX_OFFERS_PER_RUN continua sendo a trava que importa.

A interface e a mesma do backend oficial (`send_post`), entao trocar de volta
pra Cloud API e mudar DELIVERY_BACKEND no .env -- o pipeline nao sabe qual dos
dois esta rodando.
"""

from __future__ import annotations

import base64
import logging
import time

import httpx

log = logging.getLogger("promo")

# Mesmo teto da Cloud API. O WhatsApp corta legenda de imagem por volta disso
# nos dois caminhos, entao o limite nao e da API oficial -- e do app.
CAPTION_LIMIT = 1024

# Segundos de espera DEPOIS de cada tentativa de mandar a foto. O tamanho da
# lista e o numero de tentativas; o ultimo valor e zero porque nao ha o que
# esperar depois da ultima.
#
# Existe por causa de `getaddrinfo EAI_AGAIN`, que e o resolver do container da
# Evolution falhando por alguns segundos. Retry imediato nao ajuda nesse caso:
# ele repete a pergunta para o mesmo resolver ainda indisponivel.
ESPERAS_DA_MIDIA = (2.0, 6.0, 0.0)

# Quanto esperar ao baixar a foto da CDN do ML por conta propria. As imagens do
# ML sao de dezenas de KB; o que este numero cobre e a CDN lenta, nao arquivo
# grande.
MEDIA_TIMEOUT = 20.0

# Identificacao ao baixar a foto por conta propria. A CDN do Pelando fica atras
# de um desafio do Cloudflare que devolve 403 para o User-Agent padrao do httpx
# e deixa passar um que diga quem somos -- medido em 30/09/2026. A CDN do ML
# aceita qualquer um.
MEDIA_USER_AGENT = (
    "promo-bot/1.0 (leitor de ofertas; contato via comunidadedodesconto.com.br)"
)

# Listar grupos e a chamada mais lenta da API: ela espera a sincronizacao do
# Baileys com o celular. Numa conta com ~170 grupos passa de 30s.
GROUPS_TIMEOUT = 120.0


class NotConnected(RuntimeError):
    """A instancia existe mas nao esta pareada com nenhum numero."""


class Evolution:
    def __init__(self, config) -> None:  # config: EvolutionConfig
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            timeout=30.0,
            headers={"apikey": config.api_key},
        )

    # ---------- Instancia ----------

    def create_instance(self) -> dict:
        """Cria a instancia. Idempotente do nosso lado: ja existir nao e erro."""
        response = self._client.post(
            "/instance/create",
            json={
                "instanceName": self.config.instance,
                "qrcode": True,
                # Baileys e o unico integration que fala com grupo comum.
                "integration": "WHATSAPP-BAILEYS",
            },
        )
        if response.status_code == 403 and "already in use" in response.text:
            return {"status": "ja existia"}
        response.raise_for_status()
        return response.json()

    def connect(self, number: str | None = None) -> dict:
        """Inicia o pareamento.

        Com `number` o WhatsApp emite um codigo de 8 caracteres pra digitar no
        celular; sem ele, sobra o QR em base64. O codigo e melhor aqui porque
        o pareamento acontece pelo terminal -- pedir pra alguem apontar a
        camera pra um QR que so existe dentro de um JSON nao ajuda ninguem.

        O numero vai em E.164 sem "+" (ex.: 5581996257747).
        """
        response = self._client.get(
            f"/instance/connect/{self.config.instance}",
            params={"number": number} if number else None,
        )
        response.raise_for_status()
        return response.json()

    def logout(self) -> dict:
        """Desconecta o numero, preservando a instancia e a config."""
        response = self._client.delete(f"/instance/logout/{self.config.instance}")
        response.raise_for_status()
        return response.json()

    def state(self) -> str:
        """'open' = pareado e pronto. 'close'/'connecting' = precisa do QR."""
        response = self._client.get(f"/instance/connectionState/{self.config.instance}")
        response.raise_for_status()
        body = response.json()
        return (body.get("instance") or body).get("state", "desconhecido")

    def groups(self) -> list[dict]:
        """Grupos do numero pareado: (id, subject, size).

        getParticipants=false porque a lista de membros nao interessa e deixa a
        resposta enorme em grupo grande.

        Timeout proprio, bem maior que o padrao: logo depois de parear o
        Baileys ainda esta sincronizando a lista com o celular, e numa conta
        com centenas de grupos essa chamada passa facil de 30s. Cair no timeout
        padrao aqui devolvia um traceback de httpx que nao dizia nada sobre a
        causa real -- e a causa e "espere a sincronizacao terminar".
        """
        try:
            response = self._client.get(
                f"/group/fetchAllGroups/{self.config.instance}",
                params={"getParticipants": "false"},
                timeout=GROUPS_TIMEOUT,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"A Evolution nao devolveu os grupos em {GROUPS_TIMEOUT:.0f}s. "
                "Logo apos parear ela ainda sincroniza a lista com o celular; "
                "espere um minuto e rode de novo."
            ) from exc
        response.raise_for_status()
        body = response.json()
        # A versao muda entre devolver a lista crua e embrulhar em {"groups": []}.
        return body if isinstance(body, list) else body.get("groups", [])

    # ---------- Envio ----------

    def _send(self, path: str, payload: dict) -> dict:
        response = self._client.post(f"{path}/{self.config.instance}", json=payload)
        if response.status_code >= 400:
            # O erro mais comum e a instancia ter caido (celular sem rede,
            # sessao derrubada). Sem essa distincao vira "400 Bad Request" e
            # ninguem sabe que era so reparear.
            if "not connected" in response.text.lower() or response.status_code == 404:
                raise NotConnected(
                    f"Instancia '{self.config.instance}' nao esta conectada. "
                    "Rode `promo wa-connect` e leia o QR de novo."
                )
            raise RuntimeError(
                f"Evolution recusou o envio ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    def send_text(self, text: str, to: str | None = None) -> dict:
        return self._send(
            "/message/sendText",
            {
                "number": to or self.config.group_jid,
                "text": text,
                # linkPreview monta o card do produto, igual ao preview_url da
                # Cloud API -- e o que faz o post parecer post e nao spam.
                "linkPreview": True,
            },
        )

    def send_image(self, image_url: str, caption: str, to: str | None = None) -> dict:
        """Imagem + legenda numa mensagem so.

        A Evolution baixa a URL pelo lado dela e sobe como midia, entao o
        endereco precisa ser publico -- o da CDN do ML e.
        """
        return self._send(
            "/message/sendMedia",
            {
                "number": to or self.config.group_jid,
                "mediatype": "image",
                "media": image_url,
                "caption": caption[:CAPTION_LIMIT],
            },
        )

    def send_image_bytes(
        self, image_url: str, caption: str, to: str | None = None
    ) -> dict:
        """A mesma imagem, mas baixada por NOS e enviada em base64.

        Existe porque o resolver do container da Evolution cai. Em 09/09/2026 a
        falha era `getaddrinfo EAI_AGAIN http2.mlstatic.com`, reproduzida de
        dentro do container: o DNS embutido do Docker (127.0.0.11) parou de
        responder ali, e dois posts perderam a foto em tres minutos.

        Quem resolve o nome aqui somos nos, e o nosso container resolve DNS o
        tempo todo -- e ele que baixa as paginas do ML. Isso tira a Evolution
        do caminho da rede sem mexer no container que carrega a sessao pareada
        do WhatsApp.

        Fica como segunda tentativa, e nao como padrao: mandar a URL nao gasta
        banda nossa nem memoria com a imagem inteira, e funciona na maior parte
        das vezes.
        """
        with httpx.Client(
            timeout=MEDIA_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": MEDIA_USER_AGENT},
        ) as c:
            resposta = c.get(image_url)
            resposta.raise_for_status()
            bruto = resposta.content

        return self._send(
            "/message/sendMedia",
            {
                "number": to or self.config.group_jid,
                "mediatype": "image",
                "media": base64.b64encode(bruto).decode("ascii"),
                "caption": caption[:CAPTION_LIMIT],
            },
        )

    def send_post(
        self, text: str, image_url: str | None = None, to: str | None = None
    ) -> dict:
        """Mesma assinatura do backend oficial: imagem quando da, texto quando nao.

        `to` e o JID do grupo de destino. Vazio ou None usa o do .env, que era
        o unico destino ate 09/09/2026 -- e continua sendo o do Geral.

        Repare no que NAO tem aqui: janela de 24h. Ela e uma regra da Cloud API
        da Meta, e a Evolution nao passa por ela -- entao o pipeline nunca vai
        ver WindowClosed neste backend, e a fila so acumula por erro de rede ou
        instancia caida.
        """
        if image_url and len(text) <= CAPTION_LIMIT:
            # Tres tentativas, com espera crescente entre elas.
            #
            # Quem baixa a foto e a Evolution, do lado dela, e a falha vista em
            # producao foi `getaddrinfo EAI_AGAIN http2.mlstatic.com`: DNS
            # temporariamente indisponivel dentro do container. Nao ha nada de
            # errado com a URL.
            #
            # A espera importa mais que o numero de tentativas. Em 09/09/2026 o
            # retry existia mas era imediato: as duas tentativas cairam em 5
            # segundos, o resolver ainda nao tinha se recuperado, e o post saiu
            # sem foto. Com 2 s e 6 s a segunda janela e outra.
            #
            # Post de oferta sem imagem no WhatsApp passa despercebido na
            # rolagem, que e o mesmo que nao ter sido enviado.
            for tentativa, espera in enumerate(ESPERAS_DA_MIDIA, start=1):
                try:
                    return self.send_image(image_url, text, to)
                except NotConnected:
                    raise  # instancia caida nao e problema da imagem
                except Exception as exc:  # noqa: BLE001 - recusa da midia vira texto
                    log.warning(
                        "Envio da imagem falhou (tentativa %d/%d): %s",
                        tentativa,
                        len(ESPERAS_DA_MIDIA),
                        exc,
                    )
                    if espera:
                        time.sleep(espera)

            # Ultima carta: baixar a foto aqui e mandar os bytes.
            #
            # Quando a falha e o DNS da Evolution, repetir a mesma chamada nao
            # tem como dar certo -- ela vai continuar sem resolver o nome. Este
            # caminho troca QUEM busca a imagem, e por isso funciona onde o
            # retry nao funcionava.
            try:
                return self.send_image_bytes(image_url, text, to)
            except NotConnected:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Envio da imagem em base64 tambem falhou: %s", exc)

            log.warning(
                "Post sai sem imagem: a Evolution recusou a midia %d vezes, e o "
                "envio direto dos bytes tambem.",
                len(ESPERAS_DA_MIDIA),
            )
        return self.send_text(text, to)

"""Entrega os posts prontos no SEU WhatsApp via Cloud API oficial.

Por que nao postar direto no grupo: a Cloud API nao envia para grupos comuns,
e as libs nao-oficiais (Baileys, whatsapp-web.js) violam os termos e derrubam
o numero. Entao o bot te manda o texto pronto no privado e voce encaminha
pro grupo -- e de quebra voce revisa antes de publicar.

Janela de 24h: mensagem de texto livre so sai se voce falou com o numero do
bot nas ultimas 24h. Fora disso a Meta exige template aprovado -- por isso o
fallback manda um template curto pedindo que voce responda qualquer coisa,
o que reabre a janela.
"""

from __future__ import annotations
import httpx

GRAPH_VERSION = "v21.0"
# Erros da Meta que significam "janela de 24h fechada".
WINDOW_CLOSED_CODES = {131047, 131051}
# Teto da legenda de imagem na Cloud API.
CAPTION_LIMIT = 1024


class WindowClosed(RuntimeError):
    """A janela de atendimento de 24h expirou; so template passa."""


class WhatsApp:
    def __init__(self, config) -> None:  # config: WhatsAppConfig
        self.config = config
        self._client = httpx.Client(timeout=20.0)
        self._url = (
            f"https://graph.facebook.com/{GRAPH_VERSION}/"
            f"{config.phone_number_id}/messages"
        )

    def _post(self, payload: dict) -> dict:
        response = self._client.post(
            self._url,
            json=payload,
            headers={"Authorization": f"Bearer {self.config.token}"},
        )
        if response.status_code >= 400:
            body = response.json() if response.text else {}
            code = (body.get("error") or {}).get("code")
            if code in WINDOW_CLOSED_CODES:
                raise WindowClosed(str(body.get("error")))
            response.raise_for_status()
        return response.json()

    def send_text(self, text: str) -> dict:
        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": self.config.to,
                "type": "text",
                # preview_url=True deixa o WhatsApp montar o card do produto
                "text": {"body": text, "preview_url": True},
            }
        )

    def send_image(self, image_url: str, caption: str) -> dict:
        """Imagem + texto numa mensagem so.

        Uma mensagem em vez de duas importa aqui: o post e pra ser
        encaminhado pro grupo, e encaminhar duas mensagens separadas quebra
        a associacao entre a foto e o preco.

        A Meta baixa a imagem da URL pelo lado dela, entao o endereco precisa
        ser publico -- o da CDN do ML e. Legenda tem teto de 1024 caracteres.
        """
        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": self.config.to,
                "type": "image",
                "image": {"link": image_url, "caption": caption[:CAPTION_LIMIT]},
            }
        )

    def send_post(self, text: str, image_url: str | None = None) -> dict:
        """Manda como imagem quando da, e cai pra texto quando nao da.

        Motivos pra cair: sem imagem no anuncio, legenda longa demais, ou a
        Meta recusando a URL. Nenhum deles justifica perder a oferta.
        """
        if image_url and len(text) <= CAPTION_LIMIT:
            try:
                return self.send_image(image_url, text)
            except WindowClosed:
                raise  # a janela fechada nao e problema da imagem
            except Exception:  # noqa: BLE001 - qualquer recusa da imagem vira texto
                pass
        return self.send_text(text)

    def send_ping_template(self, pending: int) -> dict:
        """Template utility pra reabrir a janela quando ela fechou.

        O template precisa estar aprovado no WhatsApp Manager com 1 variavel
        no corpo, ex.: "Voce tem {{1}} ofertas novas. Responda OK pra receber."
        Variavel de template nao aceita quebra de linha -- por isso o texto
        completo so vai depois, como mensagem livre.
        """
        return self._post(
            {
                "messaging_product": "whatsapp",
                "to": self.config.to,
                "type": "template",
                "template": {
                    "name": self.config.ping_template,
                    "language": {"code": self.config.template_lang},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": str(pending)}],
                        }
                    ],
                },
            }
        )

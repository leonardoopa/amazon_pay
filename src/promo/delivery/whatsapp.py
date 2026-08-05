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
                # preview_url=False evita o card grande do link roubando a tela
                "text": {"body": text, "preview_url": True},
            }
        )

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

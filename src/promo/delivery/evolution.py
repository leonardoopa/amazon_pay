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

import httpx

# Mesmo teto da Cloud API. O WhatsApp corta legenda de imagem por volta disso
# nos dois caminhos, entao o limite nao e da API oficial -- e do app.
CAPTION_LIMIT = 1024


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

    def connect(self) -> dict:
        """Devolve o QR code pra parear o numero (ou o estado, se ja pareado)."""
        response = self._client.get(f"/instance/connect/{self.config.instance}")
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
        """
        response = self._client.get(
            f"/group/fetchAllGroups/{self.config.instance}",
            params={"getParticipants": "false"},
        )
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

    def send_post(self, text: str, image_url: str | None = None) -> dict:
        """Mesma assinatura do backend oficial: imagem quando da, texto quando nao.

        Repare no que NAO tem aqui: janela de 24h. Ela e uma regra da Cloud API
        da Meta, e a Evolution nao passa por ela -- entao o pipeline nunca vai
        ver WindowClosed neste backend, e a fila so acumula por erro de rede ou
        instancia caida.
        """
        if image_url and len(text) <= CAPTION_LIMIT:
            try:
                return self.send_image(image_url, text)
            except NotConnected:
                raise  # instancia caida nao e problema da imagem
            except Exception:  # noqa: BLE001 - qualquer recusa da midia vira texto
                pass
        return self.send_text(text)

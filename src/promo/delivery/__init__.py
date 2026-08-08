"""Backends de entrega. Os dois expoem `send_post(text, image_url)`."""

from ..config import EvolutionConfig, WhatsAppConfig, delivery_backend
from .evolution import Evolution, NotConnected
from .whatsapp import WhatsApp, WindowClosed

__all__ = ["Evolution", "NotConnected", "WhatsApp", "WindowClosed", "build_delivery"]


def build_delivery():
    """Instancia o backend escolhido em DELIVERY_BACKEND.

    O pipeline nao pergunta qual e: ele so chama send_post e trata
    WindowClosed/NotConnected, que sao especificos de cada um mas nunca
    aparecem juntos.
    """
    escolhido = delivery_backend()
    if escolhido == "evolution":
        return Evolution(EvolutionConfig.load())
    if escolhido == "cloud":
        return WhatsApp(WhatsAppConfig.load())
    raise ValueError(
        f"DELIVERY_BACKEND invalido: {escolhido!r}. Use 'evolution' ou 'cloud'."
    )

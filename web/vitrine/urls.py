from __future__ import annotations

from django.urls import path

from . import views

app_name = "vitrine"

urlpatterns = [
    path("", views.home, name="home"),
    path("oferta/<str:external_id>/", views.oferta, name="oferta"),
    # Passo intermediário até o WhatsApp: registra o clique e redireciona.
    # `?de=` diz qual chamada da página foi clicada.
    path("entrar/", views.entrar, name="entrar"),
    path("inscrever/", views.inscrever, name="inscrever"),
    # Backoffice: exige staff. NAO pode morar sob /admin/ -- o
    # `admin.site.urls` casa esse prefixo inteiro no urls.py raiz e devolve
    # 404 para o que ele nao conhece, antes de a vitrine ser consultada.
    path("backoffice/amazon/", views.amazon_add, name="amazon_add"),
    path("robots.txt", views.robots, name="robots"),
    path("sitemap.xml", views.sitemap, name="sitemap"),
    path("saude/", views.saude, name="saude"),
]

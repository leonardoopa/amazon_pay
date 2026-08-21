from __future__ import annotations

from django.urls import path

from . import views

app_name = "vitrine"

urlpatterns = [
    path("", views.home, name="home"),
    path("oferta/<str:external_id>/", views.oferta, name="oferta"),
    path("inscrever/", views.inscrever, name="inscrever"),
    path("saude/", views.saude, name="saude"),
]

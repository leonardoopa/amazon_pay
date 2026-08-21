from __future__ import annotations

from django import forms

from .models import Inscrito


class InscricaoForm(forms.ModelForm):
    class Meta:
        model = Inscrito
        fields = ["email"]
        widgets = {
            "email": forms.EmailInput(
                attrs={
                    "placeholder": "seu@email.com",
                    "autocomplete": "email",
                    "required": True,
                }
            )
        }

from __future__ import annotations

from django import forms

from .models import Inscrito

# Nome de campo que parece útil para um robô e não existe para o visitante.
# Robô de formulário preenche tudo que encontra; quem usa o site nem vê o
# campo. Um `name="url"` num formulário de e-mail é isca clássica justamente
# porque robô de spam quer plantar link.
CAMPO_ISCA = "url"


class InscricaoForm(forms.ModelForm):
    """Captura de e-mail com armadilha para robô.

    A alternativa seria CAPTCHA, que cobra do visitante o custo do problema
    que ele não criou — e num formulário de uma linha, com um único campo, o
    abandono que ele causa é maior do que o spam que evita.
    """

    # `required=False` porque o certo é ficar vazio. A validação abaixo é que
    # recusa quando vem preenchido.
    url = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                # `tabindex="-1"` e `aria-hidden` mantêm o campo fora do
                # caminho de quem navega por teclado ou leitor de tela: para
                # essas pessoas o campo não existe, como para qualquer
                # visitante.
                "tabindex": "-1",
                "aria-hidden": "true",
                "autocomplete": "off",
                "class": "isca",
            }
        ),
        label="",
    )

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

    def clean(self):
        dados = super().clean()
        if dados.get(CAMPO_ISCA):
            # Mensagem genérica de propósito: dizer "você caiu na armadilha"
            # ensina o robô a não cair na próxima.
            raise forms.ValidationError("Não foi possível concluir o cadastro.")
        return dados

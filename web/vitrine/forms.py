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


class OfertaAmazonForm(forms.Form):
    """Uma oferta da Amazon escolhida a mão, para enfileirar pelo admin.

    O preço é digitado porque a Creators API ainda não liberou — ela pede
    vendas qualificadas na conta Associates — e raspar a Amazon não é
    alternativa: o Operating Agreement do Associates proíbe "any use of data
    mining, robots, or similar data gathering and extraction tools" e exige
    que preço exibido venha da API.

    Este formulário não escreve no banco do bot. Ele chama o `POST /amazon-add`
    da API, que é quem grava — o roteador em `comunidade/routers.py` proíbe o
    site de escrever lá, e a regra vale também para o que o dono digita.
    """

    produto = forms.CharField(
        label="Link ou ASIN",
        help_text="Cole a URL do produto na Amazon. Pode ser a longa, com os "
        "rastreadores — eles são descartados.",
        widget=forms.TextInput(
            attrs={"placeholder": "https://www.amazon.com.br/dp/B07DVJC66X", "size": 60}
        ),
    )
    titulo = forms.CharField(
        label="Título",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Creatina Monohidratada 300g Max Titanium",
                "size": 60,
            }
        ),
    )
    preco = forms.DecimalField(
        label="Preço de agora (R$)", min_value=0.01, decimal_places=2
    )
    de = forms.DecimalField(
        label="Preço antes (R$)",
        min_value=0.01,
        decimal_places=2,
        help_text="O riscado que a Amazon mostra.",
    )
    imagem = forms.URLField(
        label="Imagem (opcional)",
        required=False,
        help_text="Vazio usa a foto oficial do produto, pelo ASIN.",
        widget=forms.URLInput(attrs={"size": 60}),
    )
    sem_ia = forms.BooleanField(
        label="Sem IA",
        required=False,
        help_text="Usa o texto padrão em vez de chamar o Gemini.",
    )

    def clean(self):
        dados = super().clean()
        preco, de = dados.get("preco"), dados.get("de")
        # Só compara quando os dois passaram na validação de campo — senão a
        # mensagem "preço menor que de" apareceria junto com "campo obrigatório"
        # e esconderia o erro que a pessoa precisa consertar primeiro.
        if preco is not None and de is not None and preco >= de:
            raise forms.ValidationError(
                f"O preço (R$ {preco}) precisa ser menor que o de (R$ {de}) — "
                "sem queda não há oferta."
            )
        return dados

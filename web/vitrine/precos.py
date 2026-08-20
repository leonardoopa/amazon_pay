"""Curva de preço em SVG, desenhada no servidor.

É o diferencial do site: em vez de repetir o "de R$X por R$Y" da loja, mostra
a série real dos últimos dias e onde o preço de hoje cai em relação à mediana.

SVG inline em vez de biblioteca de gráfico no navegador porque a página é
estática, o dado é pequeno e assim o gráfico aparece no primeiro paint — sem
JavaScript, sem layout shift, e funciona no preview de link do WhatsApp.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median


@dataclass(frozen=True)
class Curva:
    """Uma série de preços pronta para desenhar.

    As coordenadas saem daqui já como string com ponto decimal. O template
    roda sob locale pt-BR, que renderiza float com vírgula — e `y1="18,0"`
    é atributo inválido em SVG, o elemento simplesmente não desenha.
    """

    caminho: str  # `d` da linha
    area: str  # `d` da área sob a linha
    pontos: list[tuple[float, float]]
    minimo: float
    maximo: float
    mediana: float
    atual: float
    largura: int
    altura: int
    # Coordenadas como texto, prontas para ir direto no atributo.
    y_mediana: str
    y_rotulo_mediana: str
    x_inicio: str
    x_fim: str
    x_atual: str
    y_atual: str

    @property
    def desconto_pct(self) -> float:
        if self.mediana <= 0:
            return 0.0
        return max(0.0, (self.mediana - self.atual) / self.mediana * 100)

    @property
    def menor_da_serie(self) -> bool:
        return self.atual <= self.minimo


def montar_curva(
    precos: list[float], largura: int = 720, altura: int = 220, margem: int = 18
) -> Curva | None:
    """Converte preços (ordem cronológica) em coordenadas de SVG.

    Devolve None com menos de dois pontos: uma linha de um ponto só sugere
    tendência que não existe, e sugerir isso num site de oferta é enganoso.
    """
    if len(precos) < 2:
        return None

    minimo, maximo = min(precos), max(precos)
    # Preço estável vira faixa artificial, senão a linha ficaria colada na
    # borda e pareceria uma queda que não houve.
    if maximo - minimo < 0.01:
        minimo, maximo = minimo * 0.97, maximo * 1.03

    util_x = largura - margem * 2
    util_y = altura - margem * 2
    passo = util_x / (len(precos) - 1)

    def para_y(preco: float) -> float:
        proporcao = (preco - minimo) / (maximo - minimo)
        return round(margem + util_y - proporcao * util_y, 2)

    pontos = [(round(margem + i * passo, 2), para_y(p)) for i, p in enumerate(precos)]

    caminho = "M " + " L ".join(f"{x} {y}" for x, y in pontos)
    base = altura - margem
    area = f"{caminho} L {pontos[-1][0]} {base} L {pontos[0][0]} {base} Z"

    y_mediana = para_y(median(precos))
    # Preço estável mantém a mediana colada no topo; o rótulo acima dela sairia
    # do viewBox. Nesse caso ele desce para baixo da linha.
    y_rotulo = y_mediana - 7 if y_mediana - 7 > margem else y_mediana + 15

    return Curva(
        caminho=caminho,
        area=area,
        pontos=pontos,
        minimo=min(precos),
        maximo=max(precos),
        mediana=median(precos),
        atual=precos[-1],
        largura=largura,
        altura=altura,
        y_mediana=f"{y_mediana:g}",
        y_rotulo_mediana=f"{y_rotulo:g}",
        x_inicio=f"{margem:g}",
        x_fim=f"{largura - margem:g}",
        x_atual=f"{pontos[-1][0]:g}",
        y_atual=f"{pontos[-1][1]:g}",
    )

"""Todo `from .modulo import nome` do codigo aponta para um nome que existe.

Nasceu de um defeito de 04/10/2026. O pre-commit tem um hook `autoflake` com
`--remove-all-unused-imports`: ele apaga o import que o PROPRIO arquivo nao usa,
mesmo quando outro arquivo o importa DAQUELE modulo. `amazon_grupo` importava
`PRODUTO as PRODUTO_CANONICO` so para o `pipeline` buscar ali, o autoflake o
tirou, e `pipeline._offer_da_amazon` passou a levantar ImportError -- dentro de
uma funcao, num import preguicoso, que so estoura quando a Amazon de um grupo
chega. Tres testes pegaram; sem eles, a rodada inteira cairia em producao.

Este teste varre TODOS os imports do codigo, inclusive os de dentro de funcao,
e confere se o nome existe no modulo de destino. Pega a classe do problema, e
nao so essa instancia.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))


def _imports_do_projeto():
    """(arquivo, linha, modulo de destino, nome) de cada `from ... import ...`."""
    for arquivo in sorted((RAIZ / "src" / "promo").rglob("*.py")):
        modulo = ".".join(arquivo.relative_to(RAIZ / "src").with_suffix("").parts)
        if arquivo.name == "__init__.py":
            modulo = modulo[: -len(".__init__")]
            pacote = modulo
        else:
            pacote = modulo.rsplit(".", 1)[0]

        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if not isinstance(no, ast.ImportFrom):
                continue
            if no.level:
                destino = importlib.util.resolve_name(
                    "." * no.level + (no.module or ""), pacote
                )
            elif (no.module or "").split(".")[0] == "promo":
                destino = no.module
            else:
                continue
            for nome in no.names:
                if nome.name != "*":
                    yield arquivo.relative_to(RAIZ), no.lineno, destino, nome.name


def test_todo_import_do_projeto_resolve():
    quebrados = []
    for arquivo, linha, destino, nome in _imports_do_projeto():
        try:
            modulo = importlib.import_module(destino)
        except Exception as exc:  # noqa: BLE001 - o relatorio e o que importa
            quebrados.append(f"{arquivo}:{linha} importa {destino}, que nao carrega: {exc!r}")
            continue
        if hasattr(modulo, nome):
            continue
        try:  # `from pacote import submodulo`
            importlib.import_module(f"{destino}.{nome}")
        except Exception:  # noqa: BLE001
            quebrados.append(f"{arquivo}:{linha} importa `{nome}` de {destino}, que nao o define")

    assert not quebrados, "\n".join(quebrados)


def test_o_pipeline_monta_a_oferta_da_amazon_sem_import_quebrado():
    """O caso concreto: `_offer_da_amazon` importa dentro da funcao."""
    from promo.pipeline import _offer_da_amazon
    from promo.sources.amazon_grupo import OfertaAmazon

    oferta = _offer_da_amazon(
        OfertaAmazon(asin="B0F3391RMZ", titulo="Produto", preco=60.18)
    )

    assert oferta.url == "https://www.amazon.com.br/dp/B0F3391RMZ"
    assert oferta.image_url.endswith("B0F3391RMZ.jpg")

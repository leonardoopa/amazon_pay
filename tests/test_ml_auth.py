"""O DevCenter recusa localhost, entao o callback do OAuth e colado a mao.

Como e digitacao humana num fluxo que roda uma vez, o parser precisa aceitar
colagem imperfeita e recusar tudo que nao for uma autorizacao legitima desta
execucao.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from promo.sources.mercadolivre import parse_callback  # noqa: E402

STATE = "s3cr3t-state"
CALLBACK = f"https://example.com/callback?code=TG-abc123&state={STATE}"


def test_aceita_url_completa():
    assert parse_callback(CALLBACK, STATE) == "TG-abc123"


def test_aceita_so_a_query_string():
    assert parse_callback(f"code=TG-abc123&state={STATE}", STATE) == "TG-abc123"


def test_ignora_espacos_da_colagem():
    assert parse_callback(f"  {CALLBACK}\n", STATE) == "TG-abc123"


def test_recusa_state_divergente():
    """Protege contra colar a URL de uma tentativa anterior (ou forjada)."""
    with pytest.raises(RuntimeError, match="State divergente"):
        parse_callback("https://example.com/callback?code=X&state=outro", STATE)


def test_propaga_erro_do_mercado_livre():
    url = (
        "https://example.com/callback?error=access_denied"
        f"&error_description=Usuario+negou&state={STATE}"
    )
    with pytest.raises(RuntimeError, match="Usuario negou"):
        parse_callback(url, STATE)


def test_recusa_url_sem_code():
    with pytest.raises(RuntimeError, match="code"):
        parse_callback(f"https://example.com/callback?state={STATE}", STATE)


def test_recusa_entrada_vazia():
    with pytest.raises(RuntimeError, match="Nada colado"):
        parse_callback("   ", STATE)


def test_erro_sem_state_nao_passa_como_sucesso():
    """Um callback de erro sem state nao pode virar 'code ausente' generico."""
    with pytest.raises(RuntimeError, match="recusou|State"):
        parse_callback("https://example.com/callback?error=invalid_client", STATE)

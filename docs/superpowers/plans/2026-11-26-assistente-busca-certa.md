# Assistente que responde as coisas certas — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ligar a busca vetorial com o que já existe, achar "play5", dar ao assistente um Gemini com cota própria e responder perguntas de histórico sobre o último produto mostrado, só com dados do banco.

**Architecture:** mudanças pequenas em `src/promo/assistente/` (índice, busca, banco, conversa, serviço) e `src/promo/config.py`. Nada de serviço ou dependência nova.

**Tech Stack:** Python 3.12, SQLite (FTS5 + vetores em BLOB), numpy, google-genai, FastAPI.

**Spec:** `docs/superpowers/specs/2026-11-26-assistente-busca-certa-design.md`

## Global Constraints

- Sem dependência nova. Python 3.12. No máximo 2 chamadas ao Gemini por mensagem.
- O assistente nunca usa `GEMINI_MODEL` (modelo do grupo); o padrão dele é `gemini-3.1-flash-lite`.
- Resposta de histórico só com dados do banco (preço, data, loja, link); nada inventado.
- Texto para o usuário em português com acento; comentários e docstrings em português sem acento (estilo do repo).
- Testes sem rede e sem `.env`, SEMPRE por `/private/tmp/claude-501/-Users-leonardogabrielbarbosademelo-Desktop-dev-amazon-pay/ec5b3dfc-f32d-4ecd-8563-d7feffa13c71/scratchpad/rodar_testes.sh <args do pytest>`; nunca ler, mover ou editar `.env`.
- `black` + `flake8 --max-line-length 120 --extend-ignore E203,E231,E202,E702` (em `.../scratchpad/testenv/bin/`) nos arquivos alterados.
- Commit em português, prefixo convencional, terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Sem push.

## Review Focus

- Pergunta de histórico depois de 30 minutos → sem contexto: não pode responder sobre produto velho (Task 3, `test_contexto_expira_em_30_minutos`).
- "o segundo" quando só um produto foi mostrado → pede o produto, não quebra (Task 3, `test_item_fora_da_lista_pede_o_produto`).
- Produto mostrado que ficou indisponível → resposta honesta, sem preço (Task 3, `test_historico_de_produto_que_sumiu`).
- Banco antigo sem as colunas novas (`itens.prioridade`, `mensagens.produtos`) → migra sozinho na subida (Tasks 1 e 3).
- Vetor ligado não pode trazer lixo quando a palavra acha (Task 1, `test_com_vetor_ligado_a_palavra_continua_na_frente`).

---

### Task 1: vetor ligado com 62%, prioridade e "play5"

**Files:** Modify `src/promo/assistente/indice.py`, `src/promo/assistente/servico.py` (`indexar`), `src/promo/assistente/busca.py` (`SINONIMOS`), `src/promo/assistente/banco.py` (SCHEMA de `itens` + migração). Create `tests/test_busca_vetor.py`.

**Interfaces — Produces:**
- `indice.MINIMO_DE_VETORES = 2000`; `COBERTURA_MINIMA = 0.9` fica. Em `servico.indexar`: vetores valem quando `len(vetores) >= MINIMO_DE_VETORES or cobertura >= COBERTURA_MINIMA`.
- `itens.prioridade INTEGER NOT NULL DEFAULT 0`. `banco.conectar` migra banco antigo (`ALTER TABLE itens ADD COLUMN prioridade INTEGER NOT NULL DEFAULT 0` quando `PRAGMA table_info(itens)` não tem a coluna). A migração fica numa função `banco._migrar(conn)` chamada junto do SCHEMA (a Task 3 acrescenta a dela lá).
- `sincronizar` grava `prioridade = 1` para produto com post `status='sent'` e `sent_at` nos últimos 30 dias no banco do bot (`posts`); 0 para o resto (inclusive quando deixa de ter).
- `vetorizar_pendentes` ordena por `i.prioridade DESC, i.visto_em DESC`.
- `busca.SINONIMOS` ganha: `"play5"`, `"play 5"`, `"playstation5"` → mesmas alternativas do ps5 (`"ps5"`, `"playstation 5"`, `"playstation5"`); `"play4"`, `"play 4"`, `"playstation4"` → do ps4; `"play station"` → `("playstation",)`. `"ps5"` e `"playstation 5"` passam a incluir `"playstation5"`.

- [ ] **Step 1: testes que falham** (`tests/test_busca_vetor.py`, reaproveitando os helpers de `tests/test_assistente.py` por import: `bancos`, `produto`, `indexado`, `EmbeddingsFalsos`, `saiu_no_grupo`):
  - `test_vetor_liga_com_dois_mil_vetores_mesmo_abaixo_de_90_por_cento` — monkeypatch `MINIMO_DE_VETORES` para 30; 40 produtos, `EmbeddingsQueCaem`-like que vetoriza 30 de 40 (75%) → `indexar` deixa `busca._vetores` ligado; com 20 de 40 (abaixo de 30 e de 90%) → `None`.
  - `test_produto_do_grupo_vetoriza_primeiro` — 3 produtos, o mais antigo saiu no grupo; com `limite=1`, o vetorizado é o do grupo.
  - `test_prioridade_sai_quando_o_post_fica_velho` — post com `sent_at` de 31 dias → prioridade 0.
  - `test_banco_antigo_ganha_a_coluna_prioridade` — cria `itens` sem a coluna num arquivo, `conectar` nele → coluna existe.
  - `@pytest.mark.parametrize` `test_play5_acha_o_playstation_5` com `"me mande promoções do play5"`, `"play 5"`, `"playstation5"`, `"play station 5"` → acha "Console PlayStation 5 Slim Digital" (via `pedido_simples(...).consulta` + `buscar`).
  - `test_com_vetor_ligado_a_palavra_continua_na_frente` — com vetores falsos ligados, "fone bluetooth" com um fone e um produto sem relação: o fone vem primeiro e o sem relação não aparece.
- [ ] **Step 2:** `rodar_testes.sh tests/test_busca_vetor.py` — FAIL.
- [ ] **Step 3:** implementar os Interfaces.
- [ ] **Step 4:** `rodar_testes.sh tests/test_busca_vetor.py tests/test_assistente.py` — PASS.
- [ ] **Step 5:** commit `feat: busca vetorial liga com 2 mil vetores, vetoriza o grupo primeiro e acha play5`.

### Task 2: Gemini próprio do assistente

**Files:** Modify `src/promo/config.py`, `src/promo/assistente/conversa.py` (só o modelo padrão do `Assistente`), `.env.sample`. Create `tests/test_assistente_modelo.py`.

**Interfaces — Produces:** `config.assistente_gemini_modelo() -> str` = env `ASSISTENTE_GEMINI_MODEL` ou `"gemini-3.1-flash-lite"`. `Assistente.__init__`: `self._modelo = modelo or assistente_gemini_modelo()` (não mais `copy_model()`).

- [ ] **Step 1: testes que falham:** `test_assistente_usa_o_modelo_proprio_por_padrao` (sem env → GeminiFalso vê `model == "gemini-3.1-flash-lite"`, mesmo com `GEMINI_MODEL=gemini-3.5-flash-lite` no env); `test_modelo_do_assistente_vem_do_env` (`ASSISTENTE_GEMINI_MODEL=x` → `"x"`). O falso grava o `model` recebido em `generate_content`.
- [ ] **Step 2:** `rodar_testes.sh tests/test_assistente_modelo.py` — FAIL.
- [ ] **Step 3:** implementar; `.env.sample` ganha `ASSISTENTE_GEMINI_MODEL=gemini-3.1-flash-lite` com 2 linhas de comentário (cota por modelo: não disputa a do grupo).
- [ ] **Step 4:** `rodar_testes.sh tests/test_assistente_modelo.py tests/test_assistente.py` — PASS.
- [ ] **Step 5:** commit `feat: assistente usa um Gemini com cota propria`.

### Task 3: memória da última resposta e histórico de preço

**Files:** Modify `src/promo/assistente/banco.py`, `src/promo/assistente/busca.py`, `src/promo/assistente/conversa.py`, `src/promo/assistente/servico.py`, `tests/test_assistente.py` (só os falsos `AssistenteFalso`/`AssistenteComCartoes` passam a aceitar `contexto=None`). Create `tests/test_assistente_conversa.py`.

**Interfaces:**
- Consumes: `banco._migrar(conn)` (Task 1).
- Produces:
  - `banco`: coluna `mensagens.produtos TEXT` (migração em `_migrar`); `finalizar(..., produtos: list[str] | None = None)` grava JSON; `@dataclass(frozen=True) class Contexto: consulta: str | None; produtos: tuple[str, ...]`; `ultimo_contexto(conn, telefone: str, agora: datetime | None = None) -> Contexto | None` — última `respondida` do telefone com `respondida_em` nos últimos `MINUTOS_DE_CONTEXTO = 30` e `produtos` não vazio.
  - `busca`: `@dataclass(frozen=True) class Historico: resultado: Resultado; menor_preco: float | None; menor_em: str | None` (data `YYYY-MM-DD`)`; dias: int; grupo_em: str | None; grupo_preco: float | None`; `historico(product_id: str) -> Historico | None` (None quando `detalhes` não devolve o produto). Janela de 60 dias de `price_history` e de `posts` enviados.
  - `conversa`: `Pedido.tipo: Literal["produto", "historico", "conversa"]`; `Pedido.item: int | None = None`; `Pedido.preco_citado: float | None = None`. `Resposta.produtos: tuple[str, ...] = ()` (último campo; ids mostrados). `preco_citado(texto: str) -> float | None` ("4k"/"4 mil" → 4000.0; "R$ 3.799" e "3799 reais" → 3799.0). `pedido_simples(texto: str, tem_contexto: bool = False)`: com contexto e sinal de histórico (regex sem acento: `melhor preco|menor preco|mais barato|ja esteve|ja teve|baixou|esse foi|historico`) → `Pedido(tipo="historico", item=..., preco_citado=...)`, item por "primeiro/segundo/terceiro" ou dígito 1–3, padrão 1. `texto_do_historico(h: Historico, link: str, preco_citado: float | None, agora: datetime | None) -> str`. `Assistente.entender(texto, contexto_texto: str | None = None)` (o contexto vai no conteúdo enviado ao Gemini, prefixado por "Conversa anterior:"); `Assistente.responder(texto, agora=None, contexto: Contexto | None = None) -> Resposta`.
  - `servico.Atendente.atender_uma`: lê `ultimo_contexto` na mesma conexão, chama `responder(msg["texto"], contexto=contexto)` e grava `produtos=list(resposta.produtos)` no `finalizar`.
- Texto do histórico (linhas, nesta ordem; as fixadas por teste entre aspas): `*{nome_curto}*`; `"Hoje: {R$} no {loja}, {visto}."`; `"Menor preço que medimos em 60 dias: {R$}, em {DD/MM}."` ou `"Ainda sem histórico suficiente para comparar o preço."` (menos de `DIAS_MINIMOS` dias); `"Saiu no grupo em {DD/MM} por {R$}."` se houver; com preço citado: `"Sim: medimos {R$ menor} em {DD/MM}, abaixo dos {R$ citado} que você viu."` / `"Não medimos esse produto a {R$ citado} ou menos nos últimos 60 dias."`; `veredito(...)`; link; e `DIVULGACAO` no fim. Histórico sem contexto ou item fora da lista: `"Me diga de qual produto você quer saber o preço."`. Produto que sumiu: `"Esse produto não aparece mais disponível nas lojas que acompanhamos."`.
- `ENTENDER` ganha: `historico` (pergunta sobre preço/histórico de produto já mostrado, com `item` e `preco_citado`) e, com conversa anterior, busca nova sai com a consulta completa ("e o console?" depois de "ps5" → "console ps5").
- Resposta de histórico mantém `produtos = contexto.produtos` (a próxima pergunta continua no mesmo assunto).

- [ ] **Step 1: testes que falham** em `tests/test_assistente_conversa.py`: `test_produtos_mostrados_ficam_gravados`, `test_contexto_dos_ultimos_30_minutos`, `test_contexto_expira_em_30_minutos`, `test_contexto_e_do_proprio_telefone`, `test_banco_antigo_ganha_a_coluna_produtos`, `test_preco_citado` (parametrizado com os 4 formatos), `test_sem_gemini_pergunta_de_preco_com_contexto_vira_historico` (a frase real "esse foi o melhor preço? vi que teve promoção esses dias por 4k" → historico, item 1, 4000.0), `test_o_segundo_escolhe_o_item_2`, `test_sem_contexto_nao_e_historico`, `test_historico_le_menor_preco_e_data_e_envio_ao_grupo`, `test_texto_do_historico_compara_o_preco_citado` (abaixo e acima), `test_historico_de_produto_que_sumiu`, `test_item_fora_da_lista_pede_o_produto`, `test_responder_historico_com_gemini_e_sem_gemini`, `test_entender_manda_a_conversa_anterior_ao_gemini`, `test_atendente_passa_o_contexto_da_resposta_anterior`.
- [ ] **Step 2:** `rodar_testes.sh tests/test_assistente_conversa.py` — FAIL.
- [ ] **Step 3:** implementar os Interfaces.
- [ ] **Step 4:** `rodar_testes.sh` (suíte toda) — PASS.
- [ ] **Step 5:** commit `feat: assistente lembra a ultima resposta e responde historico de preco`.

## Ordem

Tasks 1 e 2 são independentes (arquivos diferentes) e podem correr em paralelo; a 3 vem depois das duas (usa `_migrar` e mexe em `conversa.py`).

## Validação (controlador)

1. Suíte inteira, `black --check`, `flake8`.
2. Conjunto fixo de perguntas na cópia de produção (`promos.db` + só `itens`/`vetores` do `assistente.db`), `staging` x branch: acerto no 1º, precisão nos 3, respostas vazias.
3. As perguntas de acompanhamento reais com o `gemini-3.1-flash-lite`, sem enviar nada.

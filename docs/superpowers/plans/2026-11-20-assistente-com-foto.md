# Assistente com a foto de cada produto — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cada produto da resposta do assistente vai como uma mensagem de WhatsApp com a foto e a legenda dele, e um texto de fechamento no fim.

**Architecture:** `busca.Resultado` passa a carregar a foto (`products.image_url`). `conversa.Assistente.responder` devolve um `Cartao` por produto (legenda escrita pelo Gemini e conferida uma a uma, ou do modelo fixo) e o fechamento em `Resposta.texto`. `Evolution.send_card` baixa a foto ele mesmo (URL ou arquivo local), valida e manda os bytes; foto ruim vira texto. `servico.Atendente` manda os cartões na ordem e depois o fechamento.

**Tech Stack:** Python 3.12, FastAPI, SQLite, httpx, pydantic, google-genai (Gemini), Evolution API.

**Spec:** `docs/superpowers/specs/2026-11-20-assistente-com-foto-design.md`

## Global Constraints

- Sem dependência nova. Python 3.12.
- Legenda de foto: no máximo `CAPTION_LIMIT = 1024` caracteres (já em `src/promo/delivery/evolution.py`).
- Foto do cartão: timeout `10.0` s, uma tentativa, nenhum `time.sleep`; URL só vale com status 200, `content-type` começando com `image/` e sem `gif`, e pelo menos `1024` bytes; arquivo local, pelo menos `1024` bytes.
- O Gemini continua com 2 chamadas por pergunta de produto (`entender` + uma escrita).
- `send_post`, `send_image` e `send_image_bytes` (envio do grupo) não mudam de comportamento.
- Texto para o usuário em português com acento; comentários e docstrings no estilo do repositório (português sem acento).
- Testes sem rede e sem `.env`. Rode SEMPRE pelo helper, nunca `pytest` direto no checkout e nunca mexendo no `.env`:
  `/private/tmp/claude-501/-Users-leonardogabrielbarbosademelo-Desktop-dev-amazon-pay/ec5b3dfc-f32d-4ecd-8563-d7feffa13c71/scratchpad/rodar_testes.sh <args do pytest>`
- Formatação: `black`; lint: `flake8 --max-line-length 120 --extend-ignore E203,E231,E202,E702` (os dois em `.../scratchpad/testenv/bin/`).
- Commits: mensagem em português, prefixo convencional (`feat:`, `test:`…), terminando com `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Branch `feat/assistente-com-foto`; não fazer push.

## Review Focus

- Foto que responde 200 mas é página HTML (erro da CDN) → aquele produto vai como texto (Task 2, `test_pagina_html_no_lugar_da_foto_vira_texto`).
- Arquivo local apagado de `/app/data/fotos` → texto, sem derrubar a resposta (Task 2, `test_arquivo_local_que_sumiu_vira_texto`).
- Gemini devolve legendas a mais/a menos ou com o link de outro produto → a escrita inteira é recusada e valem as legendas fixas (Task 3).
- Abertura do Gemini que, somada à primeira legenda, passa de 1024 caracteres → recusada (Task 3).
- Evolution cai entre um cartão e outro (`NotConnected`) → mensagem marcada `erro`, fila segue (Task 4).

---

### Task 1: a foto no resultado da busca

**Files:**
- Modify: `src/promo/assistente/busca.py` (`Resultado`, `detalhes`)
- Test: `tests/test_assistente.py` (helper `produto` e testes novos)

**Interfaces:**
- Produces: `Resultado.imagem: str | None = None` (último campo do dataclass). URL `http(s)`, caminho local (ex.: `/app/data/fotos/3EB0.jpg`) ou `None`.

- [ ] **Step 1: testes que falham.** Acrescente `imagem: str | None = None` ao helper `produto(...)` de `tests/test_assistente.py`, repassando como `Offer(image_url=imagem)`. Testes novos:

```python
def test_o_resultado_traz_a_foto_do_produto(bancos):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1", imagem="https://http2.mlstatic.com/D_1.jpg")
    [r] = indexado().buscar("playstation")
    assert r.imagem == "https://http2.mlstatic.com/D_1.jpg"

def test_foto_em_arquivo_local_vem_como_caminho(bancos):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1", imagem="/app/data/fotos/3EB0.jpg")
    [r] = indexado().buscar("playstation")
    assert r.imagem == "/app/data/fotos/3EB0.jpg"

@pytest.mark.parametrize("imagem", [None, ""])
def test_produto_sem_foto_vem_com_imagem_none(bancos, imagem):
    produto("Console PlayStation 5 Slim", 3816.0, "MLB1", imagem=imagem)
    [r] = indexado().buscar("playstation")
    assert r.imagem is None
```

- [ ] **Step 2:** rode `rodar_testes.sh tests/test_assistente.py -k "foto or imagem"` — esperado: FAIL (`Resultado` sem `imagem`).
- [ ] **Step 3:** em `detalhes()`, inclua `image_url` no `SELECT` de `products` e preencha `imagem=produto["image_url"] or None`. A junção de anúncios do mesmo modelo (`replace(melhor_anuncio(...), ...)`) já leva a foto do anúncio escolhido.
- [ ] **Step 4:** rode `rodar_testes.sh tests/test_assistente.py` — esperado: tudo passa.
- [ ] **Step 5:** commit `feat: resultado da busca do assistente traz a foto do produto`.

### Task 2: envio de cartão com foto baixada por nós

**Files:**
- Modify: `src/promo/delivery/evolution.py`
- Create: `tests/test_cartao.py`

**Interfaces:**
- Produces:
  - `TIMEOUT_DA_FOTO_DO_CARTAO = 10.0` e `FOTO_MINIMA_BYTES = 1024` (constantes do módulo; `send_image_bytes` pode passar a usar `FOTO_MINIMA_BYTES` no lugar do `1024` literal, sem mudar comportamento).
  - `baixar_foto_do_cartao(imagem: str) -> bytes` — função de módulo: URL via `httpx.Client(timeout=TIMEOUT_DA_FOTO_DO_CARTAO, follow_redirects=True, headers={"User-Agent": MEDIA_USER_AGENT})`; caminho local via leitura do arquivo. Levanta exceção (ex.: `ValueError`) quando não é foto válida pelas regras dos Global Constraints.
  - `Evolution.send_card(self, caption: str, image: str | None, to: str | None = None) -> dict` — sem `image`: `self.send_text(caption, to)`. Com `image`: `self._enviar_bytes(baixar_foto_do_cartao(image), caption, to)`. `NotConnected` sobe; qualquer outra exceção: `log.warning` e `self.send_text(caption, to)`.
- Os testes chamam `Evolution.send_card(falso, ...)` com um falso que tem `_enviar_bytes(bruto, caption, to)` e `send_text(text, to)` (mesmo padrão de `tests/test_entrega.py`), e trocam `httpx.Client` dentro de `promo.delivery.evolution` por um cliente falso (com `__enter__/__exit__` e `get(url)` devolvendo `httpx.Response`).

- [ ] **Step 1: testes que falham** em `tests/test_cartao.py` (nomes e o que cada um afirma):
  - `test_foto_por_url_vai_em_bytes_com_a_legenda` — 200 `image/jpeg` de 20.000 bytes → `_enviar_bytes` recebe exatamente esses bytes, a legenda e o `to`; nenhum `send_text`.
  - `test_baixa_com_o_nosso_user_agent_e_timeout_de_10s` — o `httpx.Client` é criado com `timeout == 10.0`, `follow_redirects is True` e `headers["User-Agent"] == MEDIA_USER_AGENT`.
  - `test_pixel_da_amazon_vira_texto` — 200 `image/gif` de 43 bytes → `send_text(legenda)`, nada em bytes.
  - `test_pagina_html_no_lugar_da_foto_vira_texto` — 200 `text/html` de 30.000 bytes → texto.
  - `test_foto_pequena_demais_vira_texto` — 200 `image/jpeg` de 500 bytes → texto.
  - `test_erro_http_vira_texto` — 403 → texto.
  - `test_falha_de_rede_vira_texto` — `get` levanta `httpx.ConnectError` → texto.
  - `test_foto_em_arquivo_local_vai_em_bytes` — arquivo em `tmp_path` com 20.000 bytes → bytes enviados.
  - `test_arquivo_local_que_sumiu_vira_texto` — caminho inexistente → texto.
  - `test_sem_foto_manda_so_o_texto_sem_baixar` — `image=None` → texto e o `httpx.Client` falso nunca é criado.
  - `test_instancia_caida_nao_vira_texto` — `_enviar_bytes` levanta `NotConnected` → `pytest.raises(NotConnected)`; nenhum `send_text`.
  - `test_nao_espera_entre_tentativas` — com `time.sleep` trocado por algo que levanta, uma foto que falha cai para texto sem chamar `sleep`.
- [ ] **Step 2:** `rodar_testes.sh tests/test_cartao.py -v` — esperado: FAIL (`send_card` não existe).
- [ ] **Step 3:** implemente as constantes, `baixar_foto_do_cartao` e `Evolution.send_card` como nos Interfaces. Docstring curta dizendo por que o assistente não usa `send_post` (CDN do Pelando recusa a Evolution, pixel da Amazon passa como sucesso, esperas de 2 s + 6 s são para o grupo).
- [ ] **Step 4:** `rodar_testes.sh tests/test_cartao.py tests/test_entrega.py tests/test_foto_da_amazon.py` — esperado: tudo passa.
- [ ] **Step 5:** commit `feat: envio de cartao com foto baixada pelo proprio bot`.

### Task 3: legendas por produto e os cartões da resposta

**Files:**
- Modify: `src/promo/assistente/conversa.py`
- Test: `tests/test_assistente.py` (`GeminiFalso`, testes da conversa)

**Interfaces:**
- Consumes: `Resultado.imagem` (Task 1); `CAPTION_LIMIT` de `promo.delivery.evolution`.
- Produces:
  - `@dataclass(frozen=True) class Cartao: legenda: str; imagem: str | None`
  - `Resposta` ganha `cartoes: tuple[Cartao, ...] = ()` (último campo). Resposta de produto: `texto` = fechamento; `cartoes` = um por resultado, na ordem. Boas-vindas, "não achei", PAROU e VOLTOU: `cartoes == ()`.
  - `class Escrita(BaseModel): abertura: str = ""; legendas: list[str] = []`
  - `abertura_fixa(consulta: str) -> str` → `f"Achei estas opções para *{consulta}*:"`
  - `legenda_fixa(n: int, item: dict) -> str` → o bloco que `modelo_fixo` monta hoje para o item `n` (`"{n}. *{nome_curto}*"`, preço/loja/visto, linha do grupo se houver, veredito, link).
  - `fechamento(parecidos: list[dict]) -> str` → bloco "Parecidos:" no formato de hoje (se houver) + linha em branco + `DIVULGACAO`; sem parecidos, só `DIVULGACAO`.
  - `montar_cartoes(abertura: str, legendas: list[str], imagens: list[str | None]) -> tuple[Cartao, ...]` → a abertura (quando não vazia) entra no começo da primeira legenda, separada por `"\n\n"`.
  - `escrita_aprovada(escrita: Escrita, achados: list[dict]) -> bool` → `True` só se: `len(legendas) == len(achados)`; a abertura não tem preço (`_precos`) nem link (`LINK`); cada legenda passa em `fiel(legenda, [achado])` e tem até `CAPTION_LIMIT`; e a primeira legenda com a abertura na frente cabe em `CAPTION_LIMIT`.
  - `Assistente._escrever(pergunta: str, achados: list[dict]) -> Escrita | None` — uma chamada `_gerar(..., response_mime_type="application/json", response_schema=Escrita, temperature=0.4)`; usa `parsed` ou `Escrita.model_validate_json(texto)`; devolve `None` em falha ou se `escrita_aprovada` recusar (com `log`).
  - `modelo_fixo` sai (substituído por `abertura_fixa` + `legenda_fixa` + `fechamento`).
- `ESCREVER` passa a pedir JSON com `abertura` (uma frase curta, sem preço e sem link) e `legendas` (uma por item de `achados`, na ordem: nome encurtado sem mudar o modelo, preço exatamente como está, loja, "visto", "grupo" quando houver, veredito, e o link sozinho na última linha; português com acentos; negrito só no nome). Os parecidos não vão para o Gemini.

- [ ] **Step 1: ajuste os falsos e os testes antigos.** `GeminiFalso.generate_content` devolve o pedido quando `config.response_schema is Pedido` (o SDK guarda a classe como recebeu — verificado); nos outros casos conta `textos_pedidos` e devolve `Resp(text=e.model_dump_json(), parsed=e)` com `e = self._texto(json.loads(contents))` (um `Escrita`), ou `Resp(text="")` sem `_texto`. Helper `tudo(resposta) = "\n\n".join([c.legenda for c in resposta.cartoes] + [resposta.texto])`. Testes que hoje leem `resposta.texto` em resposta de produto passam a ler `tudo(resposta)` ou `resposta.cartoes[0].legenda`; `test_o_texto_do_gemini_sai_quando_e_fiel` vira: o Gemini devolve `Escrita(abertura="Achei!", legendas=[f"*PS5 Slim* por {item['preco']} no ML\n{item['link']}"])` e o teste afirma `resposta.cartoes[0].legenda.startswith("Achei!\n\n*PS5 Slim* por R$ 3.816,00")`, `resposta.texto == DIVULGACAO` e `(consulta, resultados) == ("playstation 5", 1)`.
- [ ] **Step 2: testes novos que falham:**
  - `test_cada_produto_vira_um_cartao_com_a_foto_dele` — dois produtos com `imagem` diferentes, `GeminiFalso(falhar=True)` → 2 cartões; `[c.imagem for c in cartoes]` na ordem dos resultados; `cartoes[0].legenda` começa com `"Achei estas opções para *"` e contém `"1. *"`; `cartoes[1].legenda` começa com `"2. *"`; `resposta.texto.endswith(DIVULGACAO)`.
  - `test_escrita_aprovada_aceita_legendas_fieis` — `True`.
  - `test_escrita_com_legenda_a_menos_e_recusada` — 1 legenda para 2 achados → `False`.
  - `test_legenda_com_o_link_de_outro_produto_e_recusada` — `False`.
  - `test_abertura_com_preco_ou_link_e_recusada` — `False` para `"Só R$ 10,00!"` e para uma abertura com URL.
  - `test_abertura_mais_primeira_legenda_acima_de_1024_e_recusada` — abertura de 1.000 caracteres + legenda válida → `False`.
  - `test_escrita_recusada_vira_legendas_fixas` — o Gemini manda legenda com `"R$ 2.999,00"` → nenhum cartão contém `2.999,00` e `cartoes[0].legenda` contém `"R$ 3.816,00 no Mercado Livre"`.
  - `test_gemini_escreve_uma_vez_por_pergunta` — `textos_pedidos == 1` depois de um `responder` com resultados.
  - `test_resposta_sem_produto_nao_tem_cartao` — boas-vindas e "não achei" → `cartoes == ()`.
- [ ] **Step 3:** `rodar_testes.sh tests/test_assistente.py` — esperado: FAIL nos testes novos.
- [ ] **Step 4:** implemente os Interfaces em `conversa.py`; `responder` monta `achados`, chama `_escrever`, cai para `abertura_fixa` + `legenda_fixa` quando vier `None`, e devolve `Resposta(fechamento(extras), pedido.consulta, len(resultados), montar_cartoes(...))` com as imagens dos resultados.
- [ ] **Step 5:** `rodar_testes.sh tests/test_assistente.py` — esperado: tudo passa.
- [ ] **Step 6:** commit `feat: resposta do assistente vira um cartao por produto`.

### Task 4: o atendimento manda os cartões

**Files:**
- Modify: `src/promo/assistente/servico.py` (`Atendente.atender_uma`)
- Test: `tests/test_assistente.py` (`EntregaFalsa` e testes novos)

**Interfaces:**
- Consumes: `Cartao`, `Resposta.cartoes` (Task 3); `entrega.send_card(caption, image, to=...)` (assinatura da Task 2).
- Comportamento: com `cartoes`, uma espera humana, um `send_card(c.legenda, c.imagem, to=jid)` por cartão na ordem, e um `send_text(fechamento, to=jid)` com o aviso de privacidade no fechamento quando for o primeiro contato. Sem `cartoes`, igual a hoje. `mensagens.resposta` grava `"\n\n".join(legendas + [fechamento enviado])`. O log de "respondeu" acrescenta quantos cartões tinham foto (`imagem` não vazia).

- [ ] **Step 1:** `EntregaFalsa` ganha `self.cartoes: list[tuple[str | None, str, str | None]] = []` e `send_card(self, legenda, imagem, to=None)`, que grava `(to, legenda, imagem)` e aceita um parâmetro do construtor para levantar `NotConnected` no n-ésimo cartão. Um `AssistenteComCartoes` falso devolve `Resposta("fechamento", "ps5", 2, (Cartao("1. a", "https://f/1.jpg"), Cartao("2. b", None)))`.
- [ ] **Step 2: testes que falham:**
  - `test_resposta_com_cartoes_manda_cada_foto_e_depois_o_fechamento` — `entrega.cartoes == [(jid, "1. a", "https://f/1.jpg"), (jid, "2. b", None)]` e `entrega.enviados == [(jid, f"fechamento\n\n{AVISO_PRIMEIRA_VEZ}")]`, com `jid = "5581988887777@s.whatsapp.net"`.
  - `test_o_aviso_de_privacidade_vai_no_fechamento_e_nao_na_legenda` — `AVISO_PRIMEIRA_VEZ` não está em nenhuma legenda enviada.
  - `test_o_que_fica_gravado_e_tudo_que_foi_enviado` — `mensagens.resposta == "1. a\n\n2. b\n\nfechamento\n\n" + AVISO_PRIMEIRA_VEZ`.
  - `test_instancia_caida_no_segundo_cartao_marca_erro` — `NotConnected` no 2º cartão → `atender_uma()` devolve `True`, `status(id)[0] == "erro"`, o 1º cartão foi gravado em `entrega.cartoes` e nenhum fechamento saiu.
- [ ] **Step 3:** `rodar_testes.sh tests/test_assistente.py` — esperado: FAIL nos novos; os testes antigos do atendimento (que usam `AssistenteFalso` sem cartões) continuam passando.
- [ ] **Step 4:** implemente o comportamento em `atender_uma`.
- [ ] **Step 5:** `rodar_testes.sh tests/test_assistente.py` — esperado: tudo passa.
- [ ] **Step 6:** commit `feat: atendimento manda a foto de cada produto e depois o fechamento`.

## Ordem e paralelismo

- Tasks 1 e 2 são independentes (arquivos diferentes) e podem correr em paralelo.
- Task 3 depende da 1; Task 4 depende da 3 (e usa só o nome/assinatura de `send_card` da 2).

## Verificação final (controlador, depois das tasks)

1. `rodar_testes.sh` sem argumentos — esperado: 1.354 da base + os novos, 0 falhas.
2. `black --check` e o `flake8` dos Global Constraints nos arquivos alterados — sem saída.
3. Ensaio do design (cópia do banco de produção em `.../scratchpad/banco/promos.db`, busca só por texto, Gemini real em 5 perguntas, `send_card` com o POST gravado em vez de enviado, fotos locais copiadas da VPS só para os produtos que aparecerem) — esperado: todo cartão com foto válida sai em bytes; os que caírem para texto têm motivo registrado.
4. Na VPS, só leitura: os caminhos locais em `products.image_url` existem e têm pelo menos 1 KB.

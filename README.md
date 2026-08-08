# amazon_pay — bot de ofertas para grupo de WhatsApp

Monitora preços no Mercado Livre e na Amazon, guarda o histórico, e só te avisa
quando o preço cai **de verdade** contra a média histórica — não contra o
"de R$X por R$Y" inflado da loja. O Gemini escreve o post pronto e manda no seu
WhatsApp; você revisa e encaminha pro grupo.

```
[daemon] → coleta ML/Amazon → SQLite (1 preço/dia) → filtro de desconto real
                                                          ↓
                                     Gemini escreve o post → seu WhatsApp
                                                                  ↓
                                                       você encaminha pro grupo
```

## Por que você posta e não o bot

A Cloud API oficial do WhatsApp **não envia para grupos comuns**. As libs
não-oficiais (Baileys, whatsapp-web.js) violam os termos e derrubam o número.
Então o bot te manda o texto no privado e você encaminha — o que ainda te dá
um passo de revisão antes de qualquer coisa ir pro grupo.

## Setup

```bash
cp .env.example .env
```

Preencha o `.env`:

**Gemini** — a key sai do [Google AI Studio](https://aistudio.google.com/apikey).
O `GEMINI_MODEL` padrão é `gemini-3.5-flash-lite`; `gemini-3.6-flash` é mais capaz e
`gemini-3.5-flash-lite` é o mais barato. No volume de um grupo (poucos posts por
dia) a diferença de custo é irrelevante — escolha por qualidade de texto.

**Mercado Livre** — crie o app em [developers.mercadolivre.com.br/devcenter](https://developers.mercadolivre.com.br/devcenter)
→ *Criar nova aplicação*. Preencha nome, nome curto e descrição, marque o scope
**read**, aceite os termos e crie. Depois clique em *Editar*: o **App ID** é o
`ML_CLIENT_ID` e a **Secret Key** é o `ML_CLIENT_SECRET`. Não há aprovação.

No campo **URIs de redirect** há duas restrições que o formulário não explica:
precisa ser **HTTPS** e **`localhost` é recusado** ("O endereço deve ser válido").
Como o `ml-auth` é por colagem manual, nada precisa rodar nesse endereço — ele
só precisa ser aceito pelo formulário. O padrão é `https://example.com/callback`
(domínio reservado pela IANA, inerte). Se você tiver um domínio próprio, use o seu.

Evite os serviços "cole seu code aqui" que aparecem em tutoriais: eles existem
para ler exatamente esse valor. O risco é limitado — trocar o `code` por token
também exige o `client_secret`, que nunca sai da sua máquina — mas não há motivo
para entregar o código a terceiros.

O valor no `.env` tem que bater **caractere por caractere** com o registrado.

**Amazon** — opcional na fase 1. A PA-API 5.0 foi aposentada em 15/05/2026;
hoje é a Creators API (OAuth 2.0, `client_credentials`). O acesso exige conta
Associates com **3 vendas qualificadas em 180 dias**, e cai se ficar 30 dias
sem venda. Sem as credenciais o pipeline roda só com o ML.

**WhatsApp** — app em [developers.facebook.com](https://developers.facebook.com)
→ WhatsApp → API Setup. Pegue o `phone_number_id` e o token, e cadastre seu
número pessoal como destinatário de teste.

## Docker

```bash
docker compose build
```

O ML só aceita `authorization_code`, então autorize uma vez:

```bash
docker compose run --rm amazon_pay ml-auth
```

O comando imprime uma URL. Abra, autorize, e o navegador vai redirecionar para
o endereço registrado — **a página não vai carregar nada útil, e tudo bem**. O
que importa está na barra de endereços: copie a URL inteira e cole no terminal.
O token fica salvo em `./data/promos.db` e o `refresh_token` mantém o acesso
vivo, então isso é uma vez só.

```bash
docker compose run --rm amazon_pay test-whatsapp
```

### Enquanto a conta do ML não é liberada

A busca do ML responde **403 sem token** — não há como coletar antes da
aprovação. Mas dá pra validar tudo que vem depois do coletor com dados
sintéticos:

```bash
docker compose run --rm amazon_pay seed --days 60
```

```bash
docker compose run --rm amazon_pay demo
```

O `demo` roda scoring + Gemini de ponta a ponta e imprime os posts, sem enviar
nada. É o comando pra calibrar o tom do texto antes de ter dados reais. Use
`--no-ai` pra ver o texto de fallback, e `seed --clear` pra apagar os dados
sintéticos — eles ficam com source `demo` e nunca se misturam ao histórico real.

### Depois de liberada

Nos primeiros 7-10 dias, rode só coletando. O filtro precisa de histórico: sem
pelo menos 7 dias de preço por produto (`MIN_OBSERVATIONS`) ele não tem como
saber o que é preço normal, e não posta nada.

```bash
docker compose run --rm amazon_pay daemon --collect-only --interval 7200
```

Quando houver histórico, confira o que ele postaria — sem enviar nada:

```bash
docker compose run --rm amazon_pay run --dry-run
```

Satisfeito com o tom, sobe pra valer:

```bash
docker compose up -d
```

```bash
docker compose logs -f
```

O `daemon` é um loop simples (roda → dorme → repete), não cron dentro da
imagem: um processo só, logs no stdout e `docker stop` encerra na hora em vez
de esperar o sleep terminar. Intervalo em `RUN_INTERVAL_SECONDS` (padrão 2h).

Dois detalhes do compose: o `restart: unless-stopped` faz o container reiniciar
em loop se o `.env` estiver incompleto — o log diz qual variável falta. E a
porta 8123 só é usada pelo `ml-auth`; o daemon não escuta nada.

## Sem Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Os mesmos comandos, via `.venv/bin/amazon_pay` (ou o alias `promo`):

```bash
.venv/bin/amazon_pay ml-auth
```

```bash
.venv/bin/amazon_pay run --dry-run
```

Aqui dá pra usar cron em vez do daemon:

```bash
0 */2 * * * cd /caminho/do/projeto && .venv/bin/amazon_pay run >> data/cron.log 2>&1
```

Outros comandos: `stats` (estado do banco), `flush` (reenvia a fila).

## Como a coleta acompanha os preços

O ML aposentou `/sites/MLB/search` e `/items` para apps comuns — os dois devolvem
403 do policy agent mesmo com token válido e permissões concedidas. A coleta usa
o **catálogo**, que continua aberto:

1. **Descoberta** — `/products/search?q=` devolve produtos de catálogo já com
   nome e fotos em resolução cheia. É como um produto entra no radar.
2. **Preço** — `/products/{id}/items` lista os anúncios daquele produto, com
   preço. Um celular típico tem 27 anúncios de vendedores diferentes, de
   R$ 1.289 a R$ 1.899.
3. **Acompanhamento** — a cada rodada, os produtos já no banco são reconsultados
   pelo passo 2. Nome e foto vêm do banco, então é **uma chamada por produto**.

A unidade de rastreio é o **produto de catálogo**, não o anúncio — e isso é
melhor: o preço registrado é o **menor entre os anúncios ativos e novos**, que é
o que interessa pro grupo. Anúncio individual some da noite pro dia; o produto
fica. Anúncio usado é descartado, tanto por qualidade quanto porque fica fora
das regras do programa de afiliados.

O passo 3 é o que faz o histórico existir. Sem ele, um produto só acumularia
observação enquanto aparecesse na busca, e quase nada chegaria aos
`MIN_OBSERVATIONS` dias que o filtro exige.

**Custo de API:** `ML_TRACK_LIMIT` (padrão 150) é literalmente quantas
requisições a reconsulta gasta por rodada, já que não há multiget. Com o daemon
de 2h isso dá ~1.800 chamadas/dia. A coleta avisa no log quando trunca.

> O token do OAuth fica na mesma base do histórico (`data/promos.db`). Apagar o
> banco derruba a autorização — é preciso rodar `ml-auth` de novo.

## Link de afiliado — o passo manual

**Não dá para montar link de afiliado do ML.** Um link real do programa é assim:

```
mercadolivre.com.br/social/<seu-nickname>?matt_word=...&matt_tool=...&ref=<blob>
```

O `ref` tem ~150 bytes assinados pelo servidor do ML, e **o ID do produto não
aparece em lugar nenhum da URL** — ele está dentro do blob. Concatenar
`matt_word`/`matt_tool` na URL do produto, que é o que vários projetos por aí
fazem, produz um endereço diferente do que o programa emite. Também não existe
API oficial para gerar esses links.

Então o fluxo tem um passo humano:

```bash
promo pending-links
```

Lista os produtos rastreados sem link, com mais histórico primeiro — os que
estão mais perto de virar post. Gere cada um em
[mercadolivre.com.br/afiliados/linkbuilder](https://www.mercadolivre.com.br/afiliados/linkbuilder)
(só funciona no desktop) e salve:

```bash
promo link MLB3953571145 https://meli.la/xxxxxxx
```

O link curto `meli.la` é o encurtador do próprio ML — pode usar. Encurtador de
terceiros é proibido pelos termos do programa.

Oferta que passa no filtro **sem** link não vira post: o `run` segura ela e
imprime o ID e a URL no log, para você gerar o link e rodar `promo flush`.
Postar sem link seria queimar a oferta por comissão zero.

## Como o filtro decide

`src/promo/scoring.py` é o coração do projeto:

1. **Baseline** = mediana do menor preço diário dos últimos 60 dias, **excluindo
   hoje** (senão a própria queda puxaria a média pra baixo).
2. Exige no mínimo 7 dias distintos de histórico antes de confiar na baseline.
3. Só passa com desconto ≥ 15% contra a baseline.
4. Cooldown de 14 dias por produto, salvo se o desconto melhorou 10 p.p. ou mais.

Tudo ajustável no `.env`. Mediana em vez de média é proposital: aguenta um pico
de preço isolado sem distorcer.

## Janela de 24 horas do WhatsApp

Texto livre só sai se você falou com o número do bot nas últimas 24h. Fora
disso a Meta exige template aprovado — o bot manda um template curto pedindo
que você responda qualquer coisa, o que reabre a janela, e os posts ficam na
fila até lá (`flush` drena).

Crie no WhatsApp Manager um template **utility** chamado `ofertas_ping` com uma
variável no corpo, ex.: `Você tem {{1}} ofertas novas. Responda OK pra receber.`

Custo: dentro da janela é grátis hoje, mas a Meta passa a cobrar mensagens de
serviço a partir de **1º de outubro de 2026**. No volume de um grupo (poucas
mensagens por dia) fica em centavos.

## Antes de ligar isso pra valer

- **Divulgação de afiliado é obrigatória** nos dois programas — o prompt do
  copywriter já força a linha final, não tire.
- Comece com poucos termos no `watchlist.json`. Grupo que posta demais morre.
- O prompt em `src/promo/copywriter.py` proíbe inventar número e criar urgência
  falsa. Se for mexer no tom, mantenha essas duas regras.

## Testes

```bash
.venv/bin/python -m pytest
```

## Estrutura

| Arquivo | O que faz |
|---|---|
| `src/promo/scoring.py` | decide o que é promoção real |
| `src/promo/db.py` | schema, histórico de preços e links de afiliado |
| `src/promo/sources/mercadolivre.py` | OAuth, busca, multiget e link de afiliado |
| `src/promo/sources/amazon.py` | Creators API |
| `src/promo/copywriter.py` | prompt e chamada ao Gemini |
| `src/promo/delivery/whatsapp.py` | Cloud API, imagem + legenda, janela de 24h |
| `src/promo/pipeline.py` | orquestra tudo |
| `src/promo/cli.py` | comandos, incluindo o `daemon` |
| `watchlist.json` | termos monitorados (montado no container, editável sem rebuild) |
| `Dockerfile` / `docker-compose.yml` | imagem e serviço |

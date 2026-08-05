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

**Mercado Livre** — crie o app em [developers.mercadolivre.com.br/devcenter](https://developers.mercadolivre.com.br/devcenter).
O `redirect_uri` do app tem que bater exatamente com `ML_REDIRECT_URI`.

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

O ML só aceita `authorization_code`, então autorize uma vez. A porta precisa
estar publicada porque o callback volta pro seu navegador:

```bash
docker compose run --rm --service-ports amazon_pay ml-auth
```

O container imprime a URL (não há navegador dentro dele) — abra, autorize, e o
token fica salvo em `./data/promos.db`. Não precisa repetir: o `refresh_token`
mantém o acesso vivo.

```bash
docker compose run --rm amazon_pay test-whatsapp
```

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
| `src/promo/db.py` | schema + histórico de preços |
| `src/promo/sources/mercadolivre.py` | OAuth + busca ML |
| `src/promo/sources/amazon.py` | Creators API |
| `src/promo/copywriter.py` | prompt e chamada ao Gemini |
| `src/promo/delivery/whatsapp.py` | Cloud API + janela de 24h |
| `src/promo/pipeline.py` | orquestra tudo |
| `src/promo/cli.py` | comandos, incluindo o `daemon` |
| `watchlist.json` | termos monitorados (montado no container, editável sem rebuild) |
| `Dockerfile` / `docker-compose.yml` | imagem e serviço |

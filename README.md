# amazon_pay — bot de ofertas para grupo de WhatsApp

Monitora preços no Mercado Livre e na Amazon, guarda o histórico, e só posta
quando o preço cai **de verdade** contra a média histórica — não contra o
"de R$X por R$Y" inflado da loja. O Gemini escreve o post e ele sai no grupo.

```
[daemon] → coleta ML/Amazon → SQLite (1 preço/dia) → filtro de desconto real
                                                          ↓
                                            Gemini escreve o post
                                                          ↓
                                        DELIVERY_BACKEND decide o resto
                                     ↙                                ↘
                        evolution: posta no grupo        cloud: manda pra você,
                        (Baileys, não oficial)           você encaminha
```

## Os dois caminhos de entrega

A Cloud API oficial da Meta **não alcança grupo comum**. A Groups API que a
Meta lançou só serve grupo criado pelo próprio bot, com teto de **8
participantes** — não é grupo de ofertas. Então são dois caminhos, e a escolha
é sua:

| `DELIVERY_BACKEND` | O que acontece | O custo |
|---|---|---|
| `cloud` (padrão) | O post chega no seu WhatsApp e você encaminha | Um passo manual por post, mas zero risco e você revisa antes |
| `evolution` | O post cai no grupo sozinho | Roda em cima do Baileys: **viola os Termos da Meta e o número pode ser banido** |

Se for de `evolution`, pareie um **chip secundário**. Não o seu número pessoal
— um ban leva junto o WhatsApp da sua vida, não só o do bot.

## Setup

```bash
cp .env.sample .env
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

Antes de qualquer coisa, meça o que a API ainda entrega **nesta conta**:

```bash
docker compose run --rm amazon_pay ml-probe
```

O ML vem fechando a API pública endpoint por endpoint, sem anunciar e sem
atualizar a doc — `/sites/MLB/search` hoje devolve 403 por política mesmo com
token válido e app registrado, enquanto `/products/search` responde normal.
O `ml-probe` bate em cada candidato com o seu token e diz qual está de pé, pra
a estratégia de coleta sair de medição e não da documentação.

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

O container sobe com `serve`: a API HTTP e o loop de coleta no mesmo processo.
O loop é simples (roda → dorme → repete), não cron dentro da imagem — logs no
stdout e `docker stop` encerra na hora em vez de esperar o sleep terminar.
Intervalo em `RUN_INTERVAL_SECONDS` (padrão 2h).

Um processo só, e não dois, porque dois escrevendo no mesmo SQLite dariam
`database is locked` na primeira coincidência. Quem não quer porta aberta pode
usar `command: ["daemon"]`, que roda o mesmo loop sem HTTP.

Detalhe do compose: o `restart: unless-stopped` faz o container reiniciar em
loop se o `.env` estiver incompleto — o log diz qual variável falta.

## A API

Escuta em `127.0.0.1:8000`, nunca exposta para fora.

| Endpoint | Auth | O quê |
|---|---|---|
| `GET /health` | aberto | Estado do banco, do worker e da última rodada |
| `GET /stats` | `X-API-Key` | Mesmas contagens do `promo stats`, em JSON |
| `POST /run` | `X-API-Key` | Adianta uma rodada fora do intervalo |
| `GET /docs` | aberto | Swagger UI |

```bash
curl -s localhost:8000/health
```

O `/health` responde **503** quando o banco não abre ou quando o worker
deveria estar vivo e não está — é o alvo do healthcheck do compose e serve
direto para um monitor externo. É aberto de propósito e não devolve segredo.

Os outros dois exigem o header `X-API-Key` com o valor de `API_SECRET`. Se essa
variável estiver vazia eles respondem **503**, não abrem: `/run` gasta cota do
Gemini e dispara post no grupo, então a falha é fechada.

Para acessar de um servidor remoto, túnel SSH — nunca publique a porta:

```bash
ssh -L 8000:127.0.0.1:8000 usuario@servidor
```

## Produção

Ver [DEPLOY.md](DEPLOY.md): roteiro de VPS, pareamento por túnel SSH, backup e
custo das opções de hospedagem (incluindo as gratuitas).

## Sem Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev,api]"
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

Ou subir a API local, que já traz o loop junto:

```bash
.venv/bin/amazon_pay serve
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

### Descoberta por categoria

Não existe endpoint de ofertas ou promoções para afiliado — o que o ML expõe
com esse nome (`/seller-promotions/*`) é do lado do **vendedor**, sobre os
anúncios dele. O mais perto disso é `/highlights/{site}/category/{id}`: os mais
vendidos da categoria.

Serve para achar o que você não pensaria em colocar na watchlist. Configure em
`watchlist.json`:

```json
"categories": [
  { "id": "MLB1051", "name": "Celulares e Telefones", "max_price": 2500 }
]
```

Os IDs saem de:

```bash
promo ml-categories
```

**É a via mais cara das duas.** A lista de destaques traz só IDs — sem nome,
sem preço — então cada produto vira duas chamadas (`/products/{id}` para nome e
foto, `/products/{id}/items` para o preço), contra uma na busca por termo. Com o
teto padrão de 10 produtos, cada categoria custa ~21 requisições por rodada.
Comece com uma ou duas.

Um detalhe do formato: a lista mistura `PRODUCT` com `USER_PRODUCT` (4 em 20 em
algumas categorias). `USER_PRODUCT` é anúncio de um vendedor específico, não
produto de catálogo, e `/products/{id}/items` não responde por ele — a coleta
descarta esses antes de gastar chamada.

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

## Link de afiliado

**Não dá para montar link de afiliado do ML.** Um link real do programa é assim:

```
mercadolivre.com.br/social/<seu-nickname>?matt_word=...&matt_tool=...&ref=<blob>
```

O `ref` tem ~150 bytes assinados pelo servidor do ML, e **o ID do produto não
aparece em lugar nenhum da URL** — ele está dentro do blob. Concatenar
`matt_word`/`matt_tool` na URL do produto, que é o que vários projetos por aí
fazem, produz um endereço diferente do que o programa emite. E não existe API
oficial: o ML não expõe nenhuma.

O link curto `meli.la` é o encurtador do próprio ML — pode usar. Encurtador de
terceiros é proibido pelos termos do programa.

### Automático (pelo painel)

O bot usa o mesmo endpoint que o Link Builder do site chama. **Não é API
pública** — é página interna, autenticada por cookie de sessão, e vai quebrar
quando o ML mexer nela. Por isso toda falha aqui é não-fatal: a oferta é
segurada, a rodada continua, e o `promo link` manual segue valendo.

Capture o cookie uma vez: com o painel aberto e logado, DevTools → Network →
qualquer request para `mercadolivre.com.br` → Copy → Copy as cURL, e pegue o
valor do header `Cookie`. No `.env`:

```
ML_AFFILIATE_COOKIE=<o valor do header Cookie>
ML_AFFILIATE_TAG=<seu nickname de afiliado, minúsculo>
```

Esse cookie é **acesso à sua conta inteira**, não só ao painel. Ele mora no
`.env` (que é gitignored) e nunca aparece em log — as mensagens de erro citam
o nome da variável, nunca o valor. Quando expirar, o erro diz para recapturar.

Depois disso, o `run` gera o link sozinho quando a oferta passa no filtro. Para
adiantar os produtos já rastreados, em lote:

```bash
promo link-all
```

O `x-csrf-token` do request **não** é o cookie `_csrf` — é outro valor, que
muda a cada carregamento da página. Por isso o código busca o painel e raspa
o `<meta name="csrf-token">` antes de postar, reaproveitando o token entre
chamadas (a página tem 770 KB).

Nem todo anúncio é elegível: o painel responde `200` mas com
`"URL not allowed in affiliates program"` no item. Isso fica registrado em
`affiliate_blocked` por 30 dias — sem isso o bot perguntaria de novo, para o
mesmo anúncio inelegível, em toda rodada, para sempre.

### Manual (o caminho de volta)

Sem `ML_AFFILIATE_COOKIE` configurado, ou com ele expirado, o fluxo é o de
antes:

```bash
promo pending-links
```

Lista os produtos rastreados sem link, com mais histórico primeiro. Gere em
[mercadolivre.com.br/afiliados/linkbuilder](https://www.mercadolivre.com.br/afiliados/linkbuilder)
e salve:

```bash
promo link MLB3953571145 https://meli.la/xxxxxxx
```

Oferta que passa no filtro **sem** link não vira post: o `run` segura ela e
imprime o ID e a URL no log. Postar sem link seria queimar a oferta por
comissão zero.

## Como o filtro decide

`src/promo/scoring.py` é o coração do projeto:

1. **Baseline** = mediana do menor preço diário dos últimos 60 dias, **excluindo
   hoje** (senão a própria queda puxaria a média pra baixo).
2. Exige no mínimo 7 dias distintos de histórico antes de confiar na baseline.
3. Só passa com desconto ≥ 15% contra a baseline.
4. Cooldown de 14 dias por produto, salvo se o desconto melhorou 10 p.p. ou mais.

Tudo ajustável no `.env`. Mediana em vez de média é proposital: aguenta um pico
de preço isolado sem distorcer.

## Postar direto no grupo (Evolution API)

Só com `DELIVERY_BACKEND=evolution`. Releia o aviso de ban lá em cima antes.

Invente uma `EVOLUTION_API_KEY` no `.env` — ela é a senha do gateway, e quem a
tiver manda mensagem como o número pareado. Depois:

```bash
docker compose up -d evolution
```

```bash
docker compose run --rm amazon_pay wa-connect
```

O `wa-connect` cria a instância e devolve o código de pareamento. No celular do
chip secundário: WhatsApp → Aparelhos conectados → Conectar. O código expira em
menos de um minuto; se perder, rode de novo.

Pareado, descubra o JID do grupo (grupo não tem telefone, tem JID):

```bash
docker compose run --rm amazon_pay wa-groups
```

Cole o `...@g.us` do grupo certo em `EVOLUTION_GROUP_JID` e teste:

```bash
docker compose run --rm amazon_pay test-whatsapp
```

A sessão do WhatsApp mora no volume `evolution_instances`. Apagar esse volume
significa reparear pelo QR. E a porta 8080 fica presa em `127.0.0.1` de
propósito: exposta na rede, ela é acesso total ao WhatsApp pareado.

Neste backend não existe janela de 24h — ela é regra da Cloud API da Meta, e o
Baileys não passa por ela. A fila só acumula por erro de rede ou instância
caída (`NotConnected`, que o log identifica pedindo `wa-connect`).

## Janela de 24 horas do WhatsApp

Só vale no backend `cloud`.

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
| `src/promo/sources/mercadolivre.py` | OAuth, busca de catálogo, preço e link de afiliado |
| `src/promo/sources/ml_linkbuilder.py` | gera o link pelo painel (endpoint interno, frágil) |
| `src/promo/sources/amazon.py` | Creators API |
| `src/promo/copywriter.py` | prompt e chamada ao Gemini |
| `src/promo/delivery/whatsapp.py` | Cloud API, imagem + legenda, janela de 24h |
| `src/promo/delivery/evolution.py` | Baileys via Evolution API, posta direto no grupo |
| `src/promo/pipeline.py` | orquestra tudo |
| `src/promo/cli.py` | comandos, incluindo o `daemon` |
| `watchlist.json` | termos e categorias monitorados (montado no container, editável sem rebuild) |
| `Dockerfile` / `docker-compose.yml` | imagem e serviço |

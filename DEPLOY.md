# Deploy em VPS

Roteiro para tirar o bot da máquina local e deixá-lo rodando sozinho, postando
no grupo sem intervenção.

## O que vai subir

```
┌─ VPS (ou máquina em casa) ────────────────────────┐
│                                                    │
│  amazon_pay ──── SQLite em volume                 │
│  (serve: API + loop de coleta)                    │
│      │                                             │
│      └──▶ evolution ──▶ postgres                  │
│           (Baileys)     (sessão do WhatsApp)      │
│                                                    │
│  Portas: só 127.0.0.1. Nada exposto.              │
└────────────────────────────────────────────────────┘
                     │
                  grupo do WhatsApp
```

Três containers, tudo no `docker-compose.yml`. O `amazon_pay` roda o comando
`serve`: a API HTTP e o loop de coleta no mesmo processo — dois processos
escrevendo no mesmo SQLite dariam `database is locked`.

**Nenhuma porta é publicada na internet.** A API e o painel da Evolution ficam
presos em `127.0.0.1` e você chega neles por túnel SSH. Isso não é excesso de
zelo: a `EVOLUTION_API_KEY` dá controle total do WhatsApp pareado.

## Onde hospedar

Precisa de **2 GB de RAM no mínimo, 4 GB confortável** — a Evolution (Node) e
o Postgres são o peso; o bot em si é leve.

| Opção | RAM | Custo/mês | Nota |
|---|---|---|---|
| Oracle Always Free (São Paulo) | 24 GB (ARM) | **R$ 0** | Cota ARM costuma estar esgotada; confira se a imagem da Evolution tem tag `arm64` |
| Raspberry Pi 5 em casa | 4–8 GB | ~R$ 5 de luz | IP residencial brasileiro — menor risco de ban |
| Notebook velho em casa | — | ~R$ 15 de luz | Mesmo benefício de IP |
| Hostinger VPS BR | 4 GB | ~R$ 30–60 | |
| AWS Lightsail sa-east-1 | 2–4 GB | ~US$ 10–20 | |
| Vultr São Paulo | 2–4 GB | ~US$ 12–24 | |

Prefira **região São Paulo** ou máquina em casa. A Evolution roda sobre
Baileys, e parear um chip brasileiro a partir de um IP de datacenter
estrangeiro é um sinal a mais para o antifraude da Meta.

Distro: Ubuntu 24.04 LTS.

## 1. Preparar o servidor

Docker e o plugin do compose:

```bash
curl -fsSL https://get.docker.com | sh
```

```bash
sudo usermod -aG docker $USER && sudo systemctl enable --now docker
```

Reabra a sessão SSH para o grupo `docker` valer.

Firewall — só a 22 precisa entrar, porque todo o resto é loopback:

```bash
sudo ufw allow 22/tcp && sudo ufw --force enable
```

> No Oracle Cloud há um segundo firewall na console (Security List / NSG), além
> do `ufw`. Como nada além do SSH precisa entrar, não é necessário abrir nada
> lá — a configuração padrão já serve.

## 2. Clonar e configurar

```bash
git clone https://github.com/leonardoopa/amazon_pay.git && cd amazon_pay
```

O `.env` não vem do git. Copie o seu da máquina local:

```bash
scp .env usuario@IP_DO_SERVIDOR:~/amazon_pay/.env
```

```bash
chmod 600 .env
```

No servidor, ajuste três coisas no `.env`:

```ini
DELIVERY_BACKEND=evolution
EVOLUTION_BASE_URL=http://evolution:8080
API_SECRET=<gere com: openssl rand -hex 32>
```

O `EVOLUTION_BASE_URL` muda porque, dentro da rede do compose, o host é o nome
do serviço — não `localhost`.

## 3. Build e autorização do Mercado Livre

```bash
docker compose build
```

O ML só aceita `authorization_code`, então autorize uma vez. É interativo por
colagem e funciona direto no SSH:

```bash
docker compose run --rm amazon_pay ml-auth
```

O comando imprime uma URL. Abra no seu navegador, autorize, e copie a URL
inteira para onde o navegador te redirecionou — a página não carrega nada útil,
e tudo bem, o que importa está na barra de endereços. Cole no terminal. O
`refresh_token` mantém o acesso vivo daí em diante.

## 4. Parear o WhatsApp

> **Atenção antes de parear.** A Evolution roda sobre Baileys, que é cliente
> não oficial. Isso viola os Termos da Meta e o número pareado pode ser banido.
> Use um chip secundário, nunca o seu número pessoal — um ban leva junto o
> WhatsApp da sua vida, não só o do bot.

Suba só a Evolution e o banco dela:

```bash
docker compose up -d evolution postgres
```

O QR se renova a cada ~30s, então salvar num arquivo não funciona: o código
morre antes de você apontar a câmera. Use o painel da Evolution, que renova
sozinho na tela. Da **sua máquina**, abra o túnel:

```bash
ssh -L 8080:127.0.0.1:8080 usuario@IP_DO_SERVIDOR
```

Com o túnel aberto, acesse `http://localhost:8080/manager` no navegador. Server
URL é `http://localhost:8080`, API Key é o valor de `EVOLUTION_API_KEY`. Clique
na instância `ofertas` e conecte. No celular do chip secundário: Aparelhos
conectados → Conectar aparelho.

Pareado, pegue o JID do grupo:

```bash
docker compose run --rm amazon_pay wa-groups --search "nome do grupo"
```

Copie o valor (termina em `@g.us`) para `EVOLUTION_GROUP_JID` no `.env`.

## 5. Subir

```bash
docker compose up -d
```

```bash
docker compose logs -f
```

Daqui em diante é automático: o loop roda a cada `RUN_INTERVAL_SECONDS`
(padrão 2h), e os posts saem no grupo espaçados por `DRIP_INTERVAL_SECONDS`
(padrão 150s, com jitter) para não parecer rajada de robô.

### Os primeiros dias

A baseline precisa de `MIN_OBSERVATIONS` dias distintos (padrão 7) antes de
confiar na mediana. Nesse período vale rodar só a coleta:

```bash
docker compose run --rm amazon_pay daemon --collect-only
```

## 6. A API

Fica em `127.0.0.1:8000` no servidor. Para acessar de fora, túnel SSH:

```bash
ssh -L 8000:127.0.0.1:8000 usuario@IP_DO_SERVIDOR
```

| Endpoint | Auth | O quê |
|---|---|---|
| `GET /health` | aberto | Estado do banco, do worker e da última rodada |
| `GET /stats` | `X-API-Key` | Mesmas contagens do `promo stats`, em JSON |
| `POST /run` | `X-API-Key` | Adianta uma rodada fora do intervalo |
| `GET /docs` | aberto | Swagger UI |

O `/health` é aberto de propósito — é ele que o healthcheck do compose
consulta, e não devolve segredo nenhum. Responde **503** quando o banco não
abre ou quando o worker deveria estar vivo e não está, então serve direto como
alvo de um monitor externo (UptimeRobot, Healthchecks.io).

```bash
curl -s localhost:8000/health
```

```bash
curl -s -H "X-API-Key: $API_SECRET" localhost:8000/stats
```

`/stats` e `/run` respondem **503** se `API_SECRET` estiver vazio. É falha
fechada de propósito: `/run` gasta cota do Gemini e dispara post no grupo.

## 7. Backup

Duas coisas insubstituíveis:

- `./data/promos.db` — o histórico de preço. Perdeu, volta a esperar 7 dias de
  observação antes de postar de novo.
- Volumes `evolution_instances` e `evolution_postgres` — a sessão pareada.
  Perdeu, repareia o chip.

```bash
sqlite3 data/promos.db ".backup '/tmp/promos-backup.db'"
```

```bash
docker run --rm -v evolution_postgres:/v -v $(pwd):/out alpine tar czf /out/evolution-pg.tar.gz -C /v .
```

Coloque isso num cron diário e mande para fora da máquina.

## 8. Operação

Atualizar:

```bash
git pull && docker compose up -d --build
```

**Rotação de log** já vem configurada no compose (10 MB × 3 arquivos por
serviço). Sem isso, rodando 24/7, o `json-file` do Docker encheria o disco em
alguns meses — e disco cheio derruba os três containers de uma vez.

**O cookie do Mercado Livre expira.** O `ML_AFFILIATE_COOKIE` é sessão de
navegador e cai de tempos em tempos. Quando os links de afiliado pararem de ser
gerados, recapture no DevTools com o painel logado, atualize o `.env` e:

```bash
docker compose up -d
```

Enquanto isso, o bot volta ao fluxo manual do `promo link` em vez de quebrar.

**Reboot.** O `restart: unless-stopped` no compose e o `systemctl enable docker`
do passo 1 fazem tudo voltar sozinho.

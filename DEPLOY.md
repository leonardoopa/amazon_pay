# Deploy em VPS

Roteiro para tirar o bot da máquina local e deixá-lo rodando sozinho, postando
no grupo sem intervenção.

## O que vai subir

```
┌─ VPS (ou máquina em casa) ────────────────────────┐
│                                                    │
│  amazon_pay ──── SQLite em volume ────┐           │
│  (serve: API + loop de coleta)         │ leitura   │
│      │                                 │           │
│      └──▶ evolution ──▶ postgres       │           │
│           (Baileys)     (sessão)       │           │
│                                        ▼           │
│                          web (gunicorn, Django)   │
│                                  ▲                 │
│  Loopback só: API 8000, painel 8080.               │
└──────────────────────────────────│─────────────────┘
                     │             │ 80/443
                  grupo do      caddy (TLS)
                  WhatsApp         │
                                visitante
```

Cinco containers, tudo no `docker-compose.yml`. O `amazon_pay` roda o comando
`serve`: a API HTTP e o loop de coleta no mesmo processo — dois processos
escrevendo no mesmo SQLite dariam `database is locked`. O `web` serve a landing
lendo o **mesmo** `promos.db`, só que sem escrever nele.

**Só o Caddy publica porta na internet** (80 e 443), porque o site precisa ser
alcançável. A API e o painel da Evolution seguem presos em `127.0.0.1`, e você
chega neles por túnel SSH. Isso não é excesso de zelo: a `EVOLUTION_API_KEY` dá
controle total do WhatsApp pareado.

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

Firewall — SSH, mais 80 e 443 para o site. Todo o resto é loopback:

```bash
sudo ufw allow 22/tcp && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw --force enable
```

> No Oracle Cloud há um segundo firewall na console (Security List / NSG), além
> do `ufw`. Abra 80 e 443 lá também, senão o Caddy nem consegue validar o
> domínio para emitir o certificado.

A 80 continua necessária mesmo com o site em HTTPS: é por ela que o Let's
Encrypt confirma que o domínio é seu, e é dela que o Caddy redireciona para a
443.

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

No servidor, ajuste o bloco do bot no `.env`:

```ini
DELIVERY_BACKEND=evolution
EVOLUTION_BASE_URL=http://evolution:8080
API_SECRET=<gere com: openssl rand -hex 32>
```

O `EVOLUTION_BASE_URL` muda porque, dentro da rede do compose, o host é o nome
do serviço — não `localhost`.

E o bloco do site, que **precisa** ser ajustado antes de subir — o `web` se
recusa a iniciar sem chave e sem domínio, em vez de subir inseguro:

```ini
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<gere com: openssl rand -hex 32>
DJANGO_ALLOWED_HOSTS=ofertas.seudominio.com.br
DJANGO_CSRF_TRUSTED_ORIGINS=https://ofertas.seudominio.com.br
DJANGO_HTTPS=1
SITE_ADDRESS=ofertas.seudominio.com.br
```

Sem domínio ainda? Use `SITE_ADDRESS=:80`, `DJANGO_HTTPS=0` e
`DJANGO_ALLOWED_HOSTS=<IP do servidor>`. Serve para conferir o site pelo IP,
mas **não divulgue esse endereço**: sem TLS, o e-mail digitado no formulário
trafega em texto claro e o WhatsApp mostra aviso de link não seguro.

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

Sobem os cinco: bot, Evolution, Postgres, site e Caddy. Daqui em diante é
automático: o loop roda a cada `RUN_INTERVAL_SECONDS` (padrão 2h), e os posts
saem no grupo espaçados por `DRIP_INTERVAL_SECONDS` (padrão 150s, com jitter)
para não parecer rajada de robô. O site fica no ar em `SITE_ADDRESS` — detalhes
no passo 7.

### Os primeiros dias

A baseline precisa de `MIN_OBSERVATIONS` dias distintos (padrão 7) antes de
confiar na mediana. Nesse período vale rodar só a coleta:

```bash
docker compose run --rm amazon_pay daemon --collect-only
```

Enquanto a mediana não amadurece, o bot também repassa oferta da vitrine do
Mercado Livre para o grupo não ficar mudo. Nesse repasse o desconto é contra o
preço riscado da loja, não contra medição nossa — o post vai marcado
(`verified=0`), e o site trata os dois diferente: repasse nunca vira produto em
destaque, não entra na esteira, não conta na economia somada, e o card dele diz
**“preço da loja”** em vez de “média”. `promo stats` separa os dois em
`posts_sent` e `posts_sent_verified`.

**Post velho é descartado.** O texto de cada post carrega um preço com hora, e
o gotejamento pode segurá-lo na fila. Passando de `POST_MAX_AGE_MINUTES`
(padrão 60), ele sai como `expired` em vez de ir para o grupo — mandar um valor
que o link não confirma mais custa mais do que a oferta perdida. Se
`posts_expired` no `/stats` crescer, a coleta está produzindo mais rápido do
que a entrega drena: baixe `MAX_OFFERS_PER_RUN` ou `DRIP_INTERVAL_SECONDS`.

## 6. A API

Fica em `127.0.0.1:8000` no servidor. Para acessar de fora, túnel SSH:

```bash
ssh -L 8000:127.0.0.1:8000 usuario@IP_DO_SERVIDOR
```

| Endpoint | Auth | O quê |
|---|---|---|
| `GET /health` | aberto | Estado do banco, do worker e se a última rodada falhou |
| `GET /stats` | `X-API-Key` | Contagens do `promo stats` e o texto do último erro |
| `POST /run` | `X-API-Key` | Adianta uma rodada fora do intervalo |
| `GET /docs` | aberto | Swagger UI |

O `/health` é aberto de propósito — é ele que o healthcheck do compose
consulta, e não devolve segredo nenhum. Responde **503** quando o banco não
abre ou quando o worker deveria estar vivo e não está, então serve direto como
alvo de um monitor externo (UptimeRobot, Healthchecks.io).

Ele diz **se** a última rodada falhou (`last_run_failed`), não o quê. Texto de
exceção carrega URL interna, caminho de arquivo e às vezes o JID do grupo, e
num endpoint aberto isso é reconhecimento de graça. O texto sai no `/stats`,
que exige a chave, e no log do container.

```bash
curl -s localhost:8000/health
```

```bash
curl -s -H "X-API-Key: $API_SECRET" localhost:8000/stats
```

`/stats` e `/run` respondem **503** se `API_SECRET` estiver vazio. É falha
fechada de propósito: `/run` gasta cota do Gemini e dispara post no grupo.

## 7. O site (landing + vitrine)

O `docker compose up -d` do passo 5 já subiu `web` e `caddy`. O `web` roda da
mesma imagem do bot — só troca o comando — e migra o banco do site sozinho
antes de servir.

**Aponte o DNS antes de subir com domínio.** Um registro A do subdomínio para o
IP do servidor. O Let's Encrypt valida pelo próprio domínio, e tentativa
falhada conta no limite semanal dele:

```bash
dig +short ofertas.seudominio.com.br
```

Confira se subiu:

```bash
docker compose logs -f web caddy
```

```bash
curl -s https://ofertas.seudominio.com.br/saude/
```

O `/saude/` devolve os contadores e diz se o banco do bot está acessível — é o
alvo do healthcheck e serve para monitor externo.

### Cadastrar o grupo do WhatsApp

O site não inventa link de convite: sem grupo cadastrado, o botão principal
vira "avise-me quando abrir vaga". Crie o acesso ao admin:

```bash
docker compose exec web python web/manage.py createsuperuser
```

Depois entre em `https://ofertas.seudominio.com.br/admin/` e cadastre o grupo
com o link `chat.whatsapp.com` gerado dentro do próprio grupo. `capacidade` é o
limite do WhatsApp (1024) e `membros` é quanto já entrou — é essa razão que
desenha a barra de ocupação e marca o grupo como lotado. Hoje esse número é
manual: quando um grupo encher, atualize aqui e cadastre o próximo.

### Dados de demonstração no site no ar

A vitrine que rola (`#prova`) mostra três produtos com filme feito à mão. Se a
coleta ainda não tem esses produtos, semeie **só o catálogo**:

```bash
docker compose exec web python web/manage.py semear_demo --so-catalogo
```

O modo cheio (`semear_demo` sem argumento) também cria grupos e posts marcados
como enviados — preço inventado no feed e nos contadores do site. Com
`DJANGO_DEBUG=0` o comando recusa fazer isso, e só passa com `--forcar`.

### Imagem do preview de link

É o que decide o clique quando alguém cola o link no WhatsApp. A ordem é:
arte própria em `web/vitrine/static/vitrine/img/social.jpg`, se existir; senão
a foto do produto em destaque; senão o pôster do vídeo. Para usar arte própria,
salve um **JPG 1200×630, abaixo de 300 KB** nesse caminho e rebuilde — acima
disso o WhatsApp desiste de baixar e mostra o link sem imagem.

Depois de qualquer troca de texto ou imagem de preview, o WhatsApp guarda o
que já baixou por horas. Para conferir o resultado na hora, use o depurador do
Facebook (`developers.facebook.com/tools/debug`) e peça "Scrape Again".

### Atualizar o site

```bash
docker compose up -d --build web
```

O `collectstatic` roda no build, com hash no nome de cada arquivo. Por isso
navegador nenhum serve CSS velho depois do deploy — e por isso o build é
obrigatório: `up -d` sozinho reaproveita a imagem antiga.

## 8. Backup

Três coisas insubstituíveis:

- `./data/promos.db` — o histórico de preço. Perdeu, volta a esperar 7 dias de
  observação antes de postar de novo.
- `./data/site.db` — grupos cadastrados e e-mails de quem pediu aviso de vaga.
  Grupo se recadastra em minutos; a lista de e-mails, não.
- Volumes `evolution_instances` e `evolution_postgres` — a sessão pareada.
  Perdeu, repareia o chip.

Os quatro alvos estão no script:

```bash
scripts/backup.sh
```

Ele usa `.backup` do SQLite, e não `cp`: com o bot escrevendo durante a cópia,
um `cp` pode gravar arquivo inconsistente — e backup que não abre só é
descoberto no dia em que ele é a única coisa que sobrou. Guarda em
`./backups/<data>`, empacota os volumes da Evolution e apaga o que tem mais de
14 dias (disco cheio derruba os containers todos de uma vez).

No cron, diário às 3h:

```bash
(crontab -l 2>/dev/null; echo "0 3 * * * cd ~/amazon_pay && scripts/backup.sh >> backups/backup.log 2>&1") | crontab -
```

**Mande para fora da máquina depois.** Backup no mesmo disco não protege do
caso mais comum, que é a VPS sumir.

## 9. Operação

Atualizar:

```bash
git pull && docker compose up -d --build
```

O `up -d --build` **reconstrói a imagem que o bot e o site compartilham**, então
os dois reiniciam juntos: um ajuste de CSS derruba a coleta por alguns
segundos. Sem consequência (o loop retoma no próximo intervalo e a fila é
persistente), mas evite fazer isso no meio de uma drenagem. Para tocar só o
site: `docker compose up -d --build web`.

**Migração do banco do bot é automática.** O `init_db` aplica as colunas novas
(`verified` entre elas) na subida, sem passo manual. Banco antigo herda
`verified=1` em tudo que já existia — não há como separar retroativamente, e
marcar tudo como repasse apagaria a prova real que existe.

**Rotação de log** já vem configurada no compose (10 MB × 3 arquivos por
serviço). Sem isso, rodando 24/7, o `json-file` do Docker encheria o disco em
alguns meses — e disco cheio derruba todos os containers de uma vez.

**O cookie do Mercado Livre expira.** O `ML_AFFILIATE_COOKIE` é sessão de
navegador e cai de tempos em tempos. Quando os links de afiliado pararem de ser
gerados, recapture no DevTools com o painel logado, atualize o `.env` e:

```bash
docker compose up -d
```

Enquanto isso, o bot volta ao fluxo manual do `promo link` em vez de quebrar.

**Reboot.** O `restart: unless-stopped` no compose e o `systemctl enable docker`
do passo 1 fazem tudo voltar sozinho.

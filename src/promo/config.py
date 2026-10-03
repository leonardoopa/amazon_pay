"""Configuracao lida do .env. Falha cedo e com mensagem clara quando falta credencial."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]


class MissingConfig(RuntimeError):
    """Levantada quando uma credencial obrigatoria nao esta no .env."""


def _get(key: str, default: str | None = None) -> str:
    value = os.getenv(key, default)
    if value is None or value == "":
        raise MissingConfig(f"Falta {key} no .env (copie de .env.sample)")
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key) or default


def _int(key: str, default: int) -> int:
    raw = os.getenv(key)
    return int(raw) if raw else default


@dataclass(frozen=True)
class MercadoLivreConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    site_id: str
    # Sessao do painel de afiliados. Vazios = geracao automatica desligada, e o
    # fluxo volta a ser o `promo link` manual. Nao sao obrigatorios porque
    # cookie expira, e cookie expirado nao pode derrubar a coleta junto.
    affiliate_cookie: str = ""
    affiliate_tag: str = ""

    @classmethod
    def load(cls) -> "MercadoLivreConfig":
        return cls(
            client_id=_get("ML_CLIENT_ID"),
            client_secret=_get("ML_CLIENT_SECRET"),
            redirect_uri=_optional("ML_REDIRECT_URI", "http://localhost:8123/callback"),
            site_id=_optional("ML_SITE_ID", "MLB"),
            affiliate_cookie=_optional("ML_AFFILIATE_COOKIE"),
            affiliate_tag=_optional("ML_AFFILIATE_TAG"),
        )


@dataclass(frozen=True)
class AmazonConfig:
    client_id: str
    client_secret: str
    token_endpoint: str
    marketplace: str
    partner_tag: str

    @classmethod
    def load(cls) -> "AmazonConfig":
        return cls(
            client_id=_get("AMAZON_CLIENT_ID"),
            client_secret=_get("AMAZON_CLIENT_SECRET"),
            token_endpoint=_get("AMAZON_TOKEN_ENDPOINT"),
            marketplace=_optional("AMAZON_MARKETPLACE", "www.amazon.com.br"),
            partner_tag=_get("AMAZON_PARTNER_TAG"),
        )


@dataclass(frozen=True)
class WhatsAppConfig:
    phone_number_id: str
    token: str
    to: str
    ping_template: str
    template_lang: str

    @classmethod
    def load(cls) -> "WhatsAppConfig":
        return cls(
            phone_number_id=_get("WHATSAPP_PHONE_NUMBER_ID"),
            token=_get("WHATSAPP_TOKEN"),
            to=_get("WHATSAPP_TO"),
            ping_template=_optional("WHATSAPP_PING_TEMPLATE", "ofertas_ping"),
            template_lang=_optional("WHATSAPP_TEMPLATE_LANG", "pt_BR"),
        )


@dataclass(frozen=True)
class EvolutionConfig:
    """Evolution API -- entrega direto no grupo, por fora da API oficial."""

    base_url: str
    api_key: str
    instance: str
    group_jid: str

    @classmethod
    def load(cls) -> "EvolutionConfig":
        return cls(
            base_url=_optional("EVOLUTION_BASE_URL", "http://localhost:8080"),
            api_key=_get("EVOLUTION_API_KEY"),
            instance=_optional("EVOLUTION_INSTANCE", "ofertas"),
            # Sai do `promo wa-groups`; termina em @g.us. Nao e o numero do grupo,
            # e o JID -- grupo nao tem numero de telefone.
            group_jid=_get("EVOLUTION_GROUP_JID"),
        )


def grupos_fonte_intervalo_horas() -> float:
    """Horas entre leituras dos grupos usados como fonte de descoberta.

    Cada leitura sao 50 mensagens da Evolution mais uma pagina de ~370 KB do
    ML por link -- o link de afiliado nao carrega o ID do anuncio na URL, so
    no corpo.

    O padrao virou 0.25 h em 09/09/2026, que e menos que o intervalo da rodada
    (`RUN_INTERVAL_SECONDS=900`): assim a leitura acontece em TODA rodada, e o
    atraso entre eles postarem e nos postarmos passa a ser o da rodada, nao a
    soma dos dois. O pedido foi republicar assim que aparecer.

    O trafego nao cresce na mesma proporcao: os links repetem entre leituras --
    eles postam ~28 por dia e a janela lida sao 50 mensagens --, e o teto de
    `limite` por grupo continua valendo.

    0 desliga a fonte.
    """
    return float(_optional("GRUPOS_FONTE_INTERVALO_HORAS", "0.25"))


def delivery_backend() -> str:
    """'evolution' (posta no grupo) ou 'cloud' (manda pra voce encaminhar)."""
    return _optional("DELIVERY_BACKEND", "cloud").strip().lower()


@dataclass(frozen=True)
class Rules:
    min_discount_pct: float
    baseline_window_days: int
    min_observations: int
    repost_cooldown_days: int
    max_offers_per_run: int
    # Economia minima em reais. Porcentagem sozinha nao ve dinheiro: num
    # produto de R$ 237 um desconto de 0,8% e uma promocao de DOIS REAIS, e o
    # grupo recebeu exatamente isso -- "Tasty Whey 3w Gourmet, de R$ 239,00 por
    # R$ 237,00". Este piso e absoluto e NAO e afrouxado por tema prioritario:
    # e justamente com `PRIORITY_MIN_DISCOUNT_PCT=0` que o caso passou.
    min_discount_brl: float = 0.0
    # Piso proprio da oferta VERIFICADA. Vazio usa `min_discount_pct`.
    #
    # A verificada e a unica que afirma "acompanhamos o preco e ele caiu", e
    # essa frase precisa de um numero que a sustente: -0,8% com a mediana ao
    # lado nao sustenta. O piso separado tambem sobrevive ao afrouxamento por
    # tema, porque `regras_do_tema` so mexe em `min_discount_pct` -- que e o
    # que se quer, ja que foi `PRIORITY_MIN_DISCOUNT_PCT=0` que deixou passar
    # o Tasty Whey de dois reais.
    min_discount_pct_verified: float | None = None

    @classmethod
    def load(cls) -> "Rules":
        return cls(
            min_discount_pct=float(_optional("MIN_DISCOUNT_PCT", "15")),
            baseline_window_days=_int("BASELINE_WINDOW_DAYS", 60),
            min_observations=_int("MIN_OBSERVATIONS", 7),
            repost_cooldown_days=_int("REPOST_COOLDOWN_DAYS", 14),
            max_offers_per_run=_int("MAX_OFFERS_PER_RUN", 5),
            min_discount_brl=float(_optional("MIN_DISCOUNT_BRL", "0")),
            min_discount_pct_verified=(
                float(bruto)
                if (bruto := _optional("MIN_DISCOUNT_PCT_VERIFIED", ""))
                else None
            ),
        )


def db_path() -> Path:
    raw = _optional("DB_PATH", "data/promos.db")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def gemini_min_interval_seconds() -> float:
    """Intervalo minimo entre duas chamadas ao Gemini.

    O free tier limita por minuto alem do limite diario. 5s da 12 chamadas por
    minuto, folgado sob os 15 RPM tipicos do flash. Com faturamento ativo o
    teto sobe muito e isso pode ir a 0.
    """
    return float(_optional("GEMINI_MIN_INTERVAL_SECONDS", "5"))


def copy_model() -> str:
    return _optional("GEMINI_MODEL", "gemini-3.5-flash-lite")


def gemini_api_key() -> str:
    return _get("GEMINI_API_KEY")


def run_interval_seconds() -> int:
    """Intervalo do modo daemon (usado pelo container)."""
    return _int("RUN_INTERVAL_SECONDS", 7200)


def api_host() -> str:
    """Interface onde a API escuta.

    Padrao loopback de proposito. Dentro do container precisa ser 0.0.0.0 pra
    a porta publicada funcionar -- o Dockerfile define isso. Fora dele, ligar
    em 0.0.0.0 sem querer exporia o /run pra rede local.
    """
    return _optional("API_HOST", "127.0.0.1")


def api_port() -> int:
    return _int("API_PORT", 8000)


def api_worker() -> bool:
    """Se o processo da API tambem roda o loop de coleta.

    Desligar so faz sentido pra subir uma instancia que apenas responde HTTP,
    ao lado de um `daemon` separado. Com SQLite, nunca ligue os dois ao mesmo
    tempo apontando pro mesmo arquivo.
    """
    return _optional("API_WORKER", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def api_secret() -> str:
    """Segredo do header X-API-Key, exigido pelos endpoints que nao sao /health.

    Vazio nao libera: os endpoints protegidos respondem 503. Falha fechada,
    porque `/run` gasta cota do Gemini e dispara post no grupo.
    """
    return _optional("API_SECRET")


def drip_interval_seconds() -> float:
    """Segundos entre um post e o proximo, antes do jitter.

    Referencia: os grupos de oferta que funcionam postam de 2 a 3 minutos.
    Rajada e lida como flood pelo grupo e como robo pelo antifraude da Meta.
    """
    return float(_optional("DRIP_INTERVAL_SECONDS", "150"))


def entrega_continua() -> bool:
    """Se uma thread propria goteja a fila, independente da rodada de coleta.

    Ligada por padrao. Existe porque a entrega vivia DENTRO da rodada: o grupo
    recebia uma rajada no fim de cada uma e ficava mudo o resto do tempo. Medido
    em 02/10/2026, o Geral mandou 12 posts em 17 minutos (14h47 a 15h04) e nada
    depois disso: a coleta leva 35 a 45 minutos, e os 36 posts que esperavam
    expiraram antes da proxima drenagem.

    So vale para o backend 'evolution'. 'false' devolve a drenagem de dentro da
    rodada, como era.
    """
    return _optional("ENTREGA_CONTINUA", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def fonte_rapida() -> bool:
    """Se os grupos-fonte `prioridade: true` ganham um ciclo proprio e curto.

    Ligada por padrao. A rodada completa leva de 35 a 56 minutos (coleta do ML,
    leitura do xet e do Economizei), e uma oferta do #BVA que chega no comeco
    dela so vira post no fim. O ciclo rapido le SO os grupos prioritarios, a cada
    `FONTE_RAPIDA_INTERVALO_SEGUNDOS`, e enfileira o que for novo.

    Depende da entrega continua: sem a thread que goteja, o post enfileirado so
    sairia no fim da rodada completa e o ciclo rapido nao adiantaria nada.
    """
    return _optional("FONTE_RAPIDA", "true").strip().lower() not in {
        "0",
        "false",
        "no",
    }


def fonte_rapida_intervalo_segundos() -> float:
    """Segundos entre duas leituras do ciclo rapido. Padrao: 3 minutos.

    O #BVA posta cerca de uma mensagem a cada 6 minutos, entao 180 s pega cada
    uma em meia leitura. Cada ciclo custa um pedido a Evolution e dois ao Pelando
    por link NOVO -- os ja resolvidos ficam em cache.
    """
    return float(_optional("FONTE_RAPIDA_INTERVALO_SEGUNDOS", "180"))


def tematico_gap_seconds() -> float:
    """Intervalo minimo entre dois posts do MESMO grupo tematico.

    O Geral tem o teto por hora (`MAX_DO_GERAL_POR_HORA`); os tematicos nao
    tinham teto nenhum, so a fila curta. Com a entrega continua eles passam a ter
    este piso. 900 s e no maximo 4 por hora por grupo, que e o que Mulheres (4,1
    por hora) e Esportes (3,4) ja faziam na media de 72 horas.
    """
    return float(_optional("TEMATICO_GAP_SEGUNDOS", "900"))


def quiet_window() -> tuple[time, time] | None:
    """Faixa do dia em que o grupo recebe muito menos post.

    De madrugada o post nao e lido: ele so acorda gente e fica no topo da tela
    ate de manha, quando ja envelheceu. Continuar postando nesse horario gasta
    cota do Gemini e queima a paciencia do grupo pelo mesmo alcance.

    Formato `HH:MM`, hora local do servidor (o Dockerfile fixa
    TZ=America/Sao_Paulo). A faixa pode cruzar a meia-noite, que e o caso
    normal: 23:30 as 07:30.

    Vazio em qualquer um dos dois desliga a janela.
    """
    # `os.getenv` direto, e nao `_optional`: aquele trata vazio como ausente e
    # devolveria o padrao, deixando a janela ligada justamente para quem a
    # escreveu vazia para desliga-la. Aqui ausente usa o padrao e vazio desliga.
    inicio = os.getenv("QUIET_START", "23:30")
    fim = os.getenv("QUIET_END", "07:30")
    if not inicio or not fim:
        return None
    try:
        return time.fromisoformat(inicio), time.fromisoformat(fim)
    except ValueError:
        # Horario escrito errado nao pode silenciar o bot em silencio: sem esta
        # guarda, um `QUIET_START=23h30` viraria "janela desligada" sem aviso.
        log.warning(
            "QUIET_START/QUIET_END invalidos (%r, %r); janela ignorada. Use HH:MM.",
            inicio,
            fim,
        )
        return None


def quiet_drip_multiplier() -> float:
    """Quanto o intervalo entre posts estica dentro da janela.

    6 x 150s = 900s, acima do orcamento de drenagem de uma rodada -- na pratica
    um post por rodada, contra os cinco de fora da janela.
    """
    return float(_optional("QUIET_DRIP_MULTIPLIER", "6"))


def quiet_max_offers_per_run() -> int:
    """Teto de ofertas novas por rodada dentro da janela.

    Sem ele a coleta seguiria produzindo no ritmo normal enquanto a entrega
    anda devagar, e a diferenca inteira morreria como `expired` de manha --
    chamada de Gemini e link de afiliado gastos para nada.
    """
    return _int("QUIET_MAX_OFFERS_PER_RUN", 1)


def post_max_age_minutes() -> int:
    """Idade maxima de um post na fila antes de ser descartado.

    O texto de cada post carrega um preco com hora, e o gotejamento pode
    segurar a fila por mais de uma hora. Mandar "R$ 389" quando o link ja abre
    em R$ 459 e pior do que nao mandar nada: queima a confianca que a medicao
    inteira existe para construir.

    0 desliga o descarte -- so faca isso se a fila for curta o tempo todo.
    """
    return _int("POST_MAX_AGE_MINUTES", 60)


def price_focus_max() -> float:
    """Preco ate onde esta o foco do grupo. 0 desliga o foco.

    Nao e teto: produto caro continua entrando. E prioridade -- as vagas da
    rodada vao primeiro para o que esta ate aqui, e o que passa disso entra
    com o que sobrar.

    O motivo e de publico, nao de qualidade. Uma queda real num notebook de
    R$ 3.000 continua sendo real, mas quem le grupo de oferta decide um item de
    R$ 100 na hora e um de R$ 1.000 em dias -- e nesses dias sai do grupo,
    pesquisa e compra por outro link. A comissao e percentual, mas o que ela
    multiplica e a venda que acontece.
    """
    return float(_optional("PRICE_FOCUS_MAX", "0"))


def price_focus_reserve() -> int:
    """Quantas vagas da rodada ficam abertas a QUALQUER preco.

    Sem isto, um dia com muitos candidatos baratos empurraria o produto caro
    para fora todas as vezes, e o grupo perderia a variedade que faz alguem
    ficar. Uma vaga aberta garante que a oferta grande apareca sem dominar.
    """
    return _int("PRICE_FOCUS_RESERVE", 1)


def priority_reserve() -> int:
    """Quantas vagas da rodada ficam guardadas para os temas prioritarios.

    Ranquear so por desconto e por preco entrega a rodada a quem tem o maior
    numero, e o maior numero costuma ser eletronico de nicho: com 132 termos na
    watchlist, cabelo e pele competem contra tudo e perdem quase sempre. Foi o
    que aconteceu na primeira rodada depois de a watchlist crescer -- 252
    produtos novos de beleza entraram na carteira e nenhum apareceu entre as 5
    vagas, que ficaram com -72%, -67% e -54% de outras categorias.

    Publico nao e desconto. As mulheres que entraram no grupo pediram cabelo,
    pele e suplemento; guardar vaga e o que faz o pedido virar post sem ter que
    inflar o desconto de nada.

    0 desliga a reserva. Os temas saem de `priority` no watchlist.json.
    """
    return _int("PRIORITY_RESERVE", 0)


def priority_min_discount_pct() -> float | None:
    """Piso de desconto so para os temas do `priority`. Vazio usa o piso geral.

    Existe porque a conta e outra nesses temas. No geral, desconto pequeno e
    oferta fraca e o piso de 5% esta certo em recusar. Num shampoo de salao que
    o grupo pediu pelo nome, a pessoa ja quer o produto, e -9% num item de
    R$ 400 e mais dinheiro do que -60% num de R$ 25.

    Zero deixa passar qualquer desconto, mas nao "nenhum desconto": o
    `score_campaign` ainda exige preco riscado maior que o atual, entao o post
    nunca inventa uma queda que nao existe.
    """
    bruto = _optional("PRIORITY_MIN_DISCOUNT_PCT", "")
    return float(bruto) if bruto else None


def amazon_partner_tag() -> str:
    """Tag de associado da Amazon, usada no `?tag=` do link.

    Nao e segredo: ela aparece em toda URL que o grupo recebe, e e assim que a
    Amazon atribui a comissao. Fica aqui, e nao no codigo, porque a conta tem
    mais de um ID de rastreamento e trocar o do post nao pode exigir release.

    Vazio desliga o comando `amazon-add` -- link sem tag e trabalho de graca
    para a Amazon.
    """
    return _optional("AMAZON_PARTNER_TAG", "").strip()


def max_por_grupo_tematico() -> int:
    """Quantas ofertas cada grupo tematico recebe por rodada, so para ele.

    Os grupos tematicos -- Mulheres, Esportes, Perfumes, Casa -- ja recebem o
    que o Geral escolhe e que casa com o tema deles. Isso entrega pouco: a cota
    do Geral e disputada pelo catalogo inteiro e quem ganha e o maior desconto,
    que raramente e perfume ou air fryer.

    Este numero e a busca PROPRIA de cada grupo, dentro do catalogo que a
    rodada ja coletou. O que sai por aqui nao passa pelo Geral.

    Custa uma chamada do Gemini por post. Com 4 grupos e 5 por rodada sao ate
    20 posts a mais por rodada, entao subir este numero mexe em cota do Gemini
    e em volume do chip ao mesmo tempo.
    """
    return _int("MAX_POR_GRUPO_TEMATICO", 5)


def reserva_de_prioridade_do_grupo() -> int:
    """Quantas das vagas do grupo tematico sao disputadas so pela prioridade.

    A ordenacao sozinha nao bastava. `_escolhe_para_o_grupo` poe o prioritario
    na frente DENTRO de cada porta, mas as portas correm em sequencia: um item
    qualquer que passa pela primeira (medicao nossa) leva a vaga de um item
    prioritario que so passaria pela segunda (preco riscado). No grupo de
    Perfumes isso e a regra, nao a excecao -- perfume importado quase nunca tem
    baseline medida por nos, e o Natura de sempre tem.

    A reserva e teto, nao piso: sem candidato prioritario a vaga volta para a
    fila normal. Mesmo desenho do `PRIORITY_RESERVE` do Geral.
    """
    return _int("RESERVA_DE_PRIORIDADE_DO_GRUPO", 2)


def max_do_geral_por_hora() -> int:
    """Teto de posts enviados ao Geral por hora. 0 desliga o teto.

    Por HORA, e nao por rodada, porque a rodada nao tem duracao fixa: medido em
    17/09/2026 elas levaram de 1550s a 2679s, contra os 900s do intervalo. Teto
    por rodada viraria um numero diferente a cada hora do dia.

    Existe porque o Geral nao e limitado pelo que escolhe, e sim pelo que a
    drenagem deixa sair: medido em 17/09/2026, em 24h ele criou 497 posts,
    enviou 151 e perdeu 321 na fila por preco velho. Com essa sobra, cortar
    selecao nao tira um envio sequer -- a vaga liberada e ocupada pelo proximo
    da propria fila dele. So a entrega segura o volume.

    Sobre a escala: 160 envios em 24h, media de 6,7 por hora e pico de 18. Um
    teto de 6 corta 24% e um de 7 corta 16% -- e o pico e o que ele derruba.

    O que o teto libera vai para os grupos tematicos, porque o revezamento
    continua percorrendo a fila depois que o Geral sai dela.
    """
    return _int("MAX_DO_GERAL_POR_HORA", 0)


def max_comida_e_bebida_por_run() -> int:
    """Quantas ofertas de comida e bebida dos grupos-fonte entram por rodada,
    mesmo com a fila cheia.

    Pedido do dono em 02/10/2026, depois de uma vodka que o xet postou nao
    chegar ao grupo: bebida e comida tem que aparecer, sempre. A fila cheia era
    o que as barrava -- `MAX_PENDING_QUEUE` conta os posts de TODOS os grupos,
    e com 78 pendentes contra um teto de 30 nenhuma oferta de outro grupo
    entrava, nem a vodka.

    Este numero e o freio dessa excecao. Sem ele, um dia de promocao de
    supermercado furaria a contrapressao da fila inteira. O ritmo de ENVIO nao
    muda: `MAX_DO_GERAL_POR_HORA` e o gotejamento continuam valendo, e o que
    muda e a ordem -- ver `prioridade` em `_prioridade_do_post`.

    0 desliga a excecao.
    """
    return _int("MAX_COMIDA_E_BEBIDA_POR_RODADA", 8)


def max_fonte_prioritaria_por_run() -> int:
    """Quantas ofertas de grupos-fonte `prioridade: true` entram por rodada.

    O freio da excecao, como o de comida e bebida: a fonte prioritaria fura a
    fila cheia, a exclusao e a trava de nome e marca, e um numero baixo aqui
    impede que uma rodada com cem links inunde o grupo. O #BVA posta cerca de 6
    mensagens por hora, entao 20 por rodada nunca e o limite na pratica.
    """
    return _int("MAX_FONTE_PRIORITARIA_POR_RODADA", 20)


# Sentinela de "sem teto" para `MAX_PISTAS_POR_RUN`. Grande o bastante para
# nunca ser o limite -- quem segura passa a ser so o tamanho da fila.
SEM_TETO = 10_000


def max_pistas_por_run() -> int:
    """Quantas ofertas dos grupos-fonte podem entrar numa rodada so.

    O repasse nao disputa a cota normal: o pedido do dono e que o que o xet e o
    Economizei postam do Mercado Livre saia no nosso grupo, "nem que a gente
    passe da quantidade de publicacoes por rodada" (16/09/2026).

    Por isso o padrao virou 0 = SEM TETO. O que continua segurando e a fila
    (`MAX_PENDING_QUEUE`), e isso nao e formalidade: medido em 16/09/2026, de
    480 posts criados em 24h apenas 262 foram enviados e 209 expiraram na fila.
    Selecionar mais nao faz sair mais -- quem decide quanto sai e o gotejamento
    (`DRIP_INTERVAL_SECONDS`), e ele e o mesmo numero que decide o risco de
    banimento no Baileys.

    Um numero positivo volta a ser teto, que foi o regime entre 14 e 16/09.
    """
    valor = _int("MAX_PISTAS_POR_RUN", 0)
    return SEM_TETO if valor <= 0 else valor


def group_invite_url() -> str:
    """Link de entrada no grupo, assinado no fim de cada post.

    Post de grupo circula: membro acha a oferta boa e encaminha para um amigo,
    que le o post inteiro sem ter como entrar. A linha de convite fecha esse
    buraco -- e o unico canal de crescimento que nao custa nada, porque viaja
    junto com uma mensagem que ja ia ser encaminhada.

    Aponta para o `/entrar/` do site, e nao para o `chat.whatsapp.com` direto,
    por dois motivos. O clique fica contado (`Clique`, origem `post`), entao da
    pra responder quanto o grupo cresce por encaminhamento. E o convite mora so
    no cadastro do site: trocar de grupo quando este lotar e editar uma linha no
    backoffice, nao refazer deploy do bot.

    Vazio desliga a assinatura.
    """
    return _optional("GROUP_INVITE_URL", "").strip()


def prefer_official_store() -> bool:
    """Nos temas do `priority`, a oferta sai da loja oficial do ML?

    O mesmo produto tem dezenas de vendedores, e a regra geral -- o mais barato
    -- e a certa quase sempre. Nos temas prioritarios ela e a errada: sao marcas
    de salao e dermocosmetico, o vendedor avulso barato e onde mora a
    falsificacao, e quem responde pelo link e o grupo.

    O custo foi medido em 13 produtos dos temas: 12 tem anuncio de loja oficial
    e o sobrepreco mediano do oficial e ZERO -- em 8 dos 12 o mais barato ja era
    o oficial. Produto sem loja oficial cai na regra geral, nao some.
    """
    return _optional("PREFER_OFFICIAL_STORE", "0").strip().lower() in {
        "1",
        "true",
        "sim",
        "yes",
    }


def priority_ignores_price_focus() -> bool:
    """O foco em preco barato vale dentro da reserva dos temas?

    Desligado, o item caro do tema deixa de perder a vaga para o barato do
    mesmo tema -- que e o que se quer quando o tema inteiro foi escolhido por
    converter, nao por caber no bolso.
    """
    return _optional("PRIORITY_IGNORES_PRICE_FOCUS", "0").strip().lower() in {
        "1",
        "true",
        "sim",
        "yes",
    }


def post_cooldown_minutes() -> int:
    """Silencio minimo entre dois posts do MESMO produto.

    A vitrine do ML devolve o mesmo item rodada apos rodada enquanto ele
    seguir em promocao, e o filtro aprova de novo -- o desconto continua real.
    Do lado do grupo, porem, o segundo post em quinze minutos nao e uma oferta
    nova: e a mesma, repetida. Repeticao e o que faz gente sair de grupo de
    oferta.

    Nao se confunde com a variacao de texto que o `recent_headlines` ja fazia.
    Aquilo evita repetir a FRASE; isto evita repetir o PRODUTO, que e o que o
    leitor percebe mesmo com a frase trocada.

    0 desliga a regra.
    """
    return _int("POST_COOLDOWN_MINUTES", 60)


def family_cooldown_minutes() -> int:
    """Silencio minimo entre dois posts do mesmo produto da MESMA MARCA.

    Um nivel acima do `POST_COOLDOWN_MINUTES`, que olha o anuncio. Este olha o
    produto como o leitor o reconhece: dois anuncios do Whey Isolate Fuse da
    Dark Lab, um de 900g e outro de 1,8kg, sao dois `product_id` e um unico
    produto. A comparacao e por sobreposicao de titulo -- ver
    `db.mesmo_produto` --, e nao por assinatura de N primeiras palavras, que
    errava nos dois sentidos.

    Marca diferente nao colide: quatro wheys de quatro fabricantes continuam
    saindo, porque e a marca no titulo que separa um do outro.

    Producao usa 1440 -- um dia. E o pedido do dono em 12/09/2026: mesmo nome
    e mesma marca so voltam no dia seguinte. Medido sobre os 1.316 posts das
    72 horas anteriores, essa janela corta 18%.

    0 desliga a regra.
    """
    return _int("FAMILY_COOLDOWN_MINUTES", 1440)


def category_cooldown_minutes() -> int:
    """Silencio minimo entre dois posts do mesmo TIPO de produto.

    DESLIGADA desde 12/09/2026, por decisao do dono: ela nao ve marca. A
    assinatura de duas palavras junta "whey protein" da Growth, da Dux e da
    Optimum num balde so, e o pedido e o oposto -- whey varias vezes pode,
    desde que de marcas diferentes. Quem cuida da repeticao agora e o
    `FAMILY_COOLDOWN_MINUTES`, que compara o titulo inteiro.

    Um nivel acima do `POST_COOLDOWN_MINUTES`, que olha o produto e a familia.
    Este olha a assinatura de duas palavras -- "whey protein", "camisetas
    hering", "adidas tenis" -- e existe porque a repeticao que o grupo percebe
    nao e so a do produto igual.

    Medido em producao em 04/09/2026, nos ultimos 200 posts: zero repeticoes de
    `product_id`, 27 de familia, e 48 de assinatura grossa. Cinco wheys
    diferentes em 100 minutos sao cinco produtos para a regra e uma rajada de
    whey para quem le.

    Janela curta de proposito. Os temas prioritarios -- whey, tenis, camiseta
    Hering -- sao justamente os que mais colidem aqui, e uma janela longa
    tiraria do ar o que mais converte. Duas horas espacam a rajada sem sumir
    com o tema: ainda cabem doze posts de tenis por dia.

    0 desliga a regra.
    """
    return _int("CATEGORY_COOLDOWN_MINUTES", 0)


def max_pending_queue() -> int:
    """Teto da fila de posts nao enviados.

    Contrapressao: a coleta produz mais rapido do que a entrega gotejada
    consegue drenar, e sem teto a fila cresce pra sempre -- o grupo passaria a
    receber oferta de ontem como se fosse de agora.
    """
    return _int("MAX_PENDING_QUEUE", 30)


def ofertas_pages() -> int:
    """Paginas da vitrine /ofertas lidas por rodada (~45 produtos cada).

    0 desliga a fonte e o grupo volta a postar so o que a nossa medicao provar.
    """
    return _int("OFERTAS_PAGES", 2)


def full_refetch_interval_hours() -> float:
    """Horas entre reconsultas da carteira INTEIRA."""
    return float(_optional("FULL_REFETCH_INTERVAL_HOURS", "2"))


def hot_interval_minutes() -> float:
    """Minutos entre reconsultas da fatia quente. 0 desliga o nivel rapido."""
    return float(_optional("HOT_INTERVAL_MINUTES", "15"))


def hot_track_limit() -> int:
    """Quantos produtos quentes reconsultar. E o custo por ciclo rapido.

    Tambem e o que o ciclo rapido oferece de candidato a rodada que nao tem
    reconsulta completa nem descoberta. Isso pesa mais do que parece: a vitrine
    do ML repete os mesmos itens todo dia, e com `REPOST_COOLDOWN_DAYS` de 3 ela
    esgota -- medido em producao, 88 das 89 ofertas da vitrine ja tinham post, e
    a rodada selecionou zero. Nessas rodadas o unico catalogo disponivel e este
    teto, entao 25 numa carteira de 900 e o que faz o grupo emudecer.
    """
    return _int("HOT_TRACK_LIMIT", 25)


def hot_margin_pct() -> float:
    """Folga sobre a minima historica pra um produto contar como quente."""
    return float(_optional("HOT_MARGIN_PCT", "10"))


def products_per_keyword() -> int:
    """Candidatos de catalogo por termo da watchlist.

    Medido: ~65% dos produtos que a busca devolve tem ZERO anuncio ativo em
    /products/{id}/items -- sao entradas mortas do catalogo, e `status=active`
    na busca nao filtra isso. Entao pra rastrear N produtos e preciso pedir
    cerca de 3N. Cada candidato custa 1 chamada, morto ou vivo.
    """
    return _int("PRODUCTS_PER_KEYWORD", 20)


def products_per_category() -> int:
    """Idem para os mais vendidos. Cada um custa DUAS chamadas (nome + preco)."""
    return _int("PRODUCTS_PER_CATEGORY", 10)


def discovery_interval_hours() -> int:
    """Horas entre rodadas de descoberta (busca por termo + mais vendidos).

    A descoberta e cara e quase nao muda: o top-10 do catalogo por termo e o
    ranking de mais vendidos de uma categoria sao praticamente os mesmos de uma
    hora pra outra. Rodar ela em toda rodada de 2h gasta o orcamento de API que
    deveria estar mantendo o historico de uma carteira grande -- e carteira
    grande e o unico jeito de ter volume de post.

    0 desliga o intervalo e volta a descobrir em toda rodada.
    """
    return _int("DISCOVERY_INTERVAL_HOURS", 12)


def discovery_terms_per_round() -> int:
    """Termos da watchlist consultados por rodada de descoberta. 0 = todos.

    Existe porque a rodada de descoberta virou silencio no grupo. Medido em
    02/09/2026: 190 termos a 20 candidatos cada, mais 33 categorias, deram
    4.822 chamadas a ~4 por segundo -- 21 minutos entre o inicio da rodada e a
    primeira mensagem, com a fila vazia esperando o tempo todo.

    Fatiar troca "tudo de tres em tres horas" por "um pedaco a cada rodada", o
    que e melhor nas duas pontas: nenhuma rodada passa de poucos minutos, e
    com a descoberta rodando em toda rodada a carteira e reciclada MAIS rapido
    do que antes, nao menos.
    """
    return _int("DISCOVERY_TERMS_PER_ROUND", 0)


def discovery_categories_per_round() -> int:
    """Categorias consultadas por rodada de descoberta. 0 = todas.

    Cada uma custa ~21 chamadas (a lista mais duas por produto), entao 33
    categorias sao 693 chamadas -- quase 3 minutos sozinhas.
    """
    return _int("DISCOVERY_CATEGORIES_PER_ROUND", 0)


def discovery_priority_share() -> float:
    """Fracao da fatia reservada aos termos que casam num tema do `priority`.

    Sao 95 termos prioritarios contra 95 gerais, e sem isto a fatia trataria os
    dois pocos igual. O pedido e o oposto: produto prioritario tem que aparecer
    no grupo com mais frequencia, e frequencia de post comeca em frequencia de
    coleta -- oferta que ninguem reconsultou nao tem como ser escolhida.
    """
    return float(_optional("DISCOVERY_PRIORITY_SHARE", "0.5"))


def track_limit() -> int:
    """Teto de produtos reconsultados por rodada, por fonte.

    O ML aposentou o multiget de anuncios; hoje o preco sai de
    /products/{id}/items, que e **uma chamada por produto**. Entao esse numero
    e literalmente quantas requisicoes a reconsulta gasta. Com o daemon de 2h
    o padrao da ~1.800 chamadas/dia. Suba com parcimonia -- a coleta avisa no
    log quando trunca.
    """
    return _int("ML_TRACK_LIMIT", 150)

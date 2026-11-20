# Assistente do WhatsApp com a foto de cada produto — design

Data: 20/11/2026. Pedido do dono: "quando alguém perguntar sobre algum produto, a
gente manda a imagem desse produto; isso já está salvo no nosso banco". O dono pediu
para não ser consultado depois do brainstorming: as decisões abaixo marcadas como
**decisão nossa** foram tomadas com base no código e nos dados de produção.

## Intenção e porquê

O assistente de busca (privado do chip do grupo, beta fechado) responde hoje com
uma única mensagem de texto: até 3 produtos, cada um com preço, loja, quando foi
visto, veredito contra a mediana e link de afiliado. Em grupo de oferta quem decide
olha a foto primeiro — é assim que os posts do próprio grupo saem (foto + legenda).
A resposta do assistente deve ter a mesma cara: cada produto com a foto dele.

## Estado atual (medido em 20/11/2026, produção, só leitura)

- `products.image_url` está preenchido em **100%** dos 12.292 produtos vistos em 30
  dias: 87% são URL de CDN (`http2.mlstatic.com` 8.951, `media.pelando.com.br` 964,
  `m.media-amazon.com` 518) e 1.856 são arquivo local em `/app/data/fotos/*.jpg`
  (foto baixada do grupo-fonte; 928 MB, 4.954 arquivos). O container do assistente
  monta o mesmo `./data:/app/data`, então enxerga esses arquivos.
- Amostra baixada com o User-Agent do bot (`MEDIA_USER_AGENT`): todas 200, JPEG ou
  WebP, de 7 KB a 136 KB. Os arquivos locais amostrados têm 80–122 KB.
- O envio do grupo (`Evolution.send_post`) manda a URL para a Evolution baixar, com
  3 tentativas e esperas de 2 s e 6 s, e só então baixa por conta própria
  (`send_image_bytes`, que recusa o GIF de 1x1 da Amazon e arquivo < 1 KB). A CDN do
  Pelando recusa o User-Agent da Evolution (403) e aceita o nosso.
- O Gemini (plano gratuito, 500 chamadas de texto/dia, divididas com o copywriter do
  grupo) faz duas chamadas por pergunta: `entender` (pedido estruturado) e `_escrever`
  (texto da resposta, conferido por `fiel`: só preços e links dos itens). Quando falha
  ou inventa, sai `modelo_fixo`.
- `Atendente.atender_uma` manda tudo com um `send_text`, depois de uma espera humana
  de 2–5 s; o aviso de privacidade vai no fim da primeira resposta de cada número.

## Objetivos

1. Cada produto encontrado vira **uma mensagem com foto e legenda** (a legenda é o
   bloco daquele produto: nome, preço, loja, quando foi visto, "saiu no grupo",
   veredito, link).
2. A foto vem de `products.image_url` — URL ou arquivo local.
3. O Gemini continua escrevendo o texto: uma frase de abertura e uma legenda por
   produto, conferidas uma a uma (cada legenda só pode citar o preço e o link do seu
   produto e tem que trazer o link dele). Reprovou ou falhou, vale o modelo fixo para
   todas as legendas.
4. Depois das fotos, **uma mensagem de texto** de fechamento: os parecidos (se houver),
   o aviso de afiliado e, na primeira resposta de um número, o aviso de privacidade.
5. Foto que não baixa, não é imagem ou é pequena demais: aquele produto sai como texto
   (a mesma legenda), sem atrasar os outros.

## Não-objetivos

- Foto dos "parecidos" (seguem em texto no fechamento).
- Mudar a busca, o índice, o webhook ou o envio do grupo (`send_post` fica como está).
- Álbum de fotos, botão, lista interativa.
- Guardar foto nova ou redimensionar foto.

## Anti-objetivos (falharia mesmo "funcionando")

- Mandar o pixel vazio da Amazon (ou qualquer não-foto) com status de sucesso.
- Deixar a pessoa esperando: nada de 3 tentativas com 2 s + 6 s por foto, que é o
  ritmo do grupo, não de conversa.
- Legenda com preço ou link que não é daquele produto.
- Gastar mais chamadas do Gemini por pergunta do que hoje (são 2).
- Uma foto ruim derrubar a resposta inteira.

## Restrições

- Legenda de foto no WhatsApp: até 1.024 caracteres (`CAPTION_LIMIT`).
- Container do assistente: 160 MiB; foto típica 7–140 KB, baixada uma de cada vez.
- `sendMedia` da Evolution aceita base64 (JPEG e WebP já funcionam assim em produção
  via `send_image_bytes`).
- Testes sem rede e sem `.env` (rodam numa cópia do projeto).
- Código só chega à VPS por PR e merge na `main`; o deploy recria o assistente sozinho.

## Abordagens consideradas

1. **Uma mensagem por produto, foto + legenda (escolhida — decisão nossa).** Mesmo
   formato dos posts do grupo; cada foto fica colada ao preço e ao link dela. Custo:
   até 4 mensagens por resposta (3 fotos + fechamento) em vez de 1.
2. **Só a foto do primeiro produto, com a resposta inteira na legenda.** Descartada:
   a resposta com 3 produtos e links passa de 1.024 caracteres, e os outros dois
   ficariam sem foto.
3. **O texto de hoje e as fotos soltas depois.** Descartada: a foto fica longe do preço
   e do link dela, e a ordem no WhatsApp não garante a leitura certa.

Para o envio, também decisão nossa: **o assistente baixa a foto ele mesmo e manda os
bytes** (uma tentativa, sem esperas), em vez de passar a URL para a Evolution. Motivos:
a CDN do Pelando recusa a Evolution; o pixel da Amazon passaria como sucesso; e as
esperas de 2 s + 6 s do grupo são longas demais para quem está esperando resposta.

## Design

### Dados
- `Resultado` ganha `imagem: str | None` (no fim, com padrão `None`), preenchido por
  `detalhes()` a partir de `products.image_url` (vazio vira `None`).

### Conversa (`conversa.py`)
- Novo `Cartao(legenda: str, imagem: str | None)`. `Resposta` ganha
  `cartoes: tuple[Cartao, ...] = ()`. Para resposta de produto: `cartoes` tem um
  cartão por resultado, na ordem, e `texto` passa a ser só o fechamento (parecidos +
  `DIVULGACAO`). Boas-vindas, "não achei", PAROU e VOLTOU continuam só com `texto`.
- O Gemini escreve com saída estruturada: `{"abertura": str, "legendas": [str]}`.
  Aprovação: número de legendas igual ao de achados; cada legenda passa em
  `fiel(legenda, [achado])`; a abertura não cita preço nem link; abertura + primeira
  legenda cabem em 1.024 caracteres (e cada legenda também). Qualquer reprovação ou
  falha: todas as legendas do modelo fixo. Continua sendo **uma** chamada de escrita.
- Modelo fixo: a legenda de cada produto é o bloco que `modelo_fixo` já monta hoje
  ("1. *nome curto*", preço/loja/visto, "Saiu no grupo…", veredito, link); a abertura
  fixa é "Achei estas opções para *consulta*:". A abertura (fixa ou do Gemini) vai no
  começo da legenda do primeiro cartão.
- Os parecidos saem no fechamento, no formato de texto de hoje (fixo).

### Envio (`delivery/evolution.py`)
- Novo `Evolution.send_card(caption, image, to)`:
  - sem imagem: `send_text(caption, to)`;
  - URL `http(s)`: baixa com `MEDIA_USER_AGENT`, `MEDIA_TIMEOUT`, seguindo redirect;
    aceita só status 200, tipo diferente de GIF e pelo menos 1.024 bytes; manda com
    `_enviar_bytes`;
  - arquivo local: lê o arquivo; pelo menos 1.024 bytes; manda com `_enviar_bytes`;
  - qualquer falha que não seja `NotConnected`: registra aviso e manda a legenda como
    texto. `NotConnected` sobe (instância caída não é problema da foto).
  - Uma tentativa só, sem `time.sleep`.
- `send_post` e `send_image_bytes` (grupo) não mudam de comportamento.

### Atendimento (`servico.py`)
- Com `cartoes`: espera humana, um `send_card` por cartão, na ordem; depois um
  `send_text` com o fechamento (+ aviso de privacidade se for o primeiro contato).
- Sem `cartoes`: igual a hoje.
- O que fica gravado em `mensagens.resposta` é tudo o que foi enviado, na ordem
  (legendas e fechamento, separados por linha em branco).
- O log de "respondeu" passa a dizer quantos cartões tinham foto.

### Detalhes decididos depois da revisão (decisão nossa)
- Foto do assistente: timeout de 10 s por foto (o do grupo é 20 s); URL só vale com
  status 200, `content-type` `image/*` que não seja GIF, e pelo menos 1.024 bytes.
- `NotConnected` no meio da resposta: os cartões já enviados ficam, a mensagem vai para
  `erro` (como qualquer falha de envio hoje). Outras falhas de um cartão viram texto
  daquele cartão e a resposta segue.
- Abertura do Gemini que, somada à primeira legenda, passe de 1.024 caracteres reprova
  a escrita inteira (vale o modelo fixo, cuja abertura é curta).

## Validação esperada

- Testes de unidade sem rede para cada parte (dados, legendas do Gemini e fixas,
  `send_card` com URL, arquivo, pixel, falha e `NotConnected`, e o atendimento com
  cartões), mais a suíte inteira.
- Ensaio local com uma cópia do banco de produção (`promos.db`, tirada só com leitura):
  - perguntas reais dos testes de 19/11 (ps5, relógio garmin, forerunner 265, iphone
    15, air fryer até 400, geladeira frost free, fone bluetooth, gta 6, tv 50
    polegadas);
  - busca só por texto — é a mesma de produção hoje, porque os vetores estão em 2% e
    só entram com 90%; por isso não há parecidos;
  - Gemini real em 5 perguntas (cerca de 10 das 500 chamadas do dia), para exercitar a
    conferência das legendas; as outras com o modelo fixo;
  - fotos baixadas de verdade pelo `send_card`, com o POST para a Evolution trocado por
    um gravador (nada vai para o WhatsApp);
  - fotos em arquivo local: copiar da VPS só os arquivos que aparecerem nas respostas
    do ensaio e apontar `/app/data/fotos` para a cópia; e, na VPS, conferir só com
    leitura que os 1.856 caminhos locais existem e têm pelo menos 1 KB.
- O `assistente.db` de produção não é copiado: guarda mensagens privadas, com a promessa
  de apagar em 30 dias.
- Em produção só depois do merge na `main` (deploy pelo dono).

## Deixado para quem constrói

- Nomes internos, onde ficam os testes, como refatorar `modelo_fixo` em pedaços.
- Texto exato dos logs.
- Se `send_card` reaproveita a validação de `send_image_bytes` por um helper comum.

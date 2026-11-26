# Assistente que responde as coisas certas — design

Data: 26/11/2026. Pedido do dono: "quando eu mandar mensagem para o bot, ele tem
que me retornar as coisas certas, as promoções certas", com busca vetorial e por
palavra, e "a IA responder coisas certas com base no que realmente temos". O dono
pediu para não ser consultado: as decisões abaixo são **decisão nossa**, tomadas
com dado de produção.

## O que medimos (produção, 26/11/2026, só leitura)

- O banco já é vetorial: `assistente.db` (SQLite) guarda o texto em FTS5 e os
  vetores (`gemini-embedding-001`, 256 dimensões) em BLOB; a busca soma as duas
  listas por RRF (k=60). É o padrão das referências de busca híbrida em SQLite.
- **A metade vetorial está desligada.** Há 7.735 vetores para 12.555 produtos
  (62%), e a trava `COBERTURA_MINIMA = 0.9` só liga o vetor com 90%. A cota
  gratuita de embedding dá cerca de 1.000 textos por dia (48 avisos de 429 em 72 h),
  e o catálogo muda todo dia: os 90% podem nunca chegar.
- Perguntas reais do beta que saíram erradas:
  - "me mande promoções do play5" → busca "play5", **0 resultados** (o catálogo
    escreve "PlayStation 5"; sem vetor, nada liga um ao outro);
  - "e o console do play 5" → 1 resultado;
  - "esse foi o melhor preço? vi que teve promoção esses dias por 4k" → boas-vindas.
    O bot não lembra o que acabou de mostrar, e o banco tem o histórico de preço
    para responder.
- O dono pergunta à noite, quando o Gemini já está sem cota: o assistente usa o
  mesmo modelo do bot do grupo (`gemini-3.5-flash-lite`, 500 por dia, cota por
  modelo e por projeto). Testado: `gemini-3.1-flash-lite` responde com saída
  estruturada em ~2 s e tem cota própria; `gemini-2.5-flash-lite` não existe mais
  para contas novas.

## Objetivos

1. Ligar a busca vetorial já, com o que temos (62%), sem trazer lixo.
2. Vetorizar primeiro o que importa: produto que saiu no grupo nos últimos 30 dias.
3. "play5", "play 5", "play station 5" acham o PS5.
4. O assistente usar um Gemini só dele, sem disputar a cota do grupo.
5. Lembrar a última resposta por 30 minutos: pergunta de acompanhamento sobre um
   produto mostrado ("esse foi o melhor preço?", "o segundo já esteve mais
   barato?") recebe o histórico real do banco.
6. Validar com as perguntas reais do beta e um conjunto fixo de perguntas, medindo
   antes e depois.

## Não-objetivos

- Trocar o banco vetorial (sqlite-vec, pgvector, Qdrant), modelo de embedding local
  ou faturamento do Gemini.
- Chamada extra ao Gemini para reordenar resultados.
- Memória além da última resposta; refinar a busca anterior sem o Gemini.

## Anti-objetivos

- Resposta com preço, data ou loja que não estão no banco.
- Vetor trazendo produto sem relação (o "Papai Noel" de 18/11, com 2% de cobertura).
- Mais de 2 chamadas ao Gemini por mensagem; usar o modelo do grupo.

## Abordagens consideradas

1. **Ligar o que já temos (escolhida — decisão nossa).** SQLite + FTS5 + vetores em
   BLOB + RRF, com o vetor ligado por quantidade, prioridade de vetorização,
   sinônimos, modelo próprio e memória curta. Nenhum serviço novo.
2. **Trocar o banco vetorial** (sqlite-vec, pgvector no Postgres da Evolution,
   Qdrant). Não corrige nenhuma falha medida — o gargalo é a cota de embedding e a
   leitura do pedido — e soma extensão nativa ou container numa VPS de 2 GB com 84%
   de disco. Com 12 mil vetores de 256 dimensões, a conta em numpy leva milissegundos.
   Vale reavaliar acima de ~100 mil produtos.
3. **Modelo de embedding local** (ONNX multilíngue). Acaba com a cota, mas pede
   +300–400 MB de RAM no container (hoje 160 MiB) e tem qualidade menor em português.
   Reserva, se a cota de 1.000/dia não acompanhar o catálogo.

## Design

### Busca e índice
- Entra `MINIMO_DE_VETORES = 2000`: a busca por vetor vale com pelo menos 2.000
  vetores carregados **ou** com `COBERTURA_MINIMA` (90%) do catálogo — o segundo
  caso mantém catálogo pequeno (testes, instalação nova) funcionando. O piso de
  mediana + 0,06 continua. O resumo do índice segue registrando a cobertura.
- `itens` ganha `prioridade INTEGER NOT NULL DEFAULT 0` (migração com `ALTER TABLE`
  quando a coluna não existe). `sincronizar` grava 1 para produto com post enviado
  ao grupo nos últimos 30 dias. `vetorizar_pendentes` ordena por
  `prioridade DESC, visto_em DESC`.
- Orçamento diário de embedding: o indexador para quando o dia (fuso do Pacífico, que é
  quando a cota do Gemini renova) chega a `ORCAMENTO_DO_INDICE = 800` textos, deixando
  cerca de 200 da cota diária (~1.000, medida) para o vetor das perguntas. Medido em
  26/11: a cota estava esgotada às 14h e só voltava às 05h — com o vetor ligado, a
  pergunta ficaria sem vetor quase o dia todo.
- Sinônimos: "play5", "play 5", "play station 5", "playstation5" e os mesmos para 4
  entram no grupo do ps5/ps4.

### Gemini do assistente
- `config.assistente_gemini_modelo()` lê `ASSISTENTE_GEMINI_MODEL`, padrão
  `gemini-3.1-flash-lite`; o `Assistente` usa esse modelo. O bot do grupo continua
  em `GEMINI_MODEL`. `.env.sample` documenta.

### Memória curta e histórico de preço
- `mensagens` ganha `produtos TEXT` (JSON com os `product_id` mostrados, na ordem),
  gravado quando a resposta é de produto. Migração como acima.
- Contexto = última mensagem `respondida` do mesmo telefone, há no máximo 30
  minutos, com `produtos` não vazio: a consulta e os ids.
- `Pedido` ganha `tipo = "historico"`, `item: int | None` (1 = primeiro produto
  mostrado) e `preco_citado: float | None` ("4k", "4 mil", "R$ 4.000").
- Com contexto, o `entender` do Gemini recebe a conversa anterior (consulta e os
  produtos mostrados, com nome e preço) e decide: `historico` quando a pergunta é
  sobre preço ou histórico de algo já mostrado; `produto` para busca nova, com a
  consulta completa ("e o console?" depois de PS5 vira "console ps5").
- Sem Gemini: com contexto, mensagem com sinal de histórico ("melhor preço", "menor
  preço", "mais barato", "já esteve", "baixou", "esse foi") vira `historico`; "o
  segundo"/"2" escolhe o item; o preço citado sai por regex.
- Resposta de histórico, montada só com o banco (sem Gemini): nome; preço de hoje,
  loja e quando foi visto; menor preço medido em 60 dias e a data; "saiu no grupo"
  com data e preço; comparação com o preço citado ("não medimos esse produto a R$
  4.000,00 ou menos nos últimos 60 dias"); veredito; link de afiliado; divulgação.
  Sem histórico suficiente, diz isso. Histórico sem contexto: pede o produto.

## Validação

- Testes de unidade por parte e a suíte inteira, numa cópia sem `.env`.
- Conjunto fixo de perguntas (as reais do beta e as do teste de 19/11) com regra
  de acerto por pergunta (regex no título e faixa de preço), rodado na cópia de
  produção — `promos.db` e só as tabelas `itens` e `vetores` do `assistente.db`, sem
  mensagens — medindo acerto no 1º resultado, precisão nos 3 e respostas vazias, na
  `staging` e na branch.
- As perguntas de acompanhamento reais repetidas com o Gemini novo, sem enviar nada.
- Produção só depois do merge na `main`; o deploy recria o assistente.

## Deixado para quem constrói

- Nomes internos, formato exato do texto de histórico, onde ficam os testes.

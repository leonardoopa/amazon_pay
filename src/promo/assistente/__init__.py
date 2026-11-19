"""Assistente de busca no WhatsApp.

Quem manda mensagem privada ao numero do bot pergunta por um produto ("tem ps5
barato?") e recebe, do NOSSO banco, as ofertas que batem e duas parecidas --
com o preco medido, a hora da medicao e o veredito contra a mediana de 60 dias.

Roda num servico proprio (`amazon_pay assistente`), e nao dentro do bot: em
18/11/2026 o container do bot usava 220 de 256 MiB, e um indice vetorial ali
arriscaria derrubar o envio do grupo por falta de memoria.

Camadas, de baixo para cima:

- `banco`: o SQLite do assistente -- fila de mensagens, contatos, indice.
- `indice`: copia os produtos ativos do `promos.db` e gera os embeddings.
- `busca`: busca hibrida, FTS5 (lexical) mais vetor (semantica), juntas por RRF.
- `conversa`: o Gemini entende o pedido e escreve a resposta, presa aos dados.
- `webhook`: le o evento `messages.upsert` da Evolution.
- `servico`: a API do webhook, a fila de resposta e o indexador.
"""

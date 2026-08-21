# Vídeo do Scroll Cinema

Coloque aqui:

| Arquivo | O que é |
|---|---|
| `hero.mp4` | vídeo já reencodado com `keyint=1` |
| `poster.jpg` | opcional — use o **frame A**, é o primeiro quadro |
| `produto/<external_id>.mp4` | filme de um produto da vitrine |

A seção só aparece no site quando `hero.mp4` existe. Sem ele, o hero
normal continua valendo.

## Filme de produto

Um produto da vitrine troca a foto por vídeo quando existe um arquivo com
o **id dele** no nome — `produto/MLB46211942.mp4` casa com o produto de
`external_id` `MLB46211942`. Não há tabela de-para no código: salvar o
arquivo com o nome certo já coloca ele no ar, e apagar volta para a foto.

Esses vídeos são arrastados pela mesma rolagem que troca os produtos, então
passam pelo mesmo preparo do `hero.mp4`. Duas diferenças:

- **1280 de largura, não 1920.** Eles moram numa caixa de ~440px, não na
  tela inteira. Passe `LARGURA` e `PISO` juntos — o piso é o que impede
  alguém de rebaixar o vídeo cheio sem perceber.
- **`DURACAO` corta a cauda.** Gerador de vídeo fecha o clipe dissolvendo
  de volta ao primeiro quadro, para dar loop. Parado num quadro qualquer
  pelo scroll, isso vira um fantasma duplo. Meça onde a dissolução começa e
  corte antes.

```bash
DURACAO=9.75 VELOCIDADE=2 LARGURA=1280 PISO=1280 CRF=21 \
  bash web/scripts/scroll_cinema.sh bruto.mp4 \
  web/vitrine/static/vitrine/video/produto/MLB46211942.mp4
```

## Como preparar o arquivo

O vídeo que sai do gerador **não serve direto**. Um MP4 comum tem um
keyframe a cada ~2 segundos, e o navegador só consegue saltar liso entre
keyframes — arrastar pelo scroll um arquivo desses engasga e parece
quebrado.

```bash
bash web/scripts/scroll_cinema.sh bruto.mp4 web/vitrine/static/vitrine/video/hero.mp4
```

O script interpola para 60fps, reencoda com todo quadro virando keyframe,
e **falha explicitamente** se `quadros != keyframes` ou se a largura ficar
abaixo do piso. Um arquivo que passa calado nessas duas conferências é
exatamente o que produz o engasgo sem causa aparente.

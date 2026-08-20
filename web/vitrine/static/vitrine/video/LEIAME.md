# Vídeo do Scroll Cinema

Coloque aqui:

| Arquivo | O que é |
|---|---|
| `hero.mp4` | vídeo já reencodado com `keyint=1` |
| `poster.jpg` | opcional — use o **frame A**, é o primeiro quadro |

A seção só aparece no site quando `hero.mp4` existe. Sem ele, o hero
normal continua valendo.

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
abaixo de 1920. Um arquivo que passa calado nessas duas conferências é
exatamente o que produz o engasgo sem causa aparente.

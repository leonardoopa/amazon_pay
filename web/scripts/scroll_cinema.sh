#!/usr/bin/env bash
# Prepara um vídeo bruto para ser controlado pelo scroll.
#
#   ./scroll_cinema.sh bruto.mp4 ../vitrine/static/vitrine/video/hero.mp4
#
# São dois passos, e o segundo é o que quase ninguém faz:
#
#   1. Interpolação para 60fps. Roda local, custa zero. Não compre
#      interpolação paga antes de testar isto.
#   2. Reencode com keyint=1, ou seja, TODO quadro vira keyframe. Um MP4
#      normal tem um keyframe a cada ~2 segundos, e o navegador só salta
#      liso entre keyframes. Sem este passo o vídeo engasga ao ser
#      arrastado pelo scroll e parece quebrado.
#
# Piso obrigatório de 1920 de largura. Se o peso incomodar, a ordem é:
# encurtar o vídeo, baixar para 40fps, subir o CRF — resolução por último.

set -euo pipefail

BRUTO="${1:?uso: scroll_cinema.sh <bruto.mp4> <saida.mp4>}"
SAIDA="${2:?uso: scroll_cinema.sh <bruto.mp4> <saida.mp4>}"
FPS="${FPS:-60}"
CRF="${CRF:-18}"           # 18 para vídeo full-bleed, 21 para caixa contida
LARGURA="${LARGURA:-1920}"
VELOCIDADE="${VELOCIDADE:-1}"  # 2 = corta a duração pela metade

# O winget instala o ffmpeg e só acrescenta ao PATH de shells NOVOS. Aqui a
# busca usa $HOME em vez de $LOCALAPPDATA porque essa variável chega com
# barras invertidas do Windows, que o glob do bash não expande.
if ! command -v ffmpeg >/dev/null 2>&1; then
  for RAIZ in "$HOME/AppData/Local" "$USERPROFILE/AppData/Local"; do
    WINGET_BIN=$(ls -d "$RAIZ"/Microsoft/WinGet/Packages/Gyan.FFmpeg*/*/bin 2>/dev/null | head -1)
    if [ -n "$WINGET_BIN" ]; then
      PATH="$WINGET_BIN:$PATH"
      break
    fi
  done
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "ffmpeg não encontrado. Instale antes de continuar:" >&2
  echo "  Windows: winget install Gyan.FFmpeg" >&2
  echo "  macOS:   brew install ffmpeg" >&2
  echo "  Linux:   apt-get install ffmpeg" >&2
  exit 1
fi

TEMP="$(dirname "$SAIDA")/.interp.mp4"
mkdir -p "$(dirname "$SAIDA")"

echo "==> O que veio do gerador"
ffmpeg -i "$BRUTO" -hide_banner 2>&1 | grep Stream || true

# A aceleração vem ANTES da interpolação de propósito. Acelerar depois
# jogaria fora justamente os quadros que a interpolação acabou de criar.
FILTRO_VELOCIDADE=""
if [ "$VELOCIDADE" != "1" ]; then
  echo "==> acelerando ${VELOCIDADE}x antes de interpolar"
  FILTRO_VELOCIDADE="setpts=PTS/${VELOCIDADE},"
fi

echo "==> 1/2 interpolando para ${FPS}fps (~75s por 5s de vídeo)"
ffmpeg -y -i "$BRUTO" \
  -vf "${FILTRO_VELOCIDADE}minterpolate=fps=${FPS}:mi_mode=mci:mc_mode=aobmc:vsbmc=1:me_mode=bidir:search_param=32" \
  -c:v libx264 -crf 16 -preset medium -an "$TEMP"

echo "==> 2/2 reencodando com todo quadro = keyframe"
ffmpeg -y -i "$TEMP" \
  -vf "scale=${LARGURA}:-2:flags=lanczos" \
  -c:v libx264 \
  -x264-params "keyint=1:min-keyint=1:scenecut=0" \
  -g 1 -crf "$CRF" -preset slow \
  -pix_fmt yuv420p -movflags +faststart -an \
  "$SAIDA"

rm -f "$TEMP"

# Sem esta conferência não dá para saber se o passo 2 pegou. Se os dois
# números não baterem, o scroll vai engasgar e a causa fica invisível.
echo "==> conferindo"
TOTAL=$(ffmpeg -i "$SAIDA" -f null - 2>&1 | grep -oE 'frame= *[0-9]+' | tail -1 | grep -oE '[0-9]+')
CHAVES=$(ffmpeg -i "$SAIDA" -vf "select=eq(pict_type\,I)" -frames:v 99999 -f null - 2>&1 \
  | grep -oE 'frame= *[0-9]+' | tail -1 | grep -oE '[0-9]+')

echo "    quadros: ${TOTAL}  |  keyframes: ${CHAVES}"
if [ "$TOTAL" != "$CHAVES" ]; then
  echo "    FALHOU: nem todo quadro é keyframe. O scroll vai engasgar." >&2
  exit 1
fi

LARG_REAL=$(ffmpeg -i "$SAIDA" -hide_banner 2>&1 | grep -oE '[0-9]{3,}x[0-9]{3,}' | head -1 | cut -dx -f1)
if [ "${LARG_REAL:-0}" -lt 1920 ]; then
  echo "    FALHOU: largura ${LARG_REAL} abaixo do piso de 1920." >&2
  exit 1
fi

echo "    largura: ${LARG_REAL}  |  peso: $(du -h "$SAIDA" | cut -f1)"
echo "==> pronto: $SAIDA"

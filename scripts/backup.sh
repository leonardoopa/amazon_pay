#!/usr/bin/env bash
#
# Backup do que não se recupera: histórico de preço, banco do site e a sessão
# pareada do WhatsApp.
#
# Perder promos.db significa voltar a esperar MIN_OBSERVATIONS dias de coleta
# antes de poder postar de novo. Perder site.db leva os e-mails de quem pediu
# aviso de vaga. Perder os volumes da Evolution obriga a parear o chip outra
# vez, com o risco que todo pareamento novo carrega.
#
# Uso:
#   scripts/backup.sh [destino]        # padrão: ./backups
#
# No cron (diário, 3h da manhã), com o repositório em ~/amazon_pay:
#   0 3 * * * cd ~/amazon_pay && scripts/backup.sh >> backups/backup.log 2>&1
#
# Manda o resultado para fora da máquina depois. Backup no mesmo disco não
# protege do caso mais comum, que é o disco (ou a VPS) sumir.

set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO="${1:-$RAIZ/backups}"
DATA="$(date +%Y-%m-%d_%H%M)"
ALVO="$DESTINO/$DATA"

mkdir -p "$ALVO"

# `.backup` do sqlite3 e não `cp`: com o bot escrevendo durante a cópia, um cp
# pode gravar um arquivo inconsistente -- e um backup que não abre só é
# descoberto no dia em que ele é a única coisa que sobrou.
copiar_sqlite() {
	local origem="$1" nome="$2"
	if [ ! -f "$origem" ]; then
		echo "aviso: $origem não existe ainda, pulando"
		return
	fi
	if command -v sqlite3 >/dev/null 2>&1; then
		sqlite3 "$origem" ".backup '$ALVO/$nome'"
	else
		# Sem o cliente sqlite3 instalado, o Python que roda o bot serve: a
		# mesma API de backup online, sem depender de pacote extra.
		python3 - "$origem" "$ALVO/$nome" <<-'PY'
			import sqlite3, sys

			origem, destino = sys.argv[1], sys.argv[2]
			with sqlite3.connect(origem) as o, sqlite3.connect(destino) as d:
			    o.backup(d)
		PY
	fi
	echo "ok: $nome"
}

copiar_sqlite "$RAIZ/data/promos.db" "promos.db"
copiar_sqlite "$RAIZ/data/site.db" "site.db"

# Volumes da Evolution: a sessão do WhatsApp vive neles. Só existem onde o
# compose está rodando.
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
	for volume in evolution_instances evolution_postgres; do
		nome_real="$(docker volume ls --quiet --filter "name=${volume}$" | head -n1)"
		if [ -z "$nome_real" ]; then
			echo "aviso: volume $volume não encontrado, pulando"
			continue
		fi
		docker run --rm \
			-v "$nome_real":/v:ro \
			-v "$ALVO":/out \
			alpine tar czf "/out/${volume}.tar.gz" -C /v .
		echo "ok: ${volume}.tar.gz"
	done
else
	echo "aviso: docker indisponível, volumes da Evolution não incluídos"
fi

# Retenção: 14 dias. Sem isso o disco enche e disco cheio derruba os
# containers todos de uma vez -- o oposto do que um backup deveria causar.
find "$DESTINO" -maxdepth 1 -type d -name '20*' -mtime +14 -exec rm -rf {} +

echo "backup em $ALVO"
du -sh "$ALVO"

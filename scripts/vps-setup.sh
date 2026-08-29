#!/usr/bin/env bash
#
# Prepara uma VPS Ubuntu recém-criada para receber o compose: Docker, swap,
# firewall e o ajuste de paginação.
#
# Roda uma vez, logo depois do primeiro SSH. É idempotente -- rodar de novo
# não duplica swap nem regra de firewall.
#
# Uso, já dentro do servidor:
#   curl -fsSL https://raw.githubusercontent.com/leonardoopa/amazon_pay/main/scripts/vps-setup.sh -o vps-setup.sh
#   less vps-setup.sh          # leia antes de executar, é root que vai rodar
#   bash vps-setup.sh
#
# Depois dele, reabra a sessão SSH: o grupo `docker` só vale no login seguinte.

set -euo pipefail

if [ "$(id -u)" -eq 0 ] && [ -z "${SUDO_USER:-}" ]; then
	echo "erro: rode como usuário comum com sudo, não como root direto." >&2
	echo "O usuário precisa existir para entrar no grupo docker." >&2
	exit 1
fi

USUARIO="${SUDO_USER:-$USER}"
SWAP_GB="${SWAP_GB:-2}"

echo "==> Pacotes base"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
	ca-certificates curl sqlite3 ufw fail2ban

# ---------------------------------------------------------------------------
# SSH
#
# Um IP público de VPS começa a receber tentativa de login em minutos, vindas
# de listas que testam primeiro o padrão "nome + data + símbolo". As proteções
# aqui não dependem da força da senha:
#
#   - fail2ban bane o IP depois de 5 tentativas, o que transforma um ataque de
#     milhões de senhas por hora em algumas dezenas.
#   - root não loga direto: quem invade precisa acertar usuário E senha, e o
#     usuário deixa de ser o `root` que toda lista tenta.
#   - senha vazia nunca passa.
#
# E, quando já existe chave cadastrada, a senha é desligada de vez -- aí a
# força bruta acaba em vez de só ficar lenta. Ver a condicional abaixo: ela
# existe para não trancar a porta antes da chave estar do lado de dentro.
# ---------------------------------------------------------------------------
echo "==> SSH e fail2ban"

if ! sudo test -f /etc/fail2ban/jail.local; then
	# backend=systemd porque o Ubuntu 24.04 não traz rsyslog por padrão: sem
	# isso o fail2ban procura /var/log/auth.log, não acha, e a jail nunca sobe.
	sudo tee /etc/fail2ban/jail.local >/dev/null <<-'CONF'
		[sshd]
		enabled = true
		backend = systemd
		maxretry = 5
		findtime = 10m
		bantime = 1h
	CONF
fi
sudo systemctl enable --now fail2ban

if [ ! -f /etc/ssh/sshd_config.d/99-amazon-pay.conf ]; then
	sudo mkdir -p /etc/ssh/sshd_config.d
	sudo tee /etc/ssh/sshd_config.d/99-amazon-pay.conf >/dev/null <<-'CONF'
		PermitRootLogin no
		PermitEmptyPasswords no
		MaxAuthTries 3
	CONF

	# Desligar senha é o que realmente encerra a força bruta -- fail2ban só
	# atrasa. Mas desligar antes da chave existir tranca a porta com a chave do
	# lado de fora, então isto é condicional de propósito: só quando já há
	# authorized_keys populado para este usuário. Quem entrou por senha no
	# painel continua entrando; quem cadastrou chave fecha a senha.
	CASA="$(getent passwd "$USUARIO" | cut -d: -f6)"
	if sudo test -s "$CASA/.ssh/authorized_keys"; then
		sudo tee -a /etc/ssh/sshd_config.d/99-amazon-pay.conf >/dev/null <<-'CONF'
			PasswordAuthentication no
			KbdInteractiveAuthentication no
		CONF
		echo "Chave encontrada em $CASA/.ssh/authorized_keys: senha desligada."
	else
		echo "Sem authorized_keys para $USUARIO: login por senha continua ligado."
		echo "Cadastre a chave e rode de novo para fechá-lo."
	fi

	# Testa a configuração antes de recarregar: sshd_config quebrado derruba o
	# serviço, e sem SSH não há como consertar uma VPS remota.
	if sudo sshd -t; then
		sudo systemctl reload ssh 2>/dev/null || sudo systemctl reload sshd
	else
		echo "erro: sshd_config inválido, revertendo." >&2
		sudo rm -f /etc/ssh/sshd_config.d/99-amazon-pay.conf
		exit 1
	fi
fi

# ---------------------------------------------------------------------------
# Swap
#
# Em 2 GB de RAM o swap não é para rodar em swap -- é rede de segurança. O pico
# de `docker compose build` (pip install mais collectstatic) passa de 400 MB, e
# sem swap esse pico mata um container em produção em vez de falhar o build.
#
# swappiness=10 mantém o kernel usando RAM enquanto houver: o swap fica para o
# pico, não para o regime. Paginar a Evolution em regime derruba o websocket do
# WhatsApp, que é justamente o que não pode cair.
# ---------------------------------------------------------------------------
if swapon --show | grep -q '/swapfile'; then
	echo "==> Swap já configurado, pulando"
else
	echo "==> Swap de ${SWAP_GB} GB"
	sudo fallocate -l "${SWAP_GB}G" /swapfile
	sudo chmod 600 /swapfile
	sudo mkswap /swapfile
	sudo swapon /swapfile
	echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if [ ! -f /etc/sysctl.d/99-amazon-pay.conf ]; then
	printf 'vm.swappiness=10\nvm.vfs_cache_pressure=50\n' \
		| sudo tee /etc/sysctl.d/99-amazon-pay.conf >/dev/null
	sudo sysctl --quiet --load /etc/sysctl.d/99-amazon-pay.conf
fi

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
	echo "==> Docker já instalado, pulando"
else
	echo "==> Docker"
	curl -fsSL https://get.docker.com | sh
fi

sudo usermod -aG docker "$USUARIO"
sudo systemctl enable --now docker

# ---------------------------------------------------------------------------
# Firewall
#
# Só 22, 80 e 443. A API (8000) e o painel da Evolution (8080) ficam presos em
# 127.0.0.1 pelo compose e se alcançam por túnel SSH -- a EVOLUTION_API_KEY dá
# controle total do WhatsApp pareado, e exposta na internet é conta perdida.
#
# A 80 continua necessária mesmo com o site em HTTPS: é por ela que o Let's
# Encrypt valida o domínio antes de emitir o certificado.
# ---------------------------------------------------------------------------
echo "==> Firewall"
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable

echo
echo "Pronto. Estado:"
free -h
echo
sudo ufw status
echo
sudo fail2ban-client status sshd 2>/dev/null || true
echo
echo "ATENÇÃO: root não loga mais por SSH. Antes de fechar esta sessão, abra"
echo "OUTRO terminal e confirme que 'ssh $USUARIO@<IP>' funciona. Se você"
echo "fechar esta e o login falhar, o acesso à VPS sai só pelo console do"
echo "painel da hospedagem."
echo
echo "Reabra a sessão SSH depois disso -- o grupo docker só vale no próximo login."
echo "Em seguida: git clone https://github.com/leonardoopa/amazon_pay.git"

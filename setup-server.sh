#!/usr/bin/env bash
# GameHub one-command server setup (Ubuntu/Debian) + CGNAT playit helper.
#
# Usage on your LINUX SERVER (not your PC):
#   git clone <your-repo> /opt/gamehub && cd /opt/gamehub
#   chmod +x setup-server.sh
#   sudo ./setup-server.sh --admin-pass 'pick-a-strong-password' --with-playit
#
# Flags:
#   --admin-user NAME     (default: admin)
#   --admin-pass PASS     (default: random generated, printed at end)
#   --port PORT           (default: 8000, manager UI)
#   --with-playit         also install playit.gg agent (recommended for PH CGNAT)
#   --playit-secret KEY   non-interactive playit claim (from playit.gg dashboard).
#                         Omit it and the script prints a claim link instead.
#   --yes                 skip confirmation
set -euo pipefail

ADMIN_USER="admin"
ADMIN_PASS=""
PORT="8000"
WITH_PLAYIT="false"
PLAYIT_SECRET=""
YES="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --admin-user) ADMIN_USER="$2"; shift 2;;
    --admin-pass) ADMIN_PASS="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    --with-playit) WITH_PLAYIT="true"; shift;;
    --playit-secret) PLAYIT_SECRET="$2"; shift 2;;
    --yes) YES="true"; shift;;
    -h|--help) sed -n '2,20p' "$0"; exit 0;;
    *) echo "Unknown flag: $1 (see --help)"; exit 1;;
  esac
done

if [[ $EUID -ne 0 ]]; then echo "Run as root: sudo ./setup-server.sh ..."; exit 1; fi
if [[ "$YES" != "true" ]]; then
  echo "This will install Docker + GameHub (port $PORT) on this server."
  [[ "$WITH_PLAYIT" == "true" ]] && echo "+ playit.gg agent (CGNAT bypass for Palworld 8211/27015 UDP)."
  read -rp "Continue? [y/N] " c; [[ "$c" =~ ^[Yy]$ ]] || exit 0
fi

echo "==> [1/5] Installing Docker (if missing)..."
if ! command -v docker >/dev/null; then
  apt-get update -y
  apt-get install -y ca-certificates curl gnupg ufw openssl
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  UBUNTU_CODENAME=$(grep VERSION_CODENAME /etc/os-release | cut -d= -f2 || echo "jammy")
  echo "deb [signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $UBUNTU_CODENAME stable" > /etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
else
  echo "Docker already installed: $(docker --version)"
fi

echo "==> [2/5] Firewall (ufw): allow ssh + $PORT, game ports reached via playit tunnel..."
apt-get install -y ufw openssl >/dev/null 2>&1 || true
ufw allow OpenSSH >/dev/null 2>&1 || ufw allow 22/tcp >/dev/null 2>&1 || true
ufw allow "${PORT}/tcp" >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || true

echo "==> [3/5] Writing .env..."
SECRET_KEY=$(openssl rand -hex 32)
if [[ -z "$ADMIN_PASS" ]]; then ADMIN_PASS=$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 20); GENERATED="true"; else GENERATED="false"; fi
if [[ ! -f .env ]]; then cp .env.example .env; fi
# idempotent key=value set
set_kv() { grep -q "^$1=" .env && sed -i "s|^$1=.*|$1=$2|" .env || echo "$1=$2" >> .env; }
set_kv ADMIN_USER "$ADMIN_USER"
set_kv ADMIN_PASS "$ADMIN_PASS"
set_kv SECRET_KEY "$SECRET_KEY"
set_kv MANAGER_PORT "$PORT"
set_kv MOCK_DOCKER "false"
mkdir -p data backups

echo "==> [4/5] Starting GameHub..."
docker compose up -d --build
sleep 3
docker compose ps || true

if [[ "$WITH_PLAYIT" == "true" ]]; then
  echo "==> [5/5] Installing playit.gg agent (CGNAT bypass)..."
  if ! command -v playit >/dev/null; then
    curl -SsL https://playit-cloud.github.io/ppa/key.gpg | gpg --dearmor | tee /etc/apt/trusted.gpg.d/playit.gpg >/dev/null
    echo "deb [signed-by=/etc/apt/trusted.gpg.d/playit.gpg] https://playit-cloud.github.io/ppa/data ./" | tee /etc/apt/sources.list.d/playit-cloud.list
    apt-get update -y && apt-get install -y playit
  fi
  if [[ -n "$PLAYIT_SECRET" ]]; then
    # run as systemd service with secret (non-interactive)
    cat > /etc/systemd/system/playit.service <<EOF
[Unit]
Description=playit.gg agent (GameHub CGNAT bypass)
After=network-online.target
Wants=network-online.target
[Service]
ExecStart=/usr/bin/playit --secret $PLAYIT_SECRET
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload; systemctl enable --now playit
    echo "playit service started with provided secret."
  else
    echo ""
    echo "--- PLAYIT CLAIM NEEDED (one time) ---"
    echo "Run: sudo playit"
    echo "It prints https://playit.gg/claim/XXXX -> open it, login, claim agent."
    echo "Then create 2 UDP tunnels in playit.gg dashboard:"
    echo "  1) UDP  127.0.0.1:8211  (Palworld game, region Asia)"
    echo "  2) UDP  127.0.0.1:27015 (Palworld query, region Asia)"
    echo "Give players the playit address (e.g. xxx.asia.playit.gg:port)."
    echo "Keep it running: use tmux, or re-run with --playit-secret KEY for systemd mode."
  fi
else
  echo "==> [5/5] Skipped playit (re-run with --with-playit for CGNAT)."
fi

echo ""
echo "================ DONE ================"
echo "GameHub UI: http://$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}'):${PORT}"
echo "Login: ${ADMIN_USER} / ${ADMIN_PASS}"
[[ "$GENERATED" == "true" ]] && echo "(generated password shown above - save it! also stored in .env)"
echo ""
echo "Next: login -> + New -> Palworld, name palworld-1, set RCON password -> Create."
if [[ "$WITH_PLAYIT" == "true" && -z "$PLAYIT_SECRET" ]]; then
  echo "Then: sudo playit  (claim agent + add the 2 UDP tunnels above)."
fi
echo "RCON port 25575 stays internal - do NOT tunnel it."

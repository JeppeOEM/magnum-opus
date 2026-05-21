#!/usr/bin/env bash
# setup-vps.sh — guided first-time VPS provisioning
#
# Usage:
#   bash scripts/setup-vps.sh <vps-public-ip>
#
# Each step shows exactly what it will run and asks for confirmation.
# You can skip any step with 's'. Resume after interruption with the same command.
#
# State is saved to .setup-state (gitignored) so re-running skips completed steps.

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*" >&2; exit 1; }

# ── Args ───────────────────────────────────────────────────────────────────────
ROOT_IP="${1:-}"
if [ -z "$ROOT_IP" ]; then
  echo "Usage: bash scripts/setup-vps.sh <vps-public-ip>"
  echo ""
  echo "  Example: bash scripts/setup-vps.sh 45.56.78.90"
  echo ""
  echo "  Requires:"
  echo "    - SSH key installed on VPS root user (Linode console → SSH keys)"
  echo "    - Tailscale account created at https://tailscale.com"
  echo "    - .env file in repo root (copy from .env.example and fill in)"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STATE_FILE="${REPO_ROOT}/.setup-state"
VPS_DIR="${VPS_DIR:-/home/deploy/magnum-opus}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"

# ── State helpers ──────────────────────────────────────────────────────────────
step_done() {
  grep -q "^${1}=done" "$STATE_FILE" 2>/dev/null
}
mark_done() {
  grep -v "^${1}=" "$STATE_FILE" 2>/dev/null > "${STATE_FILE}.tmp" || true
  echo "${1}=done" >> "${STATE_FILE}.tmp"
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
}
save_var() {
  grep -v "^${1}=" "$STATE_FILE" 2>/dev/null > "${STATE_FILE}.tmp" || true
  echo "${1}=${2}" >> "${STATE_FILE}.tmp"
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
}
load_var() {
  grep "^${1}=" "$STATE_FILE" 2>/dev/null | cut -d= -f2- || echo ""
}

# ── SSH helpers ────────────────────────────────────────────────────────────────
ssh_root()   { ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "root@${ROOT_IP}" "$@"; }
ssh_deploy() { ssh -o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new "${DEPLOY_USER}@${TAILSCALE_IP:-$ROOT_IP}" "$@"; }

# ── Step runner ────────────────────────────────────────────────────────────────
STEP_NUM=0
STEP_KEY=""

begin_step() {
  STEP_NUM=$((STEP_NUM + 1))
  STEP_KEY="$1"
  local title="$2"

  echo ""
  echo -e "${BOLD}┌─────────────────────────────────────────────────────────────┐${NC}"
  printf "${BOLD}│  Step %-2d: %-52s│${NC}\n" "$STEP_NUM" "$title"
  echo -e "${BOLD}└─────────────────────────────────────────────────────────────┘${NC}"

  if step_done "$STEP_KEY"; then
    ok "Already completed — skipping"
    echo ""
    return 1  # caller should check and skip body
  fi
  return 0
}

show_commands() {
  echo ""
  echo -e "  ${DIM}Commands to run:${NC}"
  while IFS= read -r line; do
    echo -e "    ${DIM}${line}${NC}"
  done <<< "$1"
  echo ""
}

confirm_run() {
  local prompt="${1:-Run this step?}"
  printf "  ${BOLD}%s [Y/n/s=skip]${NC} " "$prompt"
  read -r reply
  case "${reply,,}" in
    ""| y | yes) return 0 ;;
    s | skip)    warn "Skipped"; return 1 ;;
    *)           fail "Aborted" ;;
  esac
}

confirm_pause() {
  echo ""
  echo -e "  ${YELLOW}${BOLD}ACTION REQUIRED${NC}"
  echo -e "  $*"
  echo ""
  printf "  Press Enter when done (Ctrl+C to abort)..."
  read -r
  echo ""
}

# ── Load persisted state ───────────────────────────────────────────────────────
touch "$STATE_FILE"
TAILSCALE_IP=$(load_var TAILSCALE_IP)

# ── Banner ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  magnum-opus — First-Time VPS Setup${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  VPS IP (public):  ${CYAN}${ROOT_IP}${NC}"
echo -e "  Repo dir on VPS:  ${CYAN}${VPS_DIR}${NC}"
echo -e "  Deploy user:      ${CYAN}${DEPLOY_USER}${NC}"
[ -n "$TAILSCALE_IP" ] && echo -e "  Tailscale IP:     ${CYAN}${TAILSCALE_IP}${NC} (from previous run)"
echo ""
echo -e "  State file: ${DIM}${STATE_FILE}${NC}"
echo -e "  Delete it to reset and re-run all steps from scratch."
echo ""
printf "  ${BOLD}Start setup? [Y/n]${NC} "
read -r reply
[[ "${reply,,}" =~ ^(y|yes|)$ ]] || exit 0
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Pre-flight
# ══════════════════════════════════════════════════════════════════════════════
if begin_step PREFLIGHT "Pre-flight checks"; then
  show_commands "ssh root@${ROOT_IP} 'echo ok'
check for: git, ssh, curl, make"

  if confirm_run; then
    for cmd in ssh git curl make; do
      command -v "$cmd" &>/dev/null && ok "${cmd} found" || fail "${cmd} not found — install it first"
    done

    info "Testing SSH to root@${ROOT_IP}..."
    ssh_root 'echo ok' > /dev/null && ok "SSH to root@${ROOT_IP} works" || \
      fail "Cannot SSH to root@${ROOT_IP} — check your SSH key is added to the Linode instance"

    info "Checking .env file..."
    [ -f "${REPO_ROOT}/.env" ] && ok ".env found" || \
      fail ".env not found — copy .env.example to .env and fill in your API keys"

    mark_done PREFLIGHT
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — System update + essentials
# ══════════════════════════════════════════════════════════════════════════════
if begin_step SYSUPDATE "System update + essential packages"; then
  show_commands "apt-get update -qq
apt-get upgrade -y
apt-get install -y curl git make ufw python3 jq"

  if confirm_run; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get upgrade -y -qq
      apt-get install -y -qq curl git make ufw python3 jq ca-certificates gnupg lsb-release
      echo "Packages installed."
ENDSSH
    ok "System updated"
    mark_done SYSUPDATE
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Docker
# ══════════════════════════════════════════════════════════════════════════════
if begin_step DOCKER "Install Docker Engine"; then
  show_commands "# Add Docker's official GPG key and repository
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo 'deb [arch=amd64 signed-by=...] https://download.docker.com/linux/ubuntu ...' > /etc/apt/sources.list.d/docker.list
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
systemctl enable --now docker"

  if confirm_run; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      if command -v docker &>/dev/null; then
        echo "Docker already installed: $(docker --version)"
        exit 0
      fi
      export DEBIAN_FRONTEND=noninteractive
      install -m 0755 -d /etc/apt/keyrings
      curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
      apt-get update -qq
      apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
      systemctl enable --now docker
      docker --version
      echo "Docker installed."
ENDSSH
    ok "Docker installed"
    mark_done DOCKER
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Swap (1 GB)
# ══════════════════════════════════════════════════════════════════════════════
if begin_step SWAP "Create 1 GB swap file (needed for Go builds on small VPS)"; then
  show_commands "fallocate -l 1G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab"

  if confirm_run; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      if swapon --show | grep -q /swapfile; then
        echo "Swap already active."
      else
        fallocate -l 1G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
        echo "Swap created."
      fi
      free -h
ENDSSH
    ok "Swap configured"
    mark_done SWAP
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — Create deploy user
# ══════════════════════════════════════════════════════════════════════════════
if begin_step DEPLOY_USER "Create 'deploy' user with Docker group access"; then
  show_commands "useradd -m -s /bin/bash deploy
usermod -aG docker deploy
# Copy root's authorized_keys so your SSH key works for deploy user too
mkdir -p /home/deploy/.ssh
cp /root/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys"

  if confirm_run; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      if id deploy &>/dev/null; then
        echo "User 'deploy' already exists."
      else
        useradd -m -s /bin/bash deploy
        echo "User 'deploy' created."
      fi
      usermod -aG docker deploy
      mkdir -p /home/deploy/.ssh
      [ -f /root/.ssh/authorized_keys ] && \
        cp /root/.ssh/authorized_keys /home/deploy/.ssh/authorized_keys
      chown -R deploy:deploy /home/deploy/.ssh
      chmod 700 /home/deploy/.ssh
      chmod 600 /home/deploy/.ssh/authorized_keys 2>/dev/null || true
      echo "deploy user configured. Groups: $(groups deploy)"
ENDSSH
    ok "deploy user created"
    mark_done DEPLOY_USER
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — SSH hardening
# ══════════════════════════════════════════════════════════════════════════════
if begin_step SSH_HARDEN "Harden SSH (disable password auth, keep key auth)"; then
  show_commands "sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload sshd"

  warn "This disables password SSH login. Ensure your SSH key works BEFORE confirming."
  echo ""
  info "Test your key now in another terminal: ssh ${DEPLOY_USER}@${ROOT_IP}"
  echo ""

  if confirm_run "Apply SSH hardening?"; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
      sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
      sshd -t && systemctl reload sshd
      echo "SSH hardened."
ENDSSH
    ok "SSH hardened — password auth disabled"
    mark_done SSH_HARDEN
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — UFW initial rules (allow SSH, deny everything else)
# ══════════════════════════════════════════════════════════════════════════════
if begin_step UFW_INIT "Initial UFW firewall (allow SSH port 22 only)"; then
  show_commands "ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH fallback'
ufw --force enable"

  warn "UFW will block all incoming traffic except SSH (port 22)."
  warn "Tailscale will be added in step 9. Admin ports stay blocked until then."
  echo ""

  if confirm_run; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      ufw --force reset
      ufw default deny incoming
      ufw default allow outgoing
      ufw allow 22/tcp comment 'SSH fallback — remove after Tailscale SSH verified'
      ufw --force enable
      ufw status verbose
ENDSSH
    ok "UFW enabled — only port 22 open"
    mark_done UFW_INIT
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Clone repo + .env
# ══════════════════════════════════════════════════════════════════════════════
if begin_step CLONE_REPO "Clone magnum-opus repo and upload .env"; then
  REPO_URL=$(git -C "$REPO_ROOT" remote get-url origin 2>/dev/null || echo "")
  show_commands "# On VPS as deploy user:
git clone ${REPO_URL:-<your-repo-url>} ${VPS_DIR}
chmod 700 ${VPS_DIR}
# Upload .env from local machine to VPS:
scp .env ${DEPLOY_USER}@${ROOT_IP}:${VPS_DIR}/.env
chmod 600 ${VPS_DIR}/.env"

  if [ -z "$REPO_URL" ]; then
    warn "Could not detect git remote URL. You'll need to set REPO_URL manually."
    printf "  Enter repo URL (e.g. git@github.com:user/magnum-opus.git): "
    read -r REPO_URL
  fi

  if confirm_run; then
    ssh_root bash -c "
      set -euo pipefail
      if [ -d '${VPS_DIR}' ]; then
        echo 'Repo already cloned at ${VPS_DIR}.'
        exit 0
      fi
      su - deploy -c \"git clone ${REPO_URL} ${VPS_DIR}\"
      echo 'Repo cloned.'
    "

    info "Uploading .env to VPS..."
    scp -o BatchMode=yes "${REPO_ROOT}/.env" "${DEPLOY_USER}@${ROOT_IP}:${VPS_DIR}/.env"
    ssh_root "chmod 600 ${VPS_DIR}/.env && chown deploy:deploy ${VPS_DIR}/.env"
    ok "Repo cloned and .env uploaded"
    mark_done CLONE_REPO
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 9 — Install Tailscale
# ══════════════════════════════════════════════════════════════════════════════
if begin_step TAILSCALE "Install Tailscale VPN"; then
  show_commands "curl -fsSL https://tailscale.com/install.sh | sh
tailscale up --ssh --accept-dns=false
tailscale ip -4"

  if confirm_run; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      if command -v tailscale &>/dev/null; then
        echo "Tailscale already installed: $(tailscale version)"
      else
        curl -fsSL https://tailscale.com/install.sh | sh
        echo "Tailscale installed."
      fi
      tailscale up --ssh --accept-dns=false || true
ENDSSH

    confirm_pause "Tailscale authentication required:
    1. Copy the authentication URL printed above
    2. Open it in your browser and approve the VPS
    3. Once approved, Tailscale will connect automatically"

    TAILSCALE_IP=$(ssh_root 'tailscale ip -4' 2>/dev/null | tr -d ' \n') || \
      fail "Could not get Tailscale IP — did authentication complete?"

    save_var TAILSCALE_IP "$TAILSCALE_IP"
    ok "Tailscale IP: ${TAILSCALE_IP}"
    mark_done TAILSCALE
  fi
fi

# Reload Tailscale IP (may have been set above or in a previous run)
TAILSCALE_IP=$(load_var TAILSCALE_IP)
if [ -z "$TAILSCALE_IP" ] && step_done TAILSCALE; then
  TAILSCALE_IP=$(ssh_root 'tailscale ip -4' 2>/dev/null | tr -d ' \n') || true
  [ -n "$TAILSCALE_IP" ] && save_var TAILSCALE_IP "$TAILSCALE_IP"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 10 — UFW Tailscale rules (lock down public ports)
# ══════════════════════════════════════════════════════════════════════════════
if begin_step UFW_TAILSCALE "UFW: allow all traffic on tailscale0, block admin ports from public internet"; then
  show_commands "ufw allow in on tailscale0
ufw allow out on tailscale0
# Port 22 stays open as fallback until Tailscale SSH is confirmed
ufw status verbose"

  warn "After this step, admin services (Grafana, QuestDB, Bot API) are only reachable"
  warn "via Tailscale. Ensure you are enrolled in the same tailnet before confirming."
  [ -n "$TAILSCALE_IP" ] && echo -e "\n  Tailscale IP: ${CYAN}${TAILSCALE_IP}${NC}"
  echo ""
  info "Test Tailscale SSH in another terminal first: ssh ${DEPLOY_USER}@${TAILSCALE_IP}"
  echo ""

  if confirm_run "Lock down public ports via UFW?"; then
    ssh_root bash << 'ENDSSH'
      set -euo pipefail
      ufw allow in on tailscale0
      ufw allow out on tailscale0
      ufw status verbose
ENDSSH
    ok "Tailscale interface allowed — admin ports now private"
    mark_done UFW_TAILSCALE
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 11 — First deploy (build all images)
# ══════════════════════════════════════════════════════════════════════════════
if begin_step FIRST_DEPLOY "First deploy — build and start all services on VPS"; then
  show_commands "# Runs on VPS as deploy user:
cd ${VPS_DIR}
docker compose up -d redis questdb
# wait for QuestDB health...
docker compose up -d --build aggregator candle-blue gateway bot dashboard
docker compose up -d prometheus alertmanager loki promtail grafana"

  warn "This will take 5-20 minutes on first run — downloading base images and compiling Go services."
  echo ""

  if confirm_run; then
    info "Starting infra (redis + questdb) first..."
    ssh_deploy "cd ${VPS_DIR} && docker compose up -d redis questdb"

    info "Waiting for QuestDB to be ready (up to 90s)..."
    WAITED=0
    until ssh_deploy "curl -sf 'http://localhost:9000/exec?query=select+1' > /dev/null 2>&1" || [ $WAITED -ge 30 ]; do
      printf "   waiting... %d/30\r" $WAITED
      sleep 3
      WAITED=$((WAITED + 1))
    done
    echo ""
    ok "QuestDB ready"

    info "Building and starting all services (this takes a while)..."
    ssh_deploy "cd ${VPS_DIR} && docker compose up -d --build aggregator candle-blue gateway bot dashboard"

    info "Starting monitoring stack..."
    ssh_deploy "cd ${VPS_DIR} && docker compose up -d prometheus alertmanager loki promtail grafana"

    ok "All services started"
    mark_done FIRST_DEPLOY
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 12 — systemd startup unit
# ══════════════════════════════════════════════════════════════════════════════
if begin_step SYSTEMD "Install systemd unit (auto-start on VPS reboot)"; then
  show_commands "# Creates /etc/systemd/system/magnum-opus.service
# Waits for QuestDB health before starting remaining services
systemctl daemon-reload
systemctl enable magnum-opus.service"

  if confirm_run; then
    ssh_root bash -c "
      cat > /etc/systemd/system/magnum-opus.service << 'EOF'
[Unit]
Description=magnum-opus trading stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
User=${DEPLOY_USER}
WorkingDirectory=${VPS_DIR}
ExecStartPre=/bin/sleep 10
ExecStart=/usr/bin/docker compose up -d redis questdb
ExecStart=/bin/bash -c 'max=30; i=0; until docker compose exec -T questdb curl -sf http://localhost:9000/exec?query=select+1 > /dev/null 2>&1; do i=\$((i+1)); [ \$i -ge \$max ] && exit 1; sleep 3; done'
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF
      systemctl daemon-reload
      systemctl enable magnum-opus.service
      echo 'systemd unit installed and enabled.'
      systemctl status magnum-opus.service --no-pager || true
    "
    ok "systemd unit installed — services will auto-start on reboot"
    mark_done SYSTEMD
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 13 — Cron jobs
# ══════════════════════════════════════════════════════════════════════════════
if begin_step CRON "Install cron jobs (nightly QuestDB backup, weekly Docker prune)"; then
  show_commands "# Nightly backup at 02:00 UTC:
0 2 * * * ${VPS_DIR}/scripts/backup-questdb.sh >> /var/log/questdb-backup.log 2>&1
# Weekly Docker image prune on Sunday 03:00 UTC:
0 3 * * 0 docker image prune -af --filter 'until=168h' >> /var/log/docker-prune.log 2>&1"

  if confirm_run; then
    ssh_deploy bash -c "
      (crontab -l 2>/dev/null | grep -v backup-questdb | grep -v docker-prune || true;
       echo '0 2 * * * ${VPS_DIR}/scripts/backup-questdb.sh >> /var/log/questdb-backup.log 2>&1';
       echo '0 3 * * 0 docker image prune -af --filter \"until=168h\" >> /var/log/docker-prune.log 2>&1'
      ) | crontab -
      echo 'Cron jobs installed:'
      crontab -l
    "
    ok "Cron jobs installed"
    mark_done CRON
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 14 — Smoke test
# ══════════════════════════════════════════════════════════════════════════════
if begin_step SMOKE_TEST "Smoke test — verify all services are healthy"; then
  show_commands "# Checking health endpoints via SSH (from VPS, avoids needing direct access):
curl http://localhost:8080/health   # aggregator
curl http://localhost:8090/health   # bot
curl http://localhost:8083/health   # gateway
curl http://localhost:8050/health   # dashboard
curl http://localhost:9000/exec?query=select+1  # questdb"

  if confirm_run; then
    ALL_HEALTHY=true
    check() {
      local name="$1" url="$2"
      local code
      code=$(ssh_deploy "curl -sf -o /dev/null -w '%{http_code}' '${url}' 2>/dev/null || echo 000")
      if echo "$code" | grep -q "^2"; then
        ok "${name} (${url}) — HTTP ${code}"
      else
        warn "${name} (${url}) — HTTP ${code}"
        ALL_HEALTHY=false
      fi
    }

    check "aggregator"  "http://localhost:8080/health"
    check "bot"         "http://localhost:8090/health"
    check "gateway"     "http://localhost:8083/health"
    check "dashboard"   "http://localhost:8050/health"
    check "questdb"     "http://localhost:9000/exec?query=select+1"
    check "prometheus"  "http://localhost:9090/-/healthy"

    if $ALL_HEALTHY; then
      ok "All services healthy"
    else
      warn "Some services not responding — give them 30s then check logs:"
      warn "  ssh ${DEPLOY_USER}@${TAILSCALE_IP} 'cd ${VPS_DIR} && docker compose ps'"
    fi
    mark_done SMOKE_TEST
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 15 — Save .env.deploy locally
# ══════════════════════════════════════════════════════════════════════════════
if begin_step SAVE_CONFIG "Save VPS connection config to .env.deploy"; then
  show_commands "# Creates .env.deploy in repo root (gitignored):
VPS=deploy@${TAILSCALE_IP:-<tailscale-ip>}
VPS_DIR=${VPS_DIR}
CANDLE_SLOT=blue"

  if confirm_run; then
    cat > "${REPO_ROOT}/.env.deploy" << EOF
# VPS connection config — loaded by Makefile and scripts/vps-deploy.sh
# Generated by setup-vps.sh on $(date -u +%Y-%m-%d)
VPS=${DEPLOY_USER}@${TAILSCALE_IP:-${ROOT_IP}}
VPS_DIR=${VPS_DIR}
CANDLE_SLOT=blue
EOF
    chmod 600 "${REPO_ROOT}/.env.deploy"
    ok ".env.deploy written"

    # Add to .gitignore if not already there
    if ! grep -q "^\.env\.deploy" "${REPO_ROOT}/.gitignore" 2>/dev/null; then
      echo ".env.deploy" >> "${REPO_ROOT}/.gitignore"
      ok ".env.deploy added to .gitignore"
    fi

    mark_done SAVE_CONFIG
  fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# DONE — Summary
# ══════════════════════════════════════════════════════════════════════════════
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  VPS setup complete!${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
if [ -n "$TAILSCALE_IP" ]; then
  echo -e "  ${BOLD}Access via Tailscale (all through auth proxy once 34-4 is implemented):${NC}"
  echo -e "    Aggregator:  ${CYAN}http://${TAILSCALE_IP}:8080/health${NC}"
  echo -e "    Bot API:     ${CYAN}http://${TAILSCALE_IP}:8090/health${NC}"
  echo -e "    Gateway WS:  ${CYAN}ws://${TAILSCALE_IP}:8083${NC}"
  echo -e "    Dashboard:   ${CYAN}http://${TAILSCALE_IP}:8050${NC}"
  echo -e "    Grafana:     ${CYAN}http://${TAILSCALE_IP}:3000${NC}"
  echo -e "    QuestDB:     ${CYAN}http://${TAILSCALE_IP}:9000${NC}"
  echo -e "    Prometheus:  ${CYAN}http://${TAILSCALE_IP}:9090${NC}"
  echo ""
  echo -e "  ${BOLD}SSH (via Tailscale):${NC}"
  echo -e "    ${CYAN}ssh ${DEPLOY_USER}@${TAILSCALE_IP}${NC}"
fi
echo ""
echo -e "  ${BOLD}Common operations:${NC}"
echo -e "    Deploy bot:     ${CYAN}make deploy-bot${NC}"
echo -e "    Deploy all:     ${CYAN}make deploy-all${NC}"
echo -e "    View logs:      ${CYAN}make vps-logs SERVICE=bot${NC}"
echo -e "    SSH in:         ${CYAN}make vps-ssh${NC}"
echo -e "    Service status: ${CYAN}make vps-status${NC}"
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo -e "    • Story 34-4: Deploy Go auth proxy (TOTP login for all admin services)"
echo -e "    • Story 34-5: Configure Telegram bot for alerts"
echo -e "    • Story 34-11: Set Tailscale ACLs (tag:server / tag:admin)"
echo ""
echo -e "  ${DIM}Setup state saved to ${STATE_FILE}${NC}"
echo -e "  ${DIM}Delete it if you need to re-run any steps.${NC}"
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

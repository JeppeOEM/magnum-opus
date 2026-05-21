#!/usr/bin/env bash
# bootstrap-vps.sh — one-time VPS provisioning for magnum-opus
# Run as root on a fresh Ubuntu 22.04 Linode.
# Idempotent: safe to re-run — all steps check for existing state before acting.
#
# Usage:
#   ssh root@<VPS_IP> 'bash -s' < scripts/bootstrap-vps.sh
#   # Or after copying the script to the VPS:
#   ./bootstrap-vps.sh
#
# After completion:
#   - Docker + compose plugin installed
#   - 'deploy' user created, added to docker group, SSH key auth only
#   - UFW: deny all incoming except SSH; allow all outgoing
#   - Repo cloned to /home/deploy/magnum-opus
#   - .env stub files created (fill in API keys before first deploy)
#   - Swap enabled (1 GB) for Go build headroom on small instances
#
# Next steps after bootstrap:
#   1. Add your SSH public key:
#        echo "ssh-ed25519 AAAA..." >> /home/deploy/.ssh/authorized_keys
#   2. Fill in API keys:
#        nano /home/deploy/magnum-opus/.env
#        nano /home/deploy/magnum-opus/bot-service/.env
#   3. Install Tailscale (story 34-3):
#        curl -fsSL https://tailscale.com/install.sh | sh
#   4. Run first deploy (story 34-2):
#        make deploy-all VPS=deploy@<IP>

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $*"; }
warn() { echo -e "  ${YELLOW}⚠${NC}  $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }
fail() { echo -e "  ${RED}✗${NC} $*" >&2; exit 1; }
step() { echo -e "\n${BOLD}── $* ──────────────────────────────────────────────────${NC}"; }

# ── Root check ─────────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
  fail "This script must be run as root."
fi

# ── Constants ──────────────────────────────────────────────────────────────────
DEPLOY_USER="deploy"
VPS_DIR="/home/${DEPLOY_USER}/magnum-opus"
REPO_URL="https://github.com/JeppeOEM/magnum-opus.git"
SWAP_FILE="/swapfile"
SWAP_SIZE="1G"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  magnum-opus — VPS Bootstrap${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — System update
# ══════════════════════════════════════════════════════════════════════════════
step "1/9 System update"
export DEBIAN_FRONTEND=noninteractive
info "Running apt-get update + upgrade..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl git make ufw python3 jq ca-certificates gnupg lsb-release
ok "System updated and essential packages installed"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Docker install
# ══════════════════════════════════════════════════════════════════════════════
step "2/9 Docker install"
if command -v docker &>/dev/null && docker compose version &>/dev/null 2>&1; then
  ok "Docker already installed with compose: $(docker --version)"
else
  # Remove docker.io / snap stub if present — it lacks the compose plugin
  apt-get remove -y -qq docker docker.io docker-doc docker-compose docker-compose-v2 \
    podman-docker containerd runc 2>/dev/null || true
  info "Installing Docker via official repository..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
  systemctl enable --now docker
  ok "Docker installed: $(docker --version)"
  ok "Docker Compose: $(docker compose version)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Swap
# ══════════════════════════════════════════════════════════════════════════════
step "3/9 Swap (${SWAP_SIZE} — Go build headroom)"
if swapon --show | grep -q "${SWAP_FILE}"; then
  ok "Swap already active on ${SWAP_FILE}"
elif [ -f "${SWAP_FILE}" ]; then
  # File exists but not active (e.g. missing fstab entry after reboot)
  swapon "${SWAP_FILE}"
  grep -q "${SWAP_FILE}" /etc/fstab || echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
  ok "Swap re-activated (${SWAP_FILE})"
else
  fallocate -l "${SWAP_SIZE}" "${SWAP_FILE}"
  chmod 600 "${SWAP_FILE}"
  mkswap "${SWAP_FILE}"
  swapon "${SWAP_FILE}"
  grep -q "${SWAP_FILE}" /etc/fstab || echo "${SWAP_FILE} none swap sw 0 0" >> /etc/fstab
  ok "Swap created and enabled (${SWAP_SIZE})"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — Deploy user
# ══════════════════════════════════════════════════════════════════════════════
step "4/9 Deploy user"
if id "${DEPLOY_USER}" &>/dev/null; then
  ok "User '${DEPLOY_USER}' already exists"
else
  adduser --disabled-password --gecos "" "${DEPLOY_USER}"
  ok "User '${DEPLOY_USER}' created"
fi

# Ensure docker group membership
if ! groups "${DEPLOY_USER}" | grep -q docker; then
  usermod -aG docker "${DEPLOY_USER}"
  ok "Added '${DEPLOY_USER}' to docker group"
else
  ok "'${DEPLOY_USER}' already in docker group"
fi

# Set up .ssh directory
mkdir -p "/home/${DEPLOY_USER}/.ssh"
chmod 700 "/home/${DEPLOY_USER}/.ssh"
touch "/home/${DEPLOY_USER}/.ssh/authorized_keys"
chmod 600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh"
ok ".ssh directory configured"

# Copy root's authorized_keys if deploy's is empty (so existing SSH key works)
if [ -f /root/.ssh/authorized_keys ] && \
   [ ! -s "/home/${DEPLOY_USER}/.ssh/authorized_keys" ]; then
  cp /root/.ssh/authorized_keys "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  chmod 600 "/home/${DEPLOY_USER}/.ssh/authorized_keys"
  ok "Copied root's authorized_keys to ${DEPLOY_USER}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — SSH hardening
# ══════════════════════════════════════════════════════════════════════════════
step "5/9 SSH hardening"
warn "⚠  IMPORTANT: Ensure your SSH public key is in authorized_keys before"
warn "   applying password auth disable — you WILL be locked out otherwise."
warn "   Add key: echo 'ssh-ed25519 AAAA...' >> /home/${DEPLOY_USER}/.ssh/authorized_keys"
echo ""

SSHD_CONF="/etc/ssh/sshd_config"
CHANGED=false

if grep -qE "^[#]*PasswordAuthentication " "${SSHD_CONF}"; then
  if ! grep -q "^PasswordAuthentication no" "${SSHD_CONF}"; then
    sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' "${SSHD_CONF}"
    CHANGED=true
  fi
else
  echo "PasswordAuthentication no" >> "${SSHD_CONF}"
  CHANGED=true
fi

if grep -qE "^[#]*PermitRootLogin " "${SSHD_CONF}"; then
  if ! grep -q "^PermitRootLogin prohibit-password" "${SSHD_CONF}"; then
    sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' "${SSHD_CONF}"
    CHANGED=true
  fi
else
  echo "PermitRootLogin prohibit-password" >> "${SSHD_CONF}"
  CHANGED=true
fi

if ! grep -q "^PubkeyAuthentication yes" "${SSHD_CONF}"; then
  # sed exits 0 even when no substitution was made, so check first
  if grep -qE "^[#]*PubkeyAuthentication" "${SSHD_CONF}"; then
    sed -i 's/^#*PubkeyAuthentication.*/PubkeyAuthentication yes/' "${SSHD_CONF}"
  else
    echo "PubkeyAuthentication yes" >> "${SSHD_CONF}"
  fi
  CHANGED=true
fi

# Also fix cloud-init drop-in overrides — Ubuntu 22.04 Linode ships
# /etc/ssh/sshd_config.d/50-cloud-init.conf with PasswordAuthentication yes,
# which takes precedence over the main sshd_config via Include.
for _conf in /etc/ssh/sshd_config.d/*.conf; do
  [ -f "${_conf}" ] || continue
  if grep -q "PasswordAuthentication yes" "${_conf}"; then
    sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' "${_conf}"
    ok "Fixed PasswordAuthentication override in $(basename "${_conf}")"
    CHANGED=true
  fi
done

if $CHANGED; then
  # Validate config before restarting
  sshd -t && systemctl reload sshd
  ok "SSH hardened (PasswordAuthentication no, PermitRootLogin prohibit-password)"
else
  ok "SSH already hardened (no changes needed)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — UFW baseline
# ══════════════════════════════════════════════════════════════════════════════
step "6/9 UFW baseline firewall"
apt-get install -y -qq ufw 2>/dev/null || true

ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp comment 'SSH'
ufw --force enable
ok "UFW enabled — default deny incoming, port 22 open"
ufw status

# ══════════════════════════════════════════════════════════════════════════════
# STEP 7 — Repo clone
# ══════════════════════════════════════════════════════════════════════════════
step "7/9 Clone repo"
if [ -d "${VPS_DIR}/.git" ]; then
  ok "Repo already cloned at ${VPS_DIR}"
elif [ -d "${VPS_DIR}" ]; then
  warn "${VPS_DIR} exists but is not a git repo — cloning into it"
  rmdir "${VPS_DIR}" 2>/dev/null || \
    fail "${VPS_DIR} exists and is not empty — remove it manually and re-run"
  su - "${DEPLOY_USER}" -c "git clone '${REPO_URL}' '${VPS_DIR}'"
  ok "Repo cloned to ${VPS_DIR}"
else
  su - "${DEPLOY_USER}" -c "git clone '${REPO_URL}' '${VPS_DIR}'"
  chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${VPS_DIR}"
  ok "Repo cloned to ${VPS_DIR}"
fi

# ══════════════════════════════════════════════════════════════════════════════
# STEP 8 — Env file stubs
# ══════════════════════════════════════════════════════════════════════════════
step "8/9 Env file stubs"
cd "${VPS_DIR}"

create_env_stub() {
  local dest="$1" example="$2"
  if [ -f "${dest}" ]; then
    ok "${dest} already exists — skipping"
    return
  fi
  if [ -f "${example}" ]; then
    cp "${example}" "${dest}"
    ok "Created ${dest} from ${example}"
  else
    touch "${dest}"
    ok "Created empty ${dest} (no .env.example found)"
  fi
  chmod 600 "${dest}"
  chown "${DEPLOY_USER}:${DEPLOY_USER}" "${dest}"
}

create_env_stub ".env"                   ".env.example"
create_env_stub "bot-service/.env"       "bot-service/.env.example"
create_env_stub "candle-service/.env"    "candle-service/.env.example"
create_env_stub "ml-service/.env"        "ml-service/.env.example"

# ══════════════════════════════════════════════════════════════════════════════
# STEP 9 — Summary
# ══════════════════════════════════════════════════════════════════════════════
step "9/9 Done"
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  ✓ Bootstrap complete.${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${BOLD}Service endpoints (once deployed):${NC}"
printf "  %-16s %s\n" "aggregator"    "http://localhost:8080  /health /metrics"
printf "  %-16s %s\n" "candle (blue)" "http://localhost:8081  /health /metrics"
printf "  %-16s %s\n" "bot"           "http://localhost:8090  /health /metrics"
printf "  %-16s %s\n" "ml-service"    "http://localhost:8000  /health /docs"
printf "  %-16s %s\n" "gateway"       "ws://localhost:8083"
printf "  %-16s %s\n" "dashboard"     "http://localhost:8050"
printf "  %-16s %s\n" "questdb"       "http://localhost:9000  (ILP: 9009)"
printf "  %-16s %s\n" "grafana"       "http://localhost:3000"
printf "  %-16s %s\n" "prometheus"    "http://localhost:9090"
echo ""
echo -e "  ${BOLD}Next steps:${NC}"
echo -e "  1. Add your SSH public key (if not done already):"
echo -e "       ${CYAN}echo 'ssh-ed25519 AAAA...' >> /home/${DEPLOY_USER}/.ssh/authorized_keys${NC}"
echo -e "  2. Fill in API keys:"
echo -e "       ${CYAN}nano ${VPS_DIR}/.env${NC}"
echo -e "       ${CYAN}nano ${VPS_DIR}/bot-service/.env${NC}"
echo -e "  3. Install Tailscale (story 34-3):"
echo -e "       ${CYAN}curl -fsSL https://tailscale.com/install.sh | sh${NC}"
echo -e "  4. Run first deploy (story 34-2):"
echo -e "       ${CYAN}make deploy-all VPS=${DEPLOY_USER}@<IP>${NC}"
echo ""

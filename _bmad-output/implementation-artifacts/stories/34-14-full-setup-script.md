---
id: 34-14
title: Full setup script — first-time VPS provisioning in one command
epic: 34
status: ready-for-dev
---

# Story 34-14: Full setup script

## Context

Stories 34-1 through 34-13 each have their own setup targets, but first-time provisioning requires running them in the correct order with pauses at interactive steps (Tailscale auth, TOTP QR scan, firewall confirmation). Without a guide, the operator must remember the sequence, know which steps require root vs deploy user, and know when to switch between SSH connections.

This story adds `scripts/setup-vps.sh` — a guided, sequential first-time setup script with clear checkpoint prompts, progress tracking, and a final summary of all service URLs.

## What to build

### `scripts/setup-vps.sh`

```bash
#!/usr/bin/env bash
# setup-vps.sh — guided first-time VPS provisioning for magnum-opus
#
# Usage:
#   bash scripts/setup-vps.sh <root-ip>
#
# What it does (in order):
#   1.  Pre-flight: check local deps, confirm VPS IP
#   2.  Bootstrap: system setup, Docker, deploy user, swap, UFW stub
#   3.  Deploy all services (first run — builds images)
#   4.  Tailscale install (prints auth URL, waits for operator)
#   5.  UFW firewall rules (locks down public ports)
#   6.  systemd startup unit (auto-start on reboot)
#   7.  Cron jobs (nightly backup, weekly docker prune)
#   8.  Auth proxy first run (prints TOTP QR, waits for operator)
#   9.  Smoke test all services
#   10. Print final access summary

set -euo pipefail

# ── Colours ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

ok()   { echo -e "${GREEN}✓${RESET} $*"; }
warn() { echo -e "${YELLOW}⚠${RESET}  $*"; }
info() { echo -e "${CYAN}→${RESET} $*"; }
fail() { echo -e "${RED}✗${RESET} $*"; exit 1; }
step() { echo -e "\n${BOLD}[$1/10] $2${RESET}"; }

pause() {
  echo ""
  echo -e "${YELLOW}$*${RESET}"
  read -r -p "    Press Enter when ready (Ctrl+C to abort)..."
  echo ""
}

# ── Args ───────────────────────────────────────────────────────────────────────
VPS_ROOT_IP="${1:-}"
if [ -z "$VPS_ROOT_IP" ]; then
  echo "Usage: bash scripts/setup-vps.sh <vps-public-ip>"
  echo "  Example: bash scripts/setup-vps.sh 45.56.78.90"
  exit 1
fi

VPS_DIR="${VPS_DIR:-/home/deploy/magnum-opus}"

# ── State tracking ─────────────────────────────────────────────────────────────
STATE_FILE="/tmp/magnum-opus-setup-state"
load_state() { [ -f "$STATE_FILE" ] && source "$STATE_FILE" || true; }
save_state() { echo "$1=done" >> "$STATE_FILE"; }
is_done()    { load_state; [ "${!1:-}" = "done" ]; }

# ── Step 1: Pre-flight ─────────────────────────────────────────────────────────
step 1 "Pre-flight checks"

for cmd in ssh git make curl; do
  command -v "$cmd" &>/dev/null && ok "$cmd found" || fail "$cmd not found — install it first"
done

if ! ssh -q -o BatchMode=yes -o ConnectTimeout=5 "root@${VPS_ROOT_IP}" true 2>/dev/null; then
  fail "Cannot SSH to root@${VPS_ROOT_IP} — check your SSH key and VPS IP"
fi
ok "SSH to root@${VPS_ROOT_IP} works"

echo ""
echo -e "${BOLD}Setup will configure:${RESET}"
echo "  VPS:         ${VPS_ROOT_IP}"
echo "  Deploy user: deploy"
echo "  Repo dir:    ${VPS_DIR}"
echo ""
read -r -p "Continue? [y/N] " confirm
[[ "${confirm,,}" = "y" ]] || exit 0

# ── Step 2: Bootstrap ─────────────────────────────────────────────────────────
step 2 "VPS bootstrap (Docker, swap, deploy user, SSH hardening)"
if is_done STEP_BOOTSTRAP; then
  ok "Already done — skipping"
else
  make bootstrap VPS="root@${VPS_ROOT_IP}"
  save_state STEP_BOOTSTRAP
  ok "Bootstrap complete"
fi

# Resolve deploy IP (same at this point, Tailscale IP comes later)
DEPLOY_VPS="deploy@${VPS_ROOT_IP}"

# ── Step 3: Deploy all services ────────────────────────────────────────────────
step 3 "First deploy — building all service images on VPS"
info "This will take 5-15 minutes on first run (downloading base images + compiling Go)"
if is_done STEP_DEPLOY_ALL; then
  ok "Already done — skipping"
else
  make deploy-all VPS="$DEPLOY_VPS"
  save_state STEP_DEPLOY_ALL
  ok "All services deployed"
fi

# ── Step 4: Tailscale ─────────────────────────────────────────────────────────
step 4 "Tailscale install"
if is_done STEP_TAILSCALE; then
  ok "Already done — skipping"
else
  info "Installing Tailscale on VPS..."
  make setup-tailscale VPS="root@${VPS_ROOT_IP}"

  pause "A Tailscale authentication URL was printed above.
    1. Open the URL in your browser
    2. Authenticate the VPS into your tailnet
    3. Note the Tailscale IP: ssh root@${VPS_ROOT_IP} 'tailscale ip -4'"

  TAILSCALE_IP=$(ssh "root@${VPS_ROOT_IP}" 'tailscale ip -4' 2>/dev/null) \
    || fail "Could not get Tailscale IP — did authentication complete?"
  ok "VPS Tailscale IP: ${TAILSCALE_IP}"
  echo "TAILSCALE_IP=${TAILSCALE_IP}" >> "$STATE_FILE"
  save_state STEP_TAILSCALE
fi

load_state
TAILSCALE_IP="${TAILSCALE_IP:-}"
[ -n "$TAILSCALE_IP" ] || fail "Tailscale IP not found in state — re-run from step 4"

# From here on, use Tailscale IP for all SSH
DEPLOY_VPS="deploy@${TAILSCALE_IP}"

# ── Step 5: Firewall ──────────────────────────────────────────────────────────
step 5 "UFW firewall — lock down public ports"
if is_done STEP_FIREWALL; then
  ok "Already done — skipping"
else
  warn "After this step, admin ports will only be reachable via Tailscale."
  warn "Ensure you are enrolled in the same tailnet as the VPS before continuing."
  read -r -p "  Apply firewall rules? [y/N] " confirm
  [[ "${confirm,,}" = "y" ]] || { warn "Skipped — run 'make setup-firewall' manually later"; }
  if [[ "${confirm,,}" = "y" ]]; then
    make setup-firewall VPS="$DEPLOY_VPS"
    save_state STEP_FIREWALL
    ok "Firewall applied — admin ports now private"
  fi
fi

# ── Step 6: systemd ───────────────────────────────────────────────────────────
step 6 "systemd startup unit (auto-start on reboot)"
if is_done STEP_SYSTEMD; then
  ok "Already done — skipping"
else
  make setup-systemd VPS="$DEPLOY_VPS"
  save_state STEP_SYSTEMD
  ok "systemd unit installed"
fi

# ── Step 7: Cron jobs ─────────────────────────────────────────────────────────
step 7 "Cron jobs (nightly QuestDB backup, weekly Docker prune)"
if is_done STEP_CRON; then
  ok "Already done — skipping"
else
  if ssh "$DEPLOY_VPS" "command -v s3cmd &>/dev/null"; then
    make setup-cron VPS="$DEPLOY_VPS"
    save_state STEP_CRON
    ok "Cron jobs installed"
  else
    warn "s3cmd not found on VPS — skipping backup cron"
    warn "Install: ssh ${DEPLOY_VPS} 'sudo apt install s3cmd && s3cmd --configure'"
    warn "Then run: make setup-cron VPS=${DEPLOY_VPS}"
  fi
fi

# ── Step 8: Auth proxy TOTP setup ─────────────────────────────────────────────
step 8 "Auth proxy — TOTP first-run setup"
if is_done STEP_TOTP; then
  ok "Already done — skipping"
else
  info "Starting auth proxy for first time to generate TOTP secret..."
  ssh "$DEPLOY_VPS" "cd ${VPS_DIR} && docker compose up -d auth-proxy" 2>/dev/null || true
  sleep 2
  TOTP_OUTPUT=$(ssh "$DEPLOY_VPS" "cd ${VPS_DIR} && docker compose logs auth-proxy --tail=30" 2>/dev/null)
  echo "$TOTP_OUTPUT"

  pause "The auth proxy printed a TOTP provisioning URI and QR code above.
    1. Scan the QR code with your authenticator app (Google Authenticator, Authy, etc.)
    2. Copy the TOTP secret (AUTH_TOTP_SECRET=...) from the output
    3. Add it to the .env file on the VPS:
       ssh ${DEPLOY_VPS} 'nano ${VPS_DIR}/.env'
    4. Also save the backup codes printed above in your password manager
    5. Restart the auth proxy:
       ssh ${DEPLOY_VPS} 'cd ${VPS_DIR} && docker compose up -d auth-proxy'"

  save_state STEP_TOTP
  ok "TOTP setup checkpoint passed"
fi

# ── Step 9: Smoke test ────────────────────────────────────────────────────────
step 9 "Smoke test — checking all services via Tailscale"
ALL_HEALTHY=true

check_service() {
  local name="$1" url="$2"
  if ssh "$DEPLOY_VPS" "curl -sf -o /dev/null -w '%{http_code}' ${url} 2>/dev/null" | grep -q "^2"; then
    ok "${name} healthy (${url})"
  else
    warn "${name} not responding at ${url}"
    ALL_HEALTHY=false
  fi
}

check_service "Bot API"      "http://localhost:8090/health"
check_service "Aggregator"   "http://localhost:8080/health"
check_service "Gateway"      "http://localhost:8083/health"
check_service "Dashboard"    "http://localhost:8050/health"
check_service "QuestDB"      "http://localhost:9000/exec?query=select+1"
check_service "Auth proxy"   "http://localhost:8080/auth/health"
check_service "Prometheus"   "http://localhost:9090/-/healthy"

if $ALL_HEALTHY; then
  ok "All services healthy"
else
  warn "Some services not healthy — check logs: make vps-logs SERVICE=<name> VPS=${DEPLOY_VPS}"
fi

# ── Step 10: Summary ──────────────────────────────────────────────────────────
step 10 "Setup complete"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}${BOLD}  magnum-opus VPS provisioning complete${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "  ${BOLD}Access all services via auth proxy (requires TOTP login):${RESET}"
echo -e "    Admin portal: ${CYAN}http://${TAILSCALE_IP}:8080/${RESET}"
echo -e "    Grafana:      ${CYAN}http://${TAILSCALE_IP}:8080/grafana/${RESET}"
echo -e "    QuestDB:      ${CYAN}http://${TAILSCALE_IP}:8080/questdb/${RESET}"
echo -e "    Dashboard:    ${CYAN}http://${TAILSCALE_IP}:8080/dashboard/${RESET}"
echo -e "    Prometheus:   ${CYAN}http://${TAILSCALE_IP}:8080/prometheus/${RESET}"
echo -e "    Bot API:      ${CYAN}http://${TAILSCALE_IP}:8080/bot/${RESET}"
echo ""
echo -e "  ${BOLD}SSH (via Tailscale — no public port needed):${RESET}"
echo -e "    ${CYAN}ssh deploy@${TAILSCALE_IP}${RESET}"
echo ""
echo -e "  ${BOLD}Common operations:${RESET}"
echo -e "    Deploy bot:   ${CYAN}make deploy-bot VPS=deploy@${TAILSCALE_IP}${RESET}"
echo -e "    View logs:    ${CYAN}make vps-logs SERVICE=bot VPS=deploy@${TAILSCALE_IP}${RESET}"
echo -e "    SSH in:       ${CYAN}make vps-ssh VPS=deploy@${TAILSCALE_IP}${RESET}"
echo ""
echo -e "  ${BOLD}Save this to your .env.deploy:${RESET}"
echo -e "    ${CYAN}VPS=deploy@${TAILSCALE_IP}${RESET}"
echo -e "    ${CYAN}VPS_DIR=${VPS_DIR}${RESET}"
echo ""
echo -e "  ${BOLD}Next: configure Tailscale ACLs (story 34-11)${RESET}"
echo -e "    Tag VPS as tag:server, your device as tag:admin"
echo -e "    https://login.tailscale.com/admin/machines"
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

rm -f "$STATE_FILE"
```

### Makefile target

```makefile
## First-time VPS provisioning — guided sequential setup
##   Runs bootstrap → deploy-all → tailscale → firewall → systemd → cron → TOTP → smoke test
##   Usage: make setup-vps VPS_IP=45.56.78.90
setup-vps:
	@[ -n "$(VPS_IP)" ] || (echo "Usage: make setup-vps VPS_IP=<public-ip>"; exit 1)
	bash scripts/setup-vps.sh $(VPS_IP)
```

### Resume behaviour (state file)

The script writes completed steps to `/tmp/magnum-opus-setup-state`. If the script is interrupted (Ctrl+C, SSH drop, Tailscale auth timeout), re-running it skips already-completed steps and picks up where it left off:

```
$ bash scripts/setup-vps.sh 45.56.78.90
[1/10] Pre-flight checks
✓ Already done — skipping   ← bootstrap
✓ Already done — skipping   ← deploy-all
→ Step 4: Tailscale ...      ← picks up here after interruption
```

State file is removed on successful completion. To force a full re-run: `rm /tmp/magnum-opus-setup-state`.

### `docs/ops.md` — first-time setup section

Add at the top of the ops runbook:

```markdown
## First-Time VPS Setup

Complete provisioning in one guided command:

```bash
# Prerequisites:
# - SSH key added to Linode VPS (root access)
# - Tailscale account created (https://tailscale.com)
# - .env file prepared from .env.example

make setup-vps VPS_IP=<your-linode-ip>
```

The script walks through 10 steps, pausing at interactive checkpoints:
- **Step 4** — Visit the Tailscale auth URL in your browser
- **Step 8** — Scan the TOTP QR code with your authenticator app

Total time: 20-30 minutes (mostly waiting for Docker builds on first run).

If interrupted, re-run the same command — completed steps are skipped automatically.

### Prerequisites checklist

Before running `make setup-vps`:

- [ ] Linode VPS created (Ubuntu 22.04, 4GB+ RAM recommended)
- [ ] SSH key added to VPS root user in Linode console
- [ ] `ssh root@<ip>` works from your machine
- [ ] Tailscale account created at https://tailscale.com
- [ ] `.env` file copied from `.env.example` and filled in:
  - `BYBIT_API_KEY` / `BYBIT_API_SECRET` (or KuCoin equivalents)
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID_URGENT`, `TELEGRAM_CHAT_ID_DIGEST`
  - `AUTH_SESSION_KEY` (generate: `openssl rand -base64 32`)
  - `AUTH_MACHINE_TOKEN` (generate: `openssl rand -hex 32`)
  - `REDIS_PASSWORD` (generate: `openssl rand -hex 32`)
  - `QUESTDB_HTTP_PASSWORD`
  - `S3_BUCKET` and s3cmd configured (for backups)
- [ ] `.env.deploy` created: `VPS=deploy@<ip>` and `VPS_DIR=/home/deploy/magnum-opus`
```

## Acceptance Criteria

1. `make setup-vps VPS_IP=<ip>` runs to completion on a fresh Ubuntu 22.04 Linode without requiring any other manual commands.
2. If interrupted at any step and re-run, completed steps are skipped and the script resumes from the correct point.
3. At step 4, the script prints the Tailscale auth URL and waits for the operator to authenticate before continuing.
4. At step 8, the script shows the TOTP QR code output and waits for the operator to scan it before continuing.
5. At step 9, all running services return 2xx health checks via SSH-tunnelled curl.
6. Step 10 prints the Tailscale IP, all service URLs (via auth proxy), and the `VPS=deploy@<tailscale-ip>` value to add to `.env.deploy`.
7. State file is deleted on successful completion; re-running after full success re-runs all steps cleanly.
8. `docs/ops.md` first-time setup section lists all prerequisites with a checkbox format.

## Dev Notes

- **Root vs deploy user:** steps 1-2 (bootstrap, Tailscale install) require root. Steps 3-10 use the `deploy` user. The script switches at the right point — Tailscale install (`setup-tailscale`) uses `root@ip` explicitly in the Makefile target; all subsequent targets use `deploy@<tailscale-ip>`.
- **State file location:** `/tmp/magnum-opus-setup-state` is on the local machine (the dev workstation running the script), not the VPS. It survives SSH drops and shell exits, but not a reboot of the dev machine. For a reboot-safe alternative, store state in `.setup-state` in the repo root (gitignored).
- **Tailscale IP detection:** `tailscale ip -4` is called on the VPS after authentication. If the VPS has multiple interfaces, this returns the first IPv4 Tailscale address. If the account has MagicDNS enabled, the hostname (`magnum-opus.tail<hash>.ts.net`) can be used instead — but IP is more reliable for initial setup.
- **Auth proxy first-run exit:** the auth proxy container exits on first run when `AUTH_TOTP_SECRET` is empty (prints QR code and exits per story 34-4 design). Step 8 starts the container, captures logs, and then the operator restarts it after saving the secret. The script waits via the pause checkpoint rather than polling — the operator is the gate.
- **Idempotency:** every `make` target called from this script must be idempotent (safe to run twice). Bootstrap checks for existing Docker install before re-running. `setup-systemd` runs `systemctl daemon-reload` and `enable` which are idempotent. `deploy-all` is `compose up --build` which is idempotent.
- **`VPS_IP` vs `VPS`:** the Makefile uses `VPS=deploy@ip` as the standard parameter. This script takes a bare IP (`VPS_IP=<ip>`) as input to avoid ambiguity about whether root or deploy user is needed, then constructs the correct connection string per step.

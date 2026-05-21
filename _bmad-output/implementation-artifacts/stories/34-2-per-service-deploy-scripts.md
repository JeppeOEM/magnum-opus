---
id: 34-2
title: Per-service deploy scripts and Makefile targets
epic: 34
status: ready-for-dev
---

# Story 34-2: Per-service deploy scripts and Makefile targets

## Context

After bootstrap (34-1), the VPS has Docker, the repo, and env files. The workflow for deploying a code change is currently manual: SSH to VPS, git pull, run docker compose. This story automates that into a single local command per service.

**Key design decisions (from prior discussion):**
- Images are built on the VPS (no registry). Layer cache makes incremental builds fast after the first.
- `docker compose up -d --no-deps --build <service>` builds and restarts only the target service — other running services are unaffected.
- Downtime = container stop + start only (~2–5s for Go services, ~10s for Python). The build happens while the old container is still running.
- Candle service is a special case: delegates to the existing `scripts/deploy-candle.sh` blue-green script.
- Position safety: bot service shutdown does NOT close positions (confirmed in code review). Reconciliation on restart restores them. The monitoring gap is ~15–30s.

## What to build

### `scripts/vps-deploy.sh`

```bash
#!/usr/bin/env bash
# vps-deploy.sh — deploy one or all services to the VPS
#
# Usage:
#   ./scripts/vps-deploy.sh aggregator          # deploy one service
#   ./scripts/vps-deploy.sh bot
#   ./scripts/vps-deploy.sh candle [blue|green] # blue-green deploy (default: green→blue)
#   ./scripts/vps-deploy.sh all                 # deploy all app services in order
#   ./scripts/vps-deploy.sh infra               # restart redis + questdb (data-safe)
#   ./scripts/vps-deploy.sh monitoring          # restart prometheus/grafana/loki stack
#
# Environment:
#   VPS        SSH target, e.g. deploy@123.45.67.89  (required)
#   VPS_DIR    repo path on VPS                      (default: ~/magnum-opus)
#   SLOT       candle slot to deploy to              (default: green)
#
# Exit codes: 0 = success, 1 = error

set -euo pipefail

SERVICE="${1:?Usage: $0 <service|all> [slot]}"
VPS="${VPS:?Set VPS=deploy@<ip> or add to .env.deploy}"
VPS_DIR="${VPS_DIR:-~/magnum-opus}"
SLOT="${2:-green}"

log()  { echo "  ==> $*"; }
fail() { echo "  ✗ $*" >&2; exit 1; }

# Map service name to docker compose service name(s)
compose_service() {
  case "$1" in
    aggregator) echo "aggregator" ;;
    candle)     echo "candle-${SLOT}" ;;
    bot)        echo "bot" ;;
    gateway)    echo "gateway" ;;
    dashboard)  echo "dashboard" ;;
    infra)      echo "redis questdb" ;;
    monitoring) echo "prometheus alertmanager loki promtail grafana" ;;
    *) fail "Unknown service: $1. Valid: aggregator candle bot gateway dashboard all infra monitoring" ;;
  esac
}

deploy_one() {
  local svc="$1"

  if [[ "$svc" == "candle" ]]; then
    deploy_candle
    return
  fi

  local compose_svc
  compose_svc=$(compose_service "$svc")

  log "Pulling latest code on VPS"
  ssh "$VPS" "cd $VPS_DIR && git pull --ff-only"

  log "Building and restarting $svc (others unaffected)"
  ssh "$VPS" "cd $VPS_DIR && docker compose up -d --no-deps --build $compose_svc"

  log "Waiting for $svc to become healthy"
  sleep 3
  ssh "$VPS" "cd $VPS_DIR && docker compose ps $compose_svc"
}

deploy_candle() {
  local old_slot
  old_slot=$( [[ "$SLOT" == "green" ]] && echo "blue" || echo "green" )

  log "Candle blue-green deploy: $old_slot → $SLOT"
  ssh "$VPS" "cd $VPS_DIR && git pull --ff-only"
  # Rebuild the incoming slot image first (build while old slot is live)
  ssh "$VPS" "cd $VPS_DIR && docker compose build candle-${SLOT}"
  # Delegate to existing blue-green script
  ssh "$VPS" "cd $VPS_DIR && ./scripts/deploy-candle.sh $SLOT $old_slot"
}

deploy_all() {
  log "Deploying all services"
  ssh "$VPS" "cd $VPS_DIR && git pull --ff-only"

  # Deploy in dependency order; each rebuilds only its own image
  for svc in aggregator candle-blue gateway bot dashboard; do
    log "  rebuilding $svc"
    ssh "$VPS" "cd $VPS_DIR && docker compose up -d --no-deps --build $svc 2>&1 | tail -3"
    sleep 2
  done

  log "All services deployed"
  ssh "$VPS" "cd $VPS_DIR && docker compose ps"
}

case "$SERVICE" in
  all)  deploy_all ;;
  *)    deploy_one "$SERVICE" ;;
esac

echo ""
echo "✓ Done."
```

### `Makefile` — deploy targets

Add a `# ── VPS Deploy ──` section after the existing `# ── Dev ──` section:

```makefile
# ── VPS Deploy ────────────────────────────────────────────────────────────────
# Set VPS in your shell or .env.deploy: export VPS=deploy@123.45.67.89
# Load with: source .env.deploy

-include .env.deploy   # optional: VPS=deploy@<ip>  VPS_DIR=~/magnum-opus

## Deploy aggregator to VPS (build on VPS, zero downtime to other services)
deploy-aggregator:
	./scripts/vps-deploy.sh aggregator

## Deploy candle service to VPS via blue-green (default: green slot)
deploy-candle:
	./scripts/vps-deploy.sh candle $(SLOT)

## Deploy bot service to VPS (positions NOT closed — reconciliation restores them)
deploy-bot:
	./scripts/vps-deploy.sh bot

## Deploy gateway to VPS
deploy-gateway:
	./scripts/vps-deploy.sh gateway

## Deploy dashboard to VPS
deploy-dashboard:
	./scripts/vps-deploy.sh dashboard

## Deploy all app services to VPS in dependency order
deploy-all:
	./scripts/vps-deploy.sh all

## Tail logs for a service on VPS: make vps-logs SERVICE=bot
vps-logs:
	ssh $(VPS) "cd $(VPS_DIR) && docker compose logs -f --tail=50 $(SERVICE)"

## Show status of all containers on VPS
vps-status:
	ssh $(VPS) "cd $(VPS_DIR) && docker compose ps"

## Open a shell on VPS
vps-ssh:
	ssh $(VPS)
```

### `.env.deploy.example`

New file (gitignored) for VPS connection config:

```bash
# Copy to .env.deploy and fill in — sourced by Makefile automatically
# Never commit this file.
VPS=deploy@123.45.67.89
VPS_DIR=~/magnum-opus
```

Add `.env.deploy` to `.gitignore`.

## Acceptance Criteria

1. `make deploy-bot VPS=deploy@<ip>` SSHes to VPS, runs `git pull`, rebuilds and restarts the `bot` container, and shows `docker compose ps` output. Other running containers are unaffected.
2. `make deploy-candle VPS=deploy@<ip>` delegates to `scripts/deploy-candle.sh` for zero-downtime blue-green.
3. `make deploy-all VPS=deploy@<ip>` deploys all app services in dependency order.
4. `make vps-status VPS=deploy@<ip>` shows `docker compose ps` output.
5. `make vps-logs SERVICE=aggregator VPS=deploy@<ip>` tails aggregator logs on the VPS.
6. `.env.deploy` is listed in `.gitignore` and never committed.
7. `scripts/vps-deploy.sh` exits non-zero on unknown service name.
8. `make bootstrap VPS=root@<ip>` runs the story 34-1 bootstrap script (carry over from 34-1).

## Dev Notes

- `git pull --ff-only` (not `git pull`) prevents silent merge commits on the VPS if someone pushes a non-linear history. If it fails, the operator is warned and the deploy stops before any container is touched.
- The deploy script uses `-d` (detached) so SSH doesn't block on the running container. `--no-deps` is critical — without it, compose would also restart dependency services (redis, questdb).
- For the `bot` service: the 45s `stop_grace_period` in compose means Docker waits up to 45s for clean shutdown. The deploy script's `sleep 3` before showing `ps` is advisory only — the container may still be starting.
- `infra` target (redis + questdb) is intentionally not in `deploy-all` — data services should only be restarted deliberately. Redis and QuestDB have named volumes; `docker compose up -d --no-deps redis questdb` does not lose data.
- The `.env.deploy` file approach avoids having to type `VPS=...` on every command. The `-include` directive silently skips the file if absent.
- `vps-deploy.sh candle` defaults `SLOT=green` because the convention is: blue is the initially-running slot, green is the first deploy target. Override with `make deploy-candle SLOT=blue`.

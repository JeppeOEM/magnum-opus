---
id: 34-6
title: Deploy safety — open position warning, confirm pause, rollback, smoke test
epic: 34
status: ready-for-dev
---

# Story 34-6: Deploy safety

## Context

The deploy scripts from story 34-2 get code onto the VPS but have no safety nets. A `make deploy-bot` while positions are open will restart the bot mid-trade — this is acceptable (reconciliation restores state), but the operator should be aware it's happening. Build failures leave the old container stopped and the new image broken. And there's no way to quickly revert to the previous working image. This story adds three layers of deploy safety: an informational position warning, a confirm pause with context, and a rollback command.

**Design principle:** warn, don't block. Open positions are not a reason to abort — the bot will reconnect and reconcile. The warning exists so the operator doesn't accidentally deploy mid-trade without realizing it.

## What to build

### Position warning in `scripts/vps-deploy.sh`

Before deploying the bot service, check for open positions and warn (never block):

```bash
warn_open_positions() {
  local open_count
  open_count=$(ssh "$VPS" "cd $VPS_DIR && \
    docker compose exec -T questdb curl -sf \
    'http://localhost:9000/exec?query=select+count()+from+orders+where+status+not+in+(%27filled%27%2C%27cancelled%27%2C%27rejected%27)' \
    2>/dev/null | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d[\"dataset\"][0][0])' \
    2>/dev/null || echo 0")
  if [ "${open_count:-0}" -gt 0 ]; then
    echo "⚠️  WARNING: $open_count open order(s) in QuestDB."
    echo "   Bot will reconcile state on restart — positions are NOT closed."
    echo "   Proceeding in 3 seconds... (Ctrl+C to abort)"
    sleep 3
  fi
}
```

Call `warn_open_positions` before deploying the `bot` service only.

### Confirm pause with context

After the warning check (or immediately for non-bot services), print a deploy summary and pause:

```bash
confirm_deploy() {
  local service="$1"
  local vps_ip
  vps_ip=$(ssh "$VPS" "tailscale ip -4 2>/dev/null || hostname -I | awk '{print \$1}'")
  local commit_sha
  commit_sha=$(git rev-parse --short HEAD)
  local branch
  branch=$(git rev-parse --abbrev-ref HEAD)

  echo ""
  echo "┌─────────────────────────────────────────┐"
  echo "│  Deploy: $service"
  printf "│  VPS:    %-32s│\n" "$vps_ip"
  printf "│  Commit: %-32s│\n" "$commit_sha ($branch)"
  echo "└─────────────────────────────────────────┘"
  echo ""
  printf "Deploy in 3s... (Ctrl+C to abort) "
  for i in 3 2 1; do printf "%d " $i; sleep 1; done
  echo "→ deploying"
}
```

### Smoke test after deploy

After `docker compose up -d --no-deps --build <service>`, poll the service health endpoint to confirm it came up:

```bash
smoke_test() {
  local service="$1"
  local health_url="$2"
  local max_attempts=20
  local attempt=0

  echo "==> Smoke test: $health_url"
  while [ $attempt -lt $max_attempts ]; do
    attempt=$((attempt + 1))
    status=$(ssh "$VPS" "curl -sf -o /dev/null -w '%{http_code}' $health_url 2>/dev/null || echo 000")
    if [ "$status" = "200" ]; then
      echo "✓ $service healthy (attempt $attempt)"
      return 0
    fi
    printf "   Waiting... attempt %d/%d (HTTP %s)\r" "$attempt" "$max_attempts" "$status"
    sleep 3
  done
  echo ""
  echo "✗ $service did not become healthy after $((max_attempts * 3))s"
  echo "  Check logs: make vps-logs SERVICE=$service"
  return 1
}

# Health URLs per service (on internal docker network, check via questdb exec or SSH curl):
# bot:        http://localhost:8090/health
# aggregator: http://localhost:8080/health
# dashboard:  http://localhost:8050/health
# gateway:    http://localhost:8083/health
```

Services without health endpoints (e.g. infra, monitoring) skip the smoke test.

### Rollback command

Tag the image before building so rollback is a one-liner:

```bash
# In vps-deploy.sh, before building:
tag_current_image() {
  local service="$1"
  ssh "$VPS" "cd $VPS_DIR && \
    docker tag magnum-opus-${service}:latest magnum-opus-${service}:rollback 2>/dev/null || true"
}

# After build, if smoke test fails:
rollback_if_failed() {
  local service="$1"
  if ! smoke_test "$service" "$health_url"; then
    echo "==> Smoke test failed. Rolling back to previous image..."
    ssh "$VPS" "cd $VPS_DIR && \
      docker tag magnum-opus-${service}:rollback magnum-opus-${service}:latest && \
      docker compose up -d --no-deps $service"
    echo "✗ Deploy failed — rolled back to previous image"
    telegram_notify "❌ *${SERVICE} deploy FAILED — rolled back*\nCommit: \`${commit_sha}\`"
    exit 1
  fi
}
```

### `Makefile` — rollback target

```makefile
## Roll back a service to its previous image (e.g. after a failed deploy)
rollback:
	@[ -n "$(SERVICE)" ] || (echo "Usage: make rollback SERVICE=bot"; exit 1)
	ssh $(VPS) "cd $(VPS_DIR) && \
	  docker tag magnum-opus-$(SERVICE):rollback magnum-opus-$(SERVICE):latest && \
	  docker compose up -d --no-deps $(SERVICE)"
	@echo "✓ $(SERVICE) rolled back"

## Tail logs for a specific service on VPS
vps-logs:
	@[ -n "$(SERVICE)" ] || (echo "Usage: make vps-logs SERVICE=bot"; exit 1)
	ssh $(VPS) "cd $(VPS_DIR) && docker compose logs --tail=100 -f $(SERVICE)"
```

### `scripts/vps-deploy.sh` — complete safety flow

```bash
deploy_service() {
  local service="$1"
  local health_url="$2"

  tag_current_image "$service"

  if [ "$service" = "bot" ]; then
    warn_open_positions
  fi

  confirm_deploy "$service"

  echo "==> Building and starting $service"
  ssh "$VPS" "cd $VPS_DIR && git pull --ff-only && \
    docker compose up -d --no-deps --build $service"

  if [ -n "$health_url" ]; then
    rollback_if_failed "$service"
  fi

  echo "✓ $service deployed"
  telegram_notify "✅ *${service} deployed*
Commit: \`$(git rev-parse --short HEAD)\`
Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
}
```

## Acceptance Criteria

1. `make deploy-bot` with open orders in QuestDB prints the open order count warning, waits 3 seconds, then deploys (does NOT abort).
2. `make deploy-aggregator` shows the confirm pause with VPS IP and commit SHA, no position warning.
3. After a successful bot deploy, the script polls `http://localhost:8090/health` and exits 0 when it returns 200.
4. If the smoke test fails (service crashes on start), the script automatically runs rollback and exits non-zero.
5. `make rollback SERVICE=bot` on the VPS restores the previous image and restarts the service.
6. `make vps-logs SERVICE=bot` tails the bot container logs.
7. A failed deploy sends a failure+rollback notification to the Telegram digest channel (if token is set).

## Dev Notes

- **Open position check via QuestDB REST:** the bot's QuestDB table `orders` tracks order status. The check is informational — if QuestDB is unreachable, the count defaults to 0 and the deploy continues. Never let the safety check itself cause a deploy failure.
- **Rollback tag lifecycle:** the `rollback` tag is overwritten on every successful deploy (tag-before-build). If two deploys happen quickly, you can only roll back one step. This is acceptable for a single-operator system.
- **Smoke test port access:** the health check runs via `ssh → curl localhost:<port>` on the VPS, not from the dev machine. This works even after Tailscale+UFW lockdown because it's local on the VPS.
- **Candle service uses blue-green:** `deploy-candle` delegates to `scripts/deploy-candle.sh` which already has a promote+shadow mechanism. Do not add position warnings or rollback to that path.
- **Confirm pause is never interactive:** no `read -p "continue? [y/N]"` — the operator's default action is always deploy. Ctrl+C is the escape hatch. Interactive prompts break `make deploy-all`.
- **Telegram integration:** the `telegram_notify` function from story 34-5 must be sourced or inlined. If `TELEGRAM_BOT_TOKEN` is empty, `telegram_notify` is a no-op.

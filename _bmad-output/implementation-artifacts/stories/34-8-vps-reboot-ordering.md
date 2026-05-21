---
id: 34-8
title: VPS reboot ordering — systemd unit, QuestDB memory limit, startup health gates
epic: 34
status: ready-for-dev
---

# Story 34-8: VPS reboot ordering

## Context

After a VPS reboot, `docker compose up` runs immediately but `depends_on` health conditions are evaluated at start time, not enforced as a gate that holds downstream containers until upstream is healthy. On a freshly booted system, QuestDB takes 20-30 seconds to become ready, Redis takes 5 seconds. The bot service starts before QuestDB is accepting connections, the startup reconciliation fails, and the bot crash-loops until QuestDB is ready.

Additionally, on small Linodes (4GB RAM), QuestDB's default memory configuration (`cairo.max.uncommitted.rows=2000000`, no JVM heap limit) can OOM-kill the QuestDB process, which silently drops fills.

This story adds a systemd unit that starts Compose after dependencies are ready, correct QuestDB memory limits for the VPS, and a startup script that gates bot startup on health checks.

## What to build

### `scripts/setup-systemd.sh` — install systemd unit

```bash
#!/usr/bin/env bash
# setup-systemd.sh — install systemd service for docker compose auto-start
# Run as root on the VPS after story 34-1 bootstrap.
set -euo pipefail

VPS_DIR="${VPS_DIR:-/home/deploy/magnum-opus}"
DEPLOY_USER="${DEPLOY_USER:-deploy}"

cat > /etc/systemd/system/magnum-opus.service << EOF
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

# Wait for Docker to be fully ready
ExecStartPre=/bin/sleep 10

# Start infrastructure first, wait for health
ExecStart=/usr/bin/docker compose up -d redis questdb
ExecStart=/bin/bash -c '\
  max=30; i=0; \
  until docker compose exec -T questdb curl -sf http://localhost:9000/exec?query=select+1 > /dev/null 2>&1; do \
    i=$((i+1)); [ $i -ge $max ] && echo "QuestDB health timeout" && exit 1; \
    sleep 2; \
  done; \
  echo "QuestDB ready after $((i*2))s"'

# Start remaining services once infra is ready
ExecStart=/usr/bin/docker compose up -d

ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable magnum-opus.service

echo "✓ systemd service installed"
echo "  Start:   systemctl start magnum-opus"
echo "  Status:  systemctl status magnum-opus"
echo "  Logs:    journalctl -u magnum-opus -f"
```

Makefile target:
```makefile
## Install systemd auto-start unit on VPS
setup-systemd:
	ssh root@$(subst deploy@,,$(VPS)) "VPS_DIR=$(VPS_DIR) bash -s" < scripts/setup-systemd.sh
```

### QuestDB memory limits — `docker-compose.yml`

QuestDB's JVM heap and native memory must be bounded to avoid OOM on small Linodes:

```yaml
  questdb:
    image: questdb/questdb:8.2.1
    environment:
      # Heap: 512MB on 4GB Linode, 1GB on 8GB+ Linode
      QDB_CAIRO_MAX_UNCOMMITTED_ROWS: 10000        # was 2000000 — reduces WAL buffer
      JAVA_OPTS: >-
        -Xms256m
        -Xmx512m
        -XX:+UseG1GC
        -XX:MaxGCPauseMillis=200
      QDB_PG_NET_RECV_BUFFER_SIZE: 1048576
      QDB_PG_NET_SEND_BUFFER_SIZE: 1048576
    deploy:
      resources:
        limits:
          memory: 1g          # hard Docker limit — OOM kill before host is affected
          cpus: "1.0"
    ...
```

`QDB_CAIRO_MAX_UNCOMMITTED_ROWS: 10000` dramatically reduces WAL memory usage. The default of 2M rows at ~100 bytes/row = 200MB just for uncommitted WAL.

### Bot startup health gate — `scripts/vps-deploy.sh`

The smoke test from story 34-6 already waits for the bot's `/health` endpoint. On reboot, the systemd unit handles the ordering. But when deploying the bot manually, also ensure QuestDB is ready:

```bash
wait_for_questdb() {
  local max=20
  local i=0
  echo "==> Waiting for QuestDB..."
  until ssh "$VPS" "curl -sf 'http://localhost:9000/exec?query=select+1' > /dev/null 2>&1"; do
    i=$((i+1))
    [ $i -ge $max ] && echo "✗ QuestDB not ready after $((max*3))s" && exit 1
    sleep 3
  done
  echo "✓ QuestDB ready"
}
```

Call `wait_for_questdb` before deploying the bot service.

### Auth proxy startup failure mode

If the auth proxy fails to start (bad TOTP secret, missing env var), all admin services become unreachable. Add a fallback note to `docs/ops.md`:

```markdown
### Auth proxy locked out / won't start

If the auth proxy container is crash-looping:

```bash
# Access via Tailscale SSH (bypasses the proxy entirely)
ssh deploy@<tailscale-ip>
cd ~/magnum-opus

# Check proxy logs
docker compose logs auth-proxy --tail=50

# Common causes:
# 1. AUTH_TOTP_SECRET empty → proxy printed QR code and exited (expected first-run)
# 2. AUTH_SESSION_KEY wrong length → needs 32 bytes base64
# 3. Redis unreachable → proxy fails open (should start anyway)

# Emergency: access Grafana directly on Tailscale IP (no auth proxy)
# Services are on internal Docker network but accessible on Tailscale:
# docker compose exec grafana sh -c 'curl -sf localhost:3000'
# Or: temporarily re-add port bindings to docker-compose.override.yml
```
```

### `docker-compose.yml` — startup dependencies hardened

Ensure bot service has a proper health-check dependency on QuestDB and Redis:

```yaml
  bot:
    ...
    depends_on:
      redis:
        condition: service_healthy
      questdb:
        condition: service_healthy
```

QuestDB health check (add to questdb service):
```yaml
  questdb:
    ...
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:9000/exec?query=select+1 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 30s
```

Redis already has a health check from story 34-2. If not:
```yaml
  redis:
    ...
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
```

### `docs/ops.md` — reboot procedure

```markdown
## VPS Reboot

### Controlled reboot

```bash
# Graceful: let systemd handle compose shutdown
ssh deploy@<tailscale-ip> "sudo systemctl stop magnum-opus && sudo reboot"

# After reboot, systemd starts magnum-opus automatically
# Monitor startup:
ssh deploy@<tailscale-ip> "journalctl -u magnum-opus -f"
```

### Expected startup sequence

1. systemd waits 10s for Docker
2. Redis starts (~3s), QuestDB starts (20-30s to become healthy)
3. All other services start once QuestDB is healthy
4. Bot runs startup reconciliation (~5s) and begins consuming
5. Total time from reboot to bot active: ~2-3 minutes

### Checking startup health

```bash
ssh deploy@<tailscale-ip> "cd ~/magnum-opus && docker compose ps"
# All services should show (healthy) or running
```
```

## Acceptance Criteria

1. After `make setup-systemd`, `systemctl is-enabled magnum-opus` returns `enabled`.
2. After a `sudo reboot`, all services are running and healthy within 3 minutes without manual intervention.
3. Bot service starts only after QuestDB health check passes (`condition: service_healthy`).
4. `docker compose exec questdb curl http://localhost:9000/exec?query=select+1` returns JSON without OOM events in the logs after running for 24h on a 4GB Linode.
5. `docker stats questdb` shows memory usage below 1GB (hard limit) under normal operation.
6. `journalctl -u magnum-opus` shows the QuestDB health gate waiting and confirming readiness.
7. The auth proxy lockout procedure is documented in `docs/ops.md`.

## Dev Notes

- **systemd `Type=oneshot` + `RemainAfterExit=yes`:** this pattern is correct for Compose — `ExecStart` runs once, the service stays "active" while containers are up. `systemctl stop magnum-opus` runs `ExecStop` (compose down).
- **`depends_on` in Compose vs systemd ordering:** `depends_on: condition: service_healthy` is enforced by Compose at start time. The systemd unit adds an outer gate (wait for QuestDB REST before starting the remaining services). These are complementary — Compose health conditions prevent the bot from starting before QuestDB answers, the systemd unit ensures the initial `compose up` doesn't time out the health check before QuestDB is ready.
- **QuestDB memory on 4GB Linode:** the stack at steady state uses approximately: Redis 100MB, QuestDB 700MB (with these limits), bot 200MB, aggregator 100MB, candle 150MB, dashboard 300MB, Prometheus 150MB, Loki 200MB, Grafana 150MB. Total ≈ 2.0GB. Leaves 2GB for OS, Go builds, and headroom. The Go build (`docker compose up --build`) spike can use 1GB — don't run builds while QuestDB WAL is catching up.
- **Linode 4GB vs 8GB:** if the VPS is 8GB, raise `Xmx512m` to `Xmx1g` and the Docker memory limit to `2g`. Add a note in the compose file about RAM-dependent tuning.
- **`start_period: 30s` on QuestDB healthcheck:** this prevents the health check from counting failures during the initial 30 seconds of startup. Without it, QuestDB would be marked unhealthy before it finishes loading WAL, and Compose would consider it failed.

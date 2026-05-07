# Ops Runbook — magnum-opus aggregator

## Prerequisites

- Docker Engine ≥ 24 + Compose plugin
- Linode account with a running VM (tested on Linode 4GB)
- `.env` file in the project root (`chmod 600 .env`)

---

## Deployment Procedure

### 1. Take a VM snapshot before every deploy

**Before touching a running instance**, create a Linode VM snapshot. This is the rollback point.

Via Linode Cloud Manager:
1. Navigate to the VM → **Backups** → **Take Snapshot**.
2. Label the snapshot `pre-deploy-<git-sha>` so you can identify it later.
3. Wait for the snapshot to complete (typically 1–5 minutes for a 4 GB VM).

Via Linode CLI (if installed):
```bash
linode-cli linodes snapshot <linode-id> --label "pre-deploy-$(git rev-parse --short HEAD)"
```

### 2. Pull the new image and restart

```bash
# On the Linode VM, from the project directory:
git pull origin main
docker compose pull
docker compose up -d --remove-orphans
```

Compose will perform a rolling restart of the `aggregator` container only — Redis and QuestDB are not restarted unless their image tag changed.

### 3. Verify the deployment

```bash
# Check /version reports the expected git SHA:
curl -s http://localhost:8080/version | jq .

# Check /health reports "ok" (may be "starting" for up to 90 seconds):
curl -s http://localhost:8080/health | jq .
```

Expected `/version` response:
```json
{
  "version": "v1.2.3",
  "git_sha": "abc1234",
  "build_time": "2026-05-07T12:00:00Z",
  "go_version": "go1.24.0"
}
```

Expected `/health` response once all feeds are live:
```json
{
  "status": "ok",
  "connected_feeds": 4,
  "gap_count_24h": 0,
  "uptime_seconds": 42
}
```

### 4. Monitor for regressions

Watch logs for 2–5 minutes after deploy:
```bash
docker compose logs -f aggregator
```

Check Prometheus metrics for feed state:
```bash
curl -s http://localhost:8080/metrics | grep aggregator_feed_state
```

All values should be `1`. If any are `0` after 90 seconds, a feed failed to subscribe.

---

## Rollback Procedure (target: 5 minutes)

Use this procedure when the new version is broken and the pre-deploy snapshot exists.

### 1. Stop the broken instance

SSH to the Linode VM and stop compose:
```bash
docker compose down
```

### 2. Restore the Linode VM snapshot

Via Linode Cloud Manager:
1. Navigate to the VM → **Backups**.
2. Find the `pre-deploy-<git-sha>` snapshot taken before the failed deploy.
3. Click **Restore to Existing Linode** → select the current VM → confirm.
4. The VM reboots into the previous snapshot state (~2–4 minutes).

> **Warning:** restoring a snapshot overwrites all disk state. Ensure QuestDB data is either replicated or acceptable to lose back to snapshot time.

### 3. Verify rollback

Once the VM is back online:
```bash
curl -s http://localhost:8080/version | jq .git_sha
```

Confirm the SHA matches the previous version. Feeds should reconnect within 90 seconds.

---

## Credential Rotation

### Rotating exchange API keys

1. Generate new API key/secret on the exchange (KuCoin or Bybit UI).
2. Update `.env` on the VM: replace `KUCOIN_API_KEY` / `KUCOIN_API_SECRET` / `KUCOIN_API_PASSPHRASE` (or `BYBIT_*`).
3. Restart only the aggregator (no need to touch Redis or QuestDB):
   ```bash
   docker compose restart aggregator
   ```
4. Verify `/health` returns `"status":"ok"` within 90 seconds.
5. Revoke the old API key on the exchange UI.

### Rotating Redis password

1. Update Redis config and restart Redis: `docker compose restart redis`.
2. Update `REDIS_PASSWORD` in `.env`.
3. Restart aggregator: `docker compose restart aggregator`.

### Credential safety rules

- `.env` must be `chmod 600` and owned by the deploy user.
- Never log credentials — the aggregator binary redacts all keys matching `key`, `secret`, `password`, `passphrase`, `token` substrings from slog output.
- Never commit `.env` to git — it is listed in `.gitignore`.
- Rotate credentials immediately if the VM is compromised or a snapshot is shared with an untrusted party.

---

## Resource Limits Reference

| Service    | Memory | CPUs |
|------------|--------|------|
| aggregator | 600 MB | 2.0  |
| redis      | 2 GB   | —    |
| questdb    | 8 GB   | —    |

Adjust in `docker-compose.yml` under `deploy.resources.limits` if the VM has more or less RAM.

---

## Useful Commands

```bash
# View live logs
docker compose logs -f aggregator

# Restart only the aggregator (after .env change)
docker compose restart aggregator

# Stop everything
docker compose down

# Hard reset (destroys all volumes — DATA LOSS)
docker compose down -v

# Check resource usage
docker stats
```

---
id: 34-7
title: Data persistence — Redis AOF, QuestDB nightly backup, Loki retention, restore procedure
epic: 34
status: ready-for-dev
---

# Story 34-7: Data persistence

## Context

The current stack has no data durability strategy. Redis uses the default RDB snapshot (data up to 60s old lost on crash). QuestDB WAL data is in a Docker volume with no offsite copy. Loki accumulates logs forever until disk fills. And there is no tested restore procedure — the first time anyone tries to recover is after a disaster.

This story adds four things: Redis AOF for near-zero fill data loss, nightly QuestDB backup to Linode Object Storage, Loki 14-day log retention, and a documented + tested restore procedure.

## What to build

### Redis AOF — `docker-compose.yml`

Switch Redis from default RDB to AOF with `appendfsync everysec` (1-second durability window, negligible performance impact):

```yaml
  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --auto-aof-rewrite-percentage 100
      --auto-aof-rewrite-min-size 64mb
      --save ""
    volumes:
      - redis-data:/data
    ...
```

`--save ""` disables RDB (AOF is the sole persistence mechanism). `auto-aof-rewrite` rewrites the AOF file when it doubles in size from 64MB baseline — prevents unbounded growth.

### QuestDB nightly backup — `scripts/backup-questdb.sh`

```bash
#!/usr/bin/env bash
# backup-questdb.sh — nightly QuestDB backup to Linode Object Storage
# Runs via cron on the VPS. Requires: s3cmd or aws CLI configured for Linode Object Storage.
#
# Linode Object Storage is S3-compatible. Bucket: magnum-opus-backups
# Install: apt install s3cmd && s3cmd --configure (use Linode API credentials)

set -euo pipefail

QUESTDB_DATA="/var/lib/docker/volumes/magnum-opus_questdb-data/_data"
BACKUP_DIR="/tmp/questdb-backup-$(date +%Y%m%d-%H%M%S)"
S3_BUCKET="${S3_BUCKET:-s3://magnum-opus-backups}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"

echo "==> Triggering QuestDB checkpoint"
curl -sf "http://localhost:9000/exec?query=CHECKPOINT+CREATE" > /dev/null || true

echo "==> Creating backup archive"
mkdir -p "$BACKUP_DIR"
tar -czf "${BACKUP_DIR}/questdb-data.tar.gz" -C "$(dirname $QUESTDB_DATA)" "$(basename $QUESTDB_DATA)"

echo "==> Uploading to Linode Object Storage"
s3cmd put "${BACKUP_DIR}/questdb-data.tar.gz" \
  "${S3_BUCKET}/questdb/$(basename $BACKUP_DIR)-questdb-data.tar.gz"

echo "==> Removing local temp"
rm -rf "$BACKUP_DIR"

echo "==> Pruning remote backups older than $RETENTION_DAYS days"
s3cmd ls "${S3_BUCKET}/questdb/" | awk '{print $4}' | while read -r key; do
  file_date=$(basename "$key" | cut -c1-8)
  if [ "$(date -d "$file_date" +%s 2>/dev/null || echo 0)" -lt "$(date -d "-${RETENTION_DAYS} days" +%s)" ]; then
    s3cmd del "$key"
  fi
done

echo "✓ QuestDB backup complete"
```

### Cron setup via `scripts/setup-cron.sh`

```bash
#!/usr/bin/env bash
# setup-cron.sh — install cron jobs on VPS
set -euo pipefail

DEPLOY_DIR="${VPS_DIR:-/home/deploy/magnum-opus}"

# QuestDB backup: daily at 02:00 UTC
(crontab -l 2>/dev/null | grep -v backup-questdb; \
  echo "0 2 * * * $DEPLOY_DIR/scripts/backup-questdb.sh >> /var/log/questdb-backup.log 2>&1") \
  | crontab -

# Docker image cache prune: weekly on Sunday at 03:00 UTC
(crontab -l 2>/dev/null | grep -v docker-prune; \
  echo "0 3 * * 0 docker image prune -af --filter 'until=168h' >> /var/log/docker-prune.log 2>&1") \
  | crontab -

echo "✓ Cron jobs installed:"
crontab -l
```

Makefile target:
```makefile
## Install cron jobs on VPS (backup, prune)
setup-cron:
	ssh $(VPS) "cd $(VPS_DIR) && bash scripts/setup-cron.sh"
```

### Loki retention — `monitoring/loki/loki-config.yaml`

```yaml
limits_config:
  retention_period: 336h  # 14 days

compactor:
  working_directory: /loki/compactor
  shared_store: filesystem
  retention_enabled: true
  retention_delete_delay: 2h
  retention_delete_worker_count: 150
```

Retention is enforced by the compactor. Also add compactor volume to compose:
```yaml
  loki:
    ...
    volumes:
      - loki-data:/loki
      - ./monitoring/loki/loki-config.yaml:/etc/loki/config.yaml
```

### Redis backup — AOF copy

AOF is continuous, but a daily copy protects against volume corruption:

```bash
# Add to backup-questdb.sh or separate script:
echo "==> Backing up Redis AOF"
docker exec magnum-opus-redis-1 redis-cli BGREWRITEAOF
sleep 5  # let rewrite finish
docker cp magnum-opus-redis-1:/data/appendonly.aof "${BACKUP_DIR}/redis-appendonly.aof"
s3cmd put "${BACKUP_DIR}/redis-appendonly.aof" \
  "${S3_BUCKET}/redis/$(date +%Y%m%d)-appendonly.aof"
```

### TOTP secret backup

The TOTP secret in `.env` is critical — losing it locks the operator out of the auth proxy. Document in `docs/ops.md`:

```markdown
### TOTP Secret Recovery

The `AUTH_TOTP_SECRET` value in `.env` is your master key to the auth proxy.
Store it in a password manager alongside your Tailscale login credentials.

**Backup command (run locally after initial setup):**
ssh deploy@<tailscale-ip> "grep AUTH_TOTP_SECRET ~/magnum-opus/.env" >> ~/.secrets/magnum-opus-totp.txt
```

### Restore procedure — `docs/ops.md`

Add a new section:

```markdown
## Data Recovery

### Redis restore from AOF backup

```bash
# Stop bot service first (prevents new writes during restore)
ssh deploy@<tailscale-ip> "cd ~/magnum-opus && docker compose stop bot"

# Download latest backup
s3cmd get s3://magnum-opus-backups/redis/<date>-appendonly.aof /tmp/appendonly.aof

# Copy into Redis container and reload
docker cp /tmp/appendonly.aof magnum-opus-redis-1:/data/appendonly.aof
docker compose restart redis

# Verify
docker compose exec redis redis-cli DBSIZE

# Restart bot (reconciliation runs on startup)
docker compose up -d bot
```

### QuestDB restore from backup

```bash
# Stop all services that write to QuestDB
docker compose stop candle aggregator bot

# Download and extract backup
s3cmd get s3://magnum-opus-backups/questdb/<date>-questdb-data.tar.gz /tmp/questdb-backup.tar.gz
docker compose down questdb
tar -xzf /tmp/questdb-backup.tar.gz -C /var/lib/docker/volumes/

# Start QuestDB and verify
docker compose up -d questdb
# Wait for health check, then:
curl 'http://localhost:9000/exec?query=select+count()+from+snapshot_1s' | python3 -m json.tool

# Start remaining services
docker compose up -d candle aggregator bot
```

### Linode snapshot restore (worst case: corrupted volume)

```bash
# In Linode console: restore latest disk snapshot to a new Linode
# SSH in with your key, confirm Docker and data are present
# Re-enroll in Tailscale: tailscale up --ssh --accept-dns=false
# Services start automatically via systemd (story 34-8)
```
```

## Acceptance Criteria

1. Redis starts with `appendonly yes` — `docker compose exec redis redis-cli CONFIG GET appendonly` returns `yes`.
2. After killing the Redis container and restarting, all keys written before the kill are present (test: write 10 keys, kill container, restart, verify keys exist).
3. `scripts/backup-questdb.sh` runs without error on the VPS and produces an object in the S3 bucket.
4. `crontab -l` on the VPS shows the backup and prune entries after `make setup-cron`.
5. Loki's compactor is enabled — after 14 days, log entries older than 14 days are not returned by queries (verify config, not full 14-day wait).
6. The restore procedure in `docs/ops.md` is complete and covers Redis AOF, QuestDB volume, and Linode snapshot recovery.
7. `make setup-cron VPS=...` completes without error.

## Dev Notes

- **QuestDB `CHECKPOINT CREATE`:** this is QuestDB's native checkpoint command (available since 8.x). It flushes WAL to the table directory. The backup captures the checkpoint state — consistent snapshot without stopping QuestDB. If QuestDB version is older, fall back to `BACKUP DATABASE` command.
- **s3cmd vs aws CLI:** Linode Object Storage is S3-compatible. Either tool works. `s3cmd` is simpler to configure for a single bucket. The Linode Object Storage endpoint is `https://<region>.linodeobjects.com`. Region matches the VPS region (e.g., `eu-central-1.linodeobjects.com`).
- **AOF vs RDB decision:** AOF with `everysec` loses at most 1 second of data vs RDB which can lose 60 seconds. For fill event data in Redis Streams, 1-second loss is acceptable. Bot reconciliation on restart queries both QuestDB and the exchange REST API, so even a 1-second Redis loss doesn't lose a fill — it just requires exchange reconciliation.
- **Docker prune safety:** `docker image prune -af --filter 'until=168h'` only removes images not tagged and not referenced by a container. The rollback tag from story 34-6 (`magnum-opus-bot:rollback`) is protected because it's a named tag, not dangling.
- **Loki compactor:** `retention_enabled: true` is required in both `limits_config` and `compactor` sections. Without it, Loki stores logs indefinitely. 14 days at ~50MB/day = ~700MB — fits comfortably on a 50GB Linode.
- **TOTP secret:** document clearly that the secret must be backed up externally. Without it and without backup codes, the operator is locked out of all admin services and must SSH directly (bypassing the proxy) to reset.

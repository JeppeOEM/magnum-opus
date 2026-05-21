---
id: 34-5
title: Telegram alerting — two tiers, heartbeat, position alerts, deploy notifications
epic: 34
status: ready-for-dev
---

# Story 34-5: Telegram alerting

## Context

Alertmanager and Grafana exist but have no delivery path to a human. Alerts fire into a void. This story wires Telegram as the notification channel using a two-tier model: urgent alerts wake you up, informational digests keep you informed without noise. The dead-man's switch heartbeat ensures you know when the alert system itself is broken.

**Two Telegram channels (or topics in one supergroup):**
- `#urgent` — wake-up alerts: service down, circuit breaker, emergency close, disk >90%
- `#digest` — informational: daily P&L, deploy events, service restarts, heartbeat

## What to build

### Alertmanager Telegram receiver

`monitoring/alertmanager/alertmanager.yml` — add Telegram receiver:

```yaml
receivers:
  - name: telegram-urgent
    telegram_configs:
      - bot_token: ${TELEGRAM_BOT_TOKEN}
        chat_id: ${TELEGRAM_CHAT_ID_URGENT}
        message: |
          🔴 *{{ .GroupLabels.alertname }}*
          {{ range .Alerts }}
          *Status:* {{ .Status }}
          *Summary:* {{ .Annotations.summary }}
          *Details:* {{ .Annotations.description }}
          {{ end }}
        parse_mode: Markdown

  - name: telegram-digest
    telegram_configs:
      - bot_token: ${TELEGRAM_BOT_TOKEN}
        chat_id: ${TELEGRAM_CHAT_ID_DIGEST}
        message: |
          ℹ️ *{{ .GroupLabels.alertname }}*
          {{ range .Alerts }}{{ .Annotations.summary }}{{ end }}
        parse_mode: Markdown

route:
  group_by: [alertname, service]
  group_wait: 30s
  group_interval: 5m
  repeat_interval: 4h
  receiver: telegram-digest
  routes:
    - match_re:
        severity: critical|page
      receiver: telegram-urgent
      repeat_interval: 30m
```

### Prometheus alert rules

`monitoring/prometheus/rules/ops.yml` — new alert rules:

```yaml
groups:
  - name: ops
    rules:

    # Disk
    - alert: DiskUsageHigh
      expr: (node_filesystem_size_bytes{mountpoint="/"} - node_filesystem_free_bytes{mountpoint="/"}) / node_filesystem_size_bytes{mountpoint="/"} > 0.80
      for: 5m
      labels:
        severity: page
      annotations:
        summary: "Disk usage > 80% on VPS"
        description: "{{ $value | humanizePercentage }} used. Docker cache prune or Loki cleanup needed."

    - alert: DiskUsageCritical
      expr: (node_filesystem_size_bytes{mountpoint="/"} - node_filesystem_free_bytes{mountpoint="/"}) / node_filesystem_size_bytes{mountpoint="/"} > 0.90
      for: 1m
      labels:
        severity: critical
      annotations:
        summary: "🚨 Disk >90% — services will fail"
        description: "Immediate action required. Redis AOF and QuestDB WAL will fail."

    # Services
    - alert: ServiceDown
      expr: up == 0
      for: 1m
      labels:
        severity: critical
      annotations:
        summary: "🚨 {{ $labels.job }} is DOWN"
        description: "Service {{ $labels.job }} has been unreachable for >1 minute."

    # Memory
    - alert: ContainerMemoryHigh
      expr: container_memory_usage_bytes{name=~"magnum.*"} / container_spec_memory_limit_bytes{name=~"magnum.*"} > 0.85
      for: 10m
      labels:
        severity: page
      annotations:
        summary: "Container {{ $labels.name }} memory >85%"
        description: "Possible memory leak. Current: {{ $value | humanize }}."

    # Bot-specific
    - alert: CircuitBreakerTripped
      expr: increase(bot_circuit_breaker_trips_total[5m]) > 0
      labels:
        severity: critical
      annotations:
        summary: "🚨 Bot circuit breaker tripped — all strategies stopped"
        description: "Daily loss limit hit. Manual review required before restart."

    - alert: BotHeartbeatTimeout
      expr: increase(bot_heartbeat_timeout_total[5m]) > 0
      labels:
        severity: critical
      annotations:
        summary: "🚨 Bot heartbeat timeout — emergency close may have fired"
        description: "Strategy bus silence detected. Check positions on exchange immediately."
```

### node_exporter in docker-compose

Add node_exporter to expose disk/memory metrics (required for disk and memory alerts):

```yaml
  node-exporter:
    image: prom/node-exporter:v1.8.0
    command:
      - '--path.rootfs=/host'
      - '--collector.filesystem.mount-points-exclude=^/(sys|proc|dev|host|etc)($$|/)'
    volumes:
      - /:/host:ro,rslave
    network_mode: host
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 64m
```

Add `node-exporter` to Prometheus scrape config.

### Position alert webhook in bot-service

`bot-service/bot_service/telegram.py` — thin wrapper:

```python
import httpx, os, structlog
log = structlog.get_logger()

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
_CHAT_URGENT = os.getenv("TELEGRAM_CHAT_ID_URGENT", "")
_CHAT_DIGEST = os.getenv("TELEGRAM_CHAT_ID_DIGEST", "")

def _send(chat_id: str, text: str) -> None:
    if not _TOKEN or not chat_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=5.0,
        )
    except Exception as exc:
        log.warning("telegram_send_failed", error=str(exc))

def alert(text: str) -> None:
    _send(_CHAT_URGENT, text)

def notify(text: str) -> None:
    _send(_CHAT_DIGEST, text)
```

Call from `order_worker.py` after fill confirmation:
```python
from bot_service.telegram import alert, notify

# In handle_fill() after confirmed fill:
notify(f"📈 *{req.side.upper()}* {req.symbol} — {req.size} @ {fill_price:.4f}\nStrategy: {req.strategy}")

# In emergency close path:
alert(f"🚨 *EMERGENCY CLOSE* {symbol}\nStrategy: {strategy_name}\nReason: heartbeat timeout")
```

### Dead-man's switch heartbeat

`bot-service/bot_service/heartbeat_telegram.py` — runs as an asyncio task in `main.py`:

```python
async def heartbeat_loop(interval_s: int = 21600) -> None:  # 6 hours
    """Send periodic alive message. Silence means something is wrong."""
    while True:
        await asyncio.sleep(interval_s)
        strategies = _file_watcher.get_strategy_statuses() if _file_watcher else {}
        running = [k for k, v in strategies.items() if v == "running"]
        notify(
            f"💚 *magnum-opus alive*\n"
            f"Strategies: {', '.join(running) or 'none'}\n"
            f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
        )
```

Start in lifespan alongside `_watcher_task` and `_watchdog_task`.

### Deploy notifications in `scripts/vps-deploy.sh`

Add to deploy script after successful deploy and after failed deploy:

```bash
telegram_notify() {
  local msg="$1"
  [ -z "$TELEGRAM_BOT_TOKEN" ] && return
  curl -sf -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="$TELEGRAM_CHAT_ID_DIGEST" \
    -d text="$msg" \
    -d parse_mode="Markdown" > /dev/null || true
}

# After successful deploy:
telegram_notify "✅ *${SERVICE} deployed*
Commit: \`$(ssh "$VPS" "cd $VPS_DIR && git rev-parse --short HEAD")\`
Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# On failed deploy (trap ERR):
telegram_notify "❌ *${SERVICE} deploy FAILED*
Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Check: ssh $VPS"
```

### `.env` additions

```bash
# Telegram
TELEGRAM_BOT_TOKEN=<bot token from @BotFather>
TELEGRAM_CHAT_ID_URGENT=<chat/topic ID for urgent alerts>
TELEGRAM_CHAT_ID_DIGEST=<chat/topic ID for digest>
```

Pass through to bot service in `docker-compose.yml`.

## Acceptance Criteria

1. Stopping the aggregator container triggers a `ServiceDown` alert delivered to the urgent Telegram channel within 2 minutes.
2. `du -sh /` on the VPS showing >80% disk usage triggers `DiskUsageHigh` alert to urgent channel.
3. A confirmed fill in the bot service sends a position notification to the digest channel.
4. A heartbeat message appears in the digest channel every 6 hours while the bot is running.
5. A successful deploy sends a notification with commit SHA to the digest channel.
6. A failed deploy (build error) sends a failure notification to the digest channel.
7. If `TELEGRAM_BOT_TOKEN` is empty, all Telegram calls are no-ops — no errors thrown.
8. `bot_circuit_breaker_trips_total` incrementing triggers a critical alert to urgent channel.

## Dev Notes

- **Bot token setup:** create a bot via @BotFather on Telegram. Send a message to your bot or group, then GET `https://api.telegram.org/bot<TOKEN>/getUpdates` to find the `chat_id`.
- **Two topics vs two groups:** Telegram supergroups support "topics" (forum mode). One group, two topics, two different `message_thread_id` values. Simpler to manage than two separate bots. Use `message_thread_id` in the API call if using topics.
- **Rate limiting:** Telegram limits bots to 30 messages/second per chat, 20 messages/minute per group. Alertmanager's `group_wait: 30s` and `group_interval: 5m` prevent storms. The `telegram.py` wrapper is fire-and-forget — delivery failures are logged as warnings, never raised.
- **node_exporter host mount:** the `rslave` mount propagation and `network_mode: host` are required for accurate disk and network metrics. This is the standard pattern from Prometheus documentation.
- **Heartbeat in digest only:** never send the heartbeat to urgent. The point is to notice its *absence* — if you don't see it for 12 hours, check the system. Making it urgent would cause alert fatigue.

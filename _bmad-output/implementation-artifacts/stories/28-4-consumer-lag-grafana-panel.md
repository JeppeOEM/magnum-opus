---
id: 28-4
title: Bot consumer lag Grafana panel
epic: 28
status: ready-for-dev
---

# Story 28-4: Bot consumer lag Grafana panel

## Context

Fixes D-16-3-1. The ops runbook references `bot_consumer_lag` metric and the alert playbook tells operators to `curl .../metrics | grep bot_consumer_lag`, but no Grafana panel visualises this metric. The metric IS emitted (set_consumer_lag wired in event_bus.py), but there is no panel in the monitoring dashboard.

Also update `docs/ops.md` to reference the panel instead of the curl command.

## What to build

### `monitoring/grafana/provisioning/dashboards/magnum-opus.json`

Add a new panel to the existing dashboard under the bot-service row (if one exists) or as a new row. The panel should be:

- **Title:** "Bot Consumer Lag"
- **Type:** `timeseries`
- **PromQL:** `bot_consumer_lag{strategy=~"$strategy"}`
- **Description:** "Redis stream consumer lag per strategy (ms). High values indicate the strategy is processing events slower than they arrive."
- **Unit:** milliseconds
- Place it adjacent to other bot-service panels.

Use template variable `$strategy` if it already exists in the dashboard; if not, add a `strategy` variable with query `label_values(bot_consumer_lag, strategy)`.

### `docs/ops.md`

Update the alert playbook for `bot_heartbeat_timeout_total` to:
1. Reference the new Grafana panel by name ("Bot Consumer Lag panel in Grafana") instead of the curl command.
2. Keep the curl command as a fallback: "or `curl http://bot-service:9001/metrics | grep bot_consumer_lag`".

## Acceptance Criteria

- Grafana JSON has a new panel with title "Bot Consumer Lag" and PromQL `bot_consumer_lag`.
- Panel type is `timeseries` with `unit: ms`.
- `docs/ops.md` references the panel and retains the curl fallback.
- README.md Grafana panel table updated per CLAUDE.md sync rules.

## Files
- `monitoring/grafana/provisioning/dashboards/magnum-opus.json`
- `docs/ops.md`
- `README.md` (Grafana panel table)

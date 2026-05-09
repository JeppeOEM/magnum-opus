# Story 10.4: Grafana Provisioned Dashboard

Status: done

## Story

As an operator,
I want a Grafana dashboard that shows health overview and USE metrics for all services,
so that when an alert fires I can immediately diagnose which condition is failing and why.

## Acceptance Criteria

1. Prometheus datasource provisioned from code — no manual UI configuration after `make up`
2. Dashboard provisioned from code — present without manual import steps
3. Row 1 (System Health Overview): stat panels for aggregator and candle-service, green/red based on `min(*_health)`
4. Row 2 (Condition Matrix): table panel querying all `*_health` metrics with green/red cell coloring
5. Row 3 (aggregator USE): ticks/s, consumer lag ms, gap rate + feed state
6. Row 4 (candle-service USE): bars/s, consumer lag messages, flush + redis publish failure rates
7. Dashboard and datasource survive container restart without reconfiguration
8. `GF_AUTH_ANONYMOUS_ENABLED=true` — no login required

## Tasks / Subtasks

- [x] Create `monitoring/grafana/provisioning/datasources/prometheus.yml` (AC: 1, 7)
- [x] Create `monitoring/grafana/provisioning/dashboards/provider.yml` (AC: 2, 7)
- [x] Create `monitoring/grafana/provisioning/dashboards/magnum-opus.json` — 4-row dashboard (AC: 3–6)

## Dev Notes

### Dashboard structure

- Row 1: 2 stat panels (`min(aggregator_health)`, `min(candle_health)`), background color thresholds 0→red, 1→green
- Row 2: table panel with `{__name__=~"aggregator_health|candle_health"}` — auto-discovers conditions
- Row 3: 3 timeseries panels (ticks/s, lag ms, gaps+feed_state)
- Row 4: 3 timeseries panels (bars/s, consumer lag, flush+redis failures)
- Datasource template variable (`${datasource}`) for easy datasource swapping
- `refresh: 30s`, `time: now-1h to now`

### Grafana container config (in docker-compose — story 10.5)

```yaml
grafana:
  image: grafana/grafana:10.4.2
  environment:
    - GF_AUTH_ANONYMOUS_ENABLED=true
    - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
    - GF_AUTH_DISABLE_LOGIN_FORM=true
  volumes:
    - ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro
    - grafana-data:/var/lib/grafana
  ports:
    - "3000:3000"
  depends_on:
    - prometheus
```

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Completion Notes List

- Created datasource provisioning: `prometheus.yml` pointing to `http://prometheus:9090`, `isDefault: true`, `editable: false`.
- Created dashboard provider: `provider.yml` with `disableDeletion: true`, `allowUiUpdates: false`, 30s update interval.
- Created `magnum-opus.json`: 4 rows, 10 panels, UID=`magnum-opus`, datasource template variable, `refresh: 30s`. Condition matrix uses `{__name__=~"aggregator_health|candle_health"}` for auto-discovery of future services.

### File List

- `monitoring/grafana/provisioning/datasources/prometheus.yml` — created
- `monitoring/grafana/provisioning/dashboards/provider.yml` — created
- `monitoring/grafana/provisioning/dashboards/magnum-opus.json` — created

---
id: 34-12
title: Promtail file-based log collection — remove Docker socket privilege
epic: 34
status: ready-for-dev
---

# Story 34-12: Promtail file-based log collection

## Context

Promtail currently mounts the Docker socket (`/var/run/docker.sock`) to discover and read container logs. Docker socket access is equivalent to root on the host — a container escape or compromise of the Promtail container gives an attacker full control of the VPS. This is the highest-privilege container in the stack.

Switching to file-based log collection removes the Docker socket entirely. Docker's built-in logging driver writes container logs to `/var/lib/docker/containers/<id>/<id>-json.log`; Promtail reads those files directly. The result is identical log content with a dramatically reduced attack surface.

**Promoted from:** D-34-6 (Promtail mounts Docker socket — privilege escalation vector)

## What to build

### Docker logging driver — `docker-compose.yml`

Add a default logging configuration so all containers write structured JSON logs to disk:

```yaml
x-logging: &default-logging
  driver: json-file
  options:
    max-size: "50m"
    max-file: "5"
    tag: "{{.Name}}/{{.ID}}"

services:
  aggregator:
    logging: *default-logging
    ...

  candle:
    logging: *default-logging
    ...

  bot:
    logging: *default-logging
    ...

  # Apply to all services — gateway, dashboard, questdb, redis, grafana, prometheus, loki, auth-proxy
```

The `tag` field adds the container name and ID to each log line's filename metadata, enabling Promtail to extract labels without needing the Docker API.

### Promtail config — file-based scrape

`monitoring/promtail/config.yml`:

```yaml
server:
  http_listen_port: 9080
  grpc_listen_port: 0

positions:
  filename: /tmp/positions.yaml

clients:
  - url: http://loki:3100/loki/api/v1/push

scrape_configs:
  - job_name: docker-containers
    static_configs:
      - targets:
          - localhost
        labels:
          job: docker
          __path__: /var/lib/docker/containers/*/*.log

    pipeline_stages:
      # Parse Docker JSON log format
      - json:
          expressions:
            log: log
            stream: stream
            time: time
            tag: attrs.tag

      # Extract container name from tag (format: "container-name/container-id")
      - regex:
          expression: '^(?P<container_name>[^/]+)/(?P<container_id>[a-f0-9]+)$'
          source: tag

      # Set labels from extracted fields
      - labels:
          container_name:
          stream:

      # Use Docker log timestamp
      - timestamp:
          source: time
          format: RFC3339Nano

      # Output the actual log content (not the wrapper JSON)
      - output:
          source: log
```

### Promtail compose service — remove Docker socket

```yaml
  promtail:
    image: grafana/promtail:2.9.0
    command: -config.file=/etc/promtail/config.yml
    volumes:
      - ./monitoring/promtail/config.yml:/etc/promtail/config.yml:ro
      # Read-only access to Docker log files on host
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      # Track read positions across restarts
      - promtail-positions:/tmp
    # NO Docker socket mount — this is the point
    depends_on:
      - loki
    deploy:
      resources:
        limits:
          memory: 64m
    restart: unless-stopped
```

Remove the previous volume mount:
```yaml
# REMOVED:
# - /var/run/docker.sock:/var/run/docker.sock
```

### Promtail container user — drop to non-root

Without the Docker socket, Promtail only needs read access to log files. Run as a non-root user with the `docker` supplementary group to read `/var/lib/docker/containers`:

```yaml
  promtail:
    ...
    user: "nobody:${DOCKER_GID}"
```

Add `DOCKER_GID` to `.env`:
```bash
# Get docker group GID: getent group docker | cut -d: -f3
DOCKER_GID=999
```

Or use root as a fallback if group GID varies across environments (simpler, still no socket).

### Verify log labels in Grafana

After the change, verify container logs appear in Grafana with correct `container_name` labels. The label names may differ from the Docker socket approach (where Promtail used `container` label from Docker API metadata). Update any existing Grafana log panel queries:

```logql
# Before (Docker socket):
{job="varlogs", container="magnum-opus-bot-1"}

# After (file-based):
{job="docker", container_name="magnum-opus-bot-1"}
```

If Grafana dashboards are provisioned JSON files, update the LogQL queries in `monitoring/grafana/provisioning/dashboards/magnum-opus.json`.

### `docs/ops.md` — update log access instructions

```markdown
### Viewing logs

Container logs are stored at `/var/lib/docker/containers/<id>/<id>-json.log` (JSON format)
and ingested by Promtail into Loki automatically. Access via:

- **Grafana Explore:** http://<tailscale-ip>:8080/grafana → Explore → Loki
  - Filter: `{job="docker", container_name="magnum-opus-bot-1"}`
- **CLI on VPS:** `docker compose logs --tail=100 -f <service>`
- **Raw file:** `/var/lib/docker/containers/<id>/<id>-json.log` (JSON, one entry per line)
```

## Acceptance Criteria

1. `docker inspect promtail` shows no `/var/run/docker.sock` volume mount.
2. Container logs from all services appear in Grafana Loki with correct `container_name` labels.
3. `{job="docker", container_name="magnum-opus-bot-1"}` in Grafana Explore returns bot service logs.
4. Log entries include the original log message (not the Docker JSON wrapper).
5. Promtail survives a container restart — `promtail-positions` volume preserves read positions and no duplicate logs appear.
6. `docker compose logs bot` still works (this reads Docker's log driver directly, unaffected by Promtail changes).
7. Log rotation works: after `max-file: 5` is exceeded, old log files are removed automatically.

## Dev Notes

- **`/var/lib/docker/containers` vs Docker socket:** the log file approach requires a host path mount (read-only). This is less privileged than the socket — reading files cannot control containers or the Docker daemon. The host path is typically only readable by root or the `docker` group, so Promtail must run with appropriate permissions.
- **Log file format:** Docker's `json-file` driver writes one JSON object per line: `{"log":"...\n","stream":"stdout","time":"2026-05-20T14:32:01.123456789Z","attrs":{"tag":"..."}}`. The Promtail pipeline stages above parse this correctly.
- **Container name label from tag:** the `tag: "{{.Name}}/{{.ID}}"` logging option embeds the container name in the log metadata. Without this, Promtail reading raw files cannot determine which container produced the log (file path contains only the container ID, not the name). The regex stage extracts the name from the tag field.
- **Positions file:** Promtail tracks which byte offset it has read in each log file in `positions.yaml`. Without persisting this (via a named volume), every Promtail restart re-reads all log files from the beginning, sending duplicate logs to Loki. The `promtail-positions:/tmp` volume mount preserves state across restarts.
- **Label migration:** if you have existing Loki data with `container` labels (from the Docker socket approach), those labels remain queryable. New data will use `container_name`. You may need to update saved Grafana dashboard queries, but the change is not destructive.
- **Log rotation and Promtail:** Docker's `max-file: 5` rotates log files. Promtail detects rotation via inode change and resumes tailing the new file. No special configuration needed.

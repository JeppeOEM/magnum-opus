---
id: 34-10
title: Internal network auth — Redis requirepass, QuestDB HTTP auth
epic: 34
status: ready-for-dev
---

# Story 34-10: Internal network auth hardening

## Context

The Docker network provides isolation from the public internet, but within the Docker network any container can read/write Redis and run arbitrary SQL on QuestDB with zero credentials. The auth proxy (story 34-4) blocks external access via Tailscale, but doesn't protect against a compromised container inside the network. This story adds password authentication to both Redis and QuestDB's HTTP interface.

**Promoted from:** D-34-1 (Redis requirepass), D-34-2 (QuestDB HTTP auth), D-34-4 (QuestDB memory tuning docs)

## What to build

### Redis — add `requirepass`

`docker-compose.yml` — add password to Redis command:

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
      --requirepass ${REDIS_PASSWORD}
```

Add to all services that connect to Redis via env var:

```yaml
  aggregator:
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      ...

  candle:
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      ...

  bot:
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      ...

  gateway:
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      ...
```

Update connection strings in each service's config/connection layer to include the password. The Go services use `redis://:<password>@redis:6379/0` format. The Python bot uses `redis.asyncio.from_url(f"redis://:{REDIS_PASSWORD}@redis:6379")`.

`.env` addition:
```bash
# Redis auth — generated once, store in password manager
REDIS_PASSWORD=<64-char random hex>
```

Generate: `openssl rand -hex 32`

#### Python bot-service — `config.py`

```python
class Config(BaseSettings):
    redis_password: str = Field(default="", description="Redis auth password")

    @property
    def redis_url(self) -> str:
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}"
        return f"redis://{self.redis_host}:{self.redis_port}"
```

#### Go aggregator / candle-service / gateway

In the Redis client initialisation (wherever `redis.ParseURL` or `redis.NewClient` is called), use `REDIS_PASSWORD` env var:

```go
opts, _ := redis.ParseURL(os.Getenv("REDIS_URL"))
// Or:
opts := &redis.Options{
    Addr:     redisAddr,
    Password: os.Getenv("REDIS_PASSWORD"),
    DB:       0,
}
```

### QuestDB — HTTP basic auth

QuestDB supports HTTP basic auth via `pg.password` setting in `server.conf`. Mount a config file:

`monitoring/questdb/server.conf`:
```ini
# HTTP authentication
http.user=admin
http.password=${QUESTDB_HTTP_PASSWORD}

# Keep PG wire protocol for ILP (no auth on ILP port 9009 — internal only)
pg.enabled=true
```

```yaml
  questdb:
    image: questdb/questdb:8.2.1
    volumes:
      - questdb-data:/root/.questdb
      - ./monitoring/questdb/server.conf:/root/.questdb/conf/server.conf:ro
    environment:
      QUESTDB_HTTP_PASSWORD: ${QUESTDB_HTTP_PASSWORD}
      ...
```

`.env` addition:
```bash
QUESTDB_HTTP_PASSWORD=<password>
```

Update any service that calls QuestDB HTTP REST (`/exec` endpoint) to include basic auth headers. The main caller is the dashboard's QuestDB query client and the backup script:

```python
# dashboard — questdb_client.py
import httpx
auth = (os.getenv("QUESTDB_HTTP_USER", "admin"), os.getenv("QUESTDB_HTTP_PASSWORD", ""))
resp = httpx.get(f"http://questdb:9000/exec", params={"query": sql}, auth=auth)
```

```bash
# backup script
curl -sf -u "admin:${QUESTDB_HTTP_PASSWORD}" "http://localhost:9000/exec?query=CHECKPOINT+CREATE"
```

ILP writes (port 9009) are not affected — the ILP protocol on that port does not use HTTP auth.

### QuestDB memory tuning table — `docs/ops.md`

Add a reference table so future operators can tune correctly:

```markdown
### QuestDB memory tuning by Linode plan

| Linode RAM | Xms  | Xmx   | Docker limit | max_uncommitted_rows |
|-----------|------|-------|--------------|----------------------|
| 4 GB      | 256m | 512m  | 1g           | 10,000               |
| 8 GB      | 512m | 1g    | 2g           | 50,000               |
| 16 GB     | 1g   | 2g    | 4g           | 200,000              |

The default `max_uncommitted_rows=2,000,000` is designed for 16GB+ servers and uses ~200MB
of WAL buffer on its own. On a 4GB Linode this leaves insufficient headroom for the rest of
the stack.
```

## Acceptance Criteria

1. `docker compose exec redis redis-cli ping` without password returns `NOAUTH Authentication required`.
2. `docker compose exec redis redis-cli -a $REDIS_PASSWORD ping` returns `PONG`.
3. All services that use Redis (aggregator, candle, bot, gateway) connect successfully with the password set.
4. `curl http://localhost:9000/exec?query=select+1` (no auth) returns 401 Unauthorized.
5. `curl -u admin:$QUESTDB_HTTP_PASSWORD http://localhost:9000/exec?query=select+1` returns the result JSON.
6. Dashboard QuestDB queries continue to work with auth credentials passed via env var.
7. ILP writes to port 9009 are unaffected by HTTP auth (ILP has no auth — internal network only).
8. `docs/ops.md` contains the QuestDB memory tuning table.

## Dev Notes

- **Redis `requirepass` vs ACL:** `requirepass` is a legacy config option that creates a default user with the given password. For a single-operator system this is sufficient. If multiple services need different permissions in future, switch to Redis ACL (`aclfile`).
- **QuestDB HTTP auth vs PG wire:** QuestDB's HTTP REST (`:9000`) and PG wire (`:8812`) are separate. The HTTP auth (`http.password`) only protects the REST interface. PG wire has its own auth setting. The aggregator and candle-service write ILP to `:9009` which has no auth — this is acceptable because ILP port is not exposed publicly and is on the internal Docker network only.
- **Health check impact:** the compose health check `curl http://localhost:9000/exec?query=select+1` must be updated to include `-u admin:${QUESTDB_HTTP_PASSWORD}`. Without this the health check will return 401 and the service will be marked unhealthy.
- **Environment variable injection into QuestDB server.conf:** QuestDB does not natively interpolate env vars in `server.conf`. Options: (1) use `envsubst` to render the config at container startup via an entrypoint wrapper, (2) pass the password as a JVM system property `-Dhttp.password=${QUESTDB_HTTP_PASSWORD}`, or (3) mount the rendered config from the deploy script. Simplest: use an entrypoint script that runs `envsubst < /tmp/server.conf.tpl > /root/.questdb/conf/server.conf` before starting QuestDB.
- **Backup script auth:** `scripts/backup-questdb.sh` must pass `QUESTDB_HTTP_PASSWORD` from the environment for the checkpoint call. The VPS `.env` file provides this when the cron job sources it.

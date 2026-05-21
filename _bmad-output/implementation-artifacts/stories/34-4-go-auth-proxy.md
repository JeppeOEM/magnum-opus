---
id: 34-4
title: Go auth proxy — TOTP, 7-day session, machine token, audit log
epic: 34
status: ready-for-dev
---

# Story 34-4: Go auth proxy

## Context

All admin services (Grafana :3000, QuestDB :9000, Prometheus :9090, Dashboard :8050, Bot API :8090) are currently reachable by any enrolled Tailscale device with no application-layer authentication. Tailscale provides network-layer security but a compromised enrolled device has full access to everything. This story adds a Go reverse proxy that sits in front of all admin services and enforces two-factor authentication at the application layer.

**Defense-in-depth model:**
- Layer 1 — Tailscale: only enrolled devices reach the VPS
- Layer 2 — Auth proxy: even on the tailnet, a valid session is required

**Two credential types:**
- **Human:** TOTP login via browser → 7-day signed session cookie
- **Machine:** Static bearer token for Makefile deploy scripts and automation

## What to build

### `auth-proxy/` — new top-level directory

New Go module: `auth-proxy/cmd/proxy/main.go`. Single binary, ~400 lines. No external auth library dependencies — use stdlib `crypto/hmac`, `crypto/sha256`, `encoding/base64`, `net/http/httputil`.

### Core behaviour

**Routes:**
- `GET /auth/login` — serve login form (HTML, inline, no template files)
- `POST /auth/login` — validate TOTP code, set session cookie, redirect to `/`
- `GET /auth/logout` — delete session cookie, redirect to login
- All other paths — check session cookie OR `Authorization: Bearer <machine-token>` → reverse-proxy to upstream

**Upstream routing table** (driven by env vars, with defaults):
```
/grafana/     → http://grafana:3000
/questdb/     → http://questdb:9000
/dashboard/   → http://dashboard:8050
/bot/         → http://bot:8090
/prometheus/  → http://prometheus:9090
/             → http://grafana:3000  (default)
```

Upstream services keep their existing ports on the internal Docker network. Nothing is exposed externally except the proxy on `:8443` (or `:8080` — see Dev Notes on TLS decision).

### TOTP implementation

Use `github.com/pquerna/otp/totp` (well-audited, stdlib-compatible). On first start, if `AUTH_TOTP_SECRET` env var is empty, generate a new secret, print the provisioning URI and a QR code as ASCII to stdout, and exit — forcing the operator to scan it and restart with the secret set.

```go
// First-run secret generation
secret, _ := totp.Generate(totp.GenerateOpts{
    Issuer:      "magnum-opus",
    AccountName: "mrqdt",
})
fmt.Printf("TOTP Secret: %s\n", secret.Secret())
fmt.Printf("Scan this URI in your authenticator app:\n%s\n", secret.URL())
os.Exit(0)
```

Validate submitted codes with a 1-step window (±30s clock drift tolerance).

### Session cookie

Signed with HMAC-SHA256 using `AUTH_SESSION_KEY` (32-byte random, set in `.env`). Cookie value: `base64(session_id + ":" + expiry_unix + ":" + hmac)`. No external session store needed for the happy path — the HMAC is the verification. Sessions expire after 7 days.

**Session revocation** (for device-loss scenario): maintain a small Redis set `auth:revoked_sessions` of invalidated session IDs. Check membership on every request. `make revoke-sessions VPS=...` calls `POST /auth/revoke-all` (machine-token authenticated), which adds all currently-issued session IDs to the revoked set with a 7-day TTL.

### Machine token

`AUTH_MACHINE_TOKEN` env var (long random string, set in `.env`). Any request with `Authorization: Bearer <AUTH_MACHINE_TOKEN>` bypasses TOTP entirely. Requests with the machine token are logged with `source: machine` in the audit log.

### Rate limiting

In-memory per-IP rate limiter on `POST /auth/login`: max 5 failed attempts per IP per 15 minutes. After limit: return 429 with `Retry-After` header. Store in a `sync.Map` with a background goroutine that prunes entries older than 15 minutes.

### Audit log

Every proxied request logged as a structlog JSON line to stdout (captured by Loki via Promtail):
```json
{"level":"info","ts":"2026-05-20T14:32:01Z","event":"proxy_request","session_id":"abc123","source":"human","method":"POST","path":"/bot/stop-all","upstream":"bot","status":200,"latency_ms":12}
```

### TOTP backup codes

On first run (alongside the secret generation), generate 8 one-time backup codes. Print them to stdout. Each code is a 10-character random string stored as bcrypt hashes in `AUTH_BACKUP_CODES` env var (comma-separated bcrypt hashes). A backup code can be submitted in the TOTP field and is invalidated after first use (removed from the env var list — operator must restart proxy to persist the updated list).

### Break-glass recovery procedure

Document in `docs/ops.md`:
```bash
# Locked out of TOTP (new phone, lost authenticator):
ssh deploy@<tailscale-ip>
docker compose exec auth-proxy /proxy -reset-totp
# Prints new QR code to stdout
# Update AUTH_TOTP_SECRET in .env, restart proxy
docker compose up -d --no-deps auth-proxy
```

### `docker-compose.yml` — add auth-proxy service

```yaml
  auth-proxy:
    build:
      context: ./auth-proxy
    environment:
      AUTH_TOTP_SECRET: ${AUTH_TOTP_SECRET}
      AUTH_SESSION_KEY: ${AUTH_SESSION_KEY}
      AUTH_MACHINE_TOKEN: ${AUTH_MACHINE_TOKEN}
      AUTH_BACKUP_CODES: ${AUTH_BACKUP_CODES:-}
      REDIS_ADDR: redis:6379
      UPSTREAM_GRAFANA: http://grafana:3000
      UPSTREAM_QUESTDB: http://questdb:9000
      UPSTREAM_DASHBOARD: http://dashboard:8050
      UPSTREAM_BOT: http://bot:8090
      UPSTREAM_PROMETHEUS: http://prometheus:9090
      PROXY_ADDR: ":8080"
    ports:
      - "8080:8080"
    depends_on:
      redis:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 64m
          cpus: "0.25"
    restart: unless-stopped
```

Downstream services remove their host port bindings (they remain on internal Docker network only):
- Grafana: `ports` removed (was `3000:3000`)
- QuestDB: `ports` reduced to ILP only (`9009:9009`), HTTP `:9000` internal only
- Dashboard: `ports` removed (was `8050:8050`)
- Bot: `ports` removed (was `8090:8090`)
- Prometheus: `ports` removed (was `9090:9090`)

### `Makefile` additions

```makefile
## Revoke all active human sessions (e.g. lost device)
revoke-sessions:
	curl -sf -X POST -H "Authorization: Bearer $$AUTH_MACHINE_TOKEN" \
	  http://$(VPS_TAILSCALE_IP):8080/auth/revoke-all

## Print new TOTP QR code (locked out recovery)
reset-totp:
	ssh $(VPS) "cd $(VPS_DIR) && docker compose exec auth-proxy /proxy -reset-totp"
```

## Acceptance Criteria

1. `GET http://VPS:8080/grafana/` without a session cookie returns the login page (200 with TOTP form), not Grafana.
2. Submitting a valid TOTP code sets a session cookie; subsequent requests reach Grafana without re-authenticating.
3. Session cookie is valid for 7 days; expired sessions redirect to login.
4. `curl -H "Authorization: Bearer $AUTH_MACHINE_TOKEN" http://VPS:8080/bot/health` returns the bot health response.
5. 6 failed TOTP attempts from the same IP within 15 minutes return 429.
6. `make revoke-sessions` causes the existing session cookie to be rejected on next request.
7. Every proxied request produces a JSON audit log line with session_id, path, upstream, and status.
8. Grafana, dashboard, bot API ports are NOT reachable directly from Tailscale (only through proxy on :8080).
9. First-run with empty `AUTH_TOTP_SECRET` prints QR code URI and exits — does not start the proxy.
10. Backup codes: one code works once, is rejected on second use.

## Dev Notes

- **TLS decision:** HTTP-only over Tailscale is acceptable — WireGuard encrypts the transport. Set `Secure` and `HttpOnly` flags on the session cookie regardless. Browsers will honour `Secure` on localhost/Tailscale IP because Tailscale IPs (100.x.x.x) use the loopback network class in some browser implementations; if not, add a self-signed cert. Do not let this block the story — HTTP first, TLS as a follow-up.
- **Proxy fail mode on Redis unavailability:** if Redis is down, the revocation check fails open (session is accepted) with a `warn` log. Revocation is a convenience feature; it must not make the proxy unavailable. The HMAC signature check still runs.
- **Upstream path rewriting:** `/bot/health` → `http://bot:8090/health` (strip the prefix). Use `httputil.NewSingleHostReverseProxy` with a custom `Director` that rewrites the path.
- **QuestDB HTTP port:** QuestDB exposes `:9000` (HTTP) and `:9009` (ILP). Only ILP needs to stay directly accessible (candle-service and bot write ILP directly). The HTTP UI/API goes through the proxy. Keep `9009:9009` port binding in compose; remove `9000:9000`.
- **Auth proxy is the most critical service:** if it crashes, all admin access is lost. `restart: unless-stopped` is mandatory. Add a `/auth/health` endpoint that returns `{"status":"ok"}` — used by the Telegram heartbeat story (34-5).

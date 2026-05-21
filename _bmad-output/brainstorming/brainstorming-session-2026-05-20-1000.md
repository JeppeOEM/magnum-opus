---
stepsCompleted: [1, 2, 3, 4]
inputDocuments: []
session_topic: 'Gaps in Epic 34 — security hardening and zero-downtime independent service deployment'
session_goals: 'Surface anything missing from the current deploy/security epic: attack surfaces not covered, workflow friction not addressed, downtime scenarios not handled, operational blind spots'
selected_approach: 'ai-recommended'
techniques_used: ['reverse-brainstorming', 'assumption-reversal', 'chaos-engineering']
ideas_generated: [93]
workflow_completed: true
session_active: false
context_file: ''
---

# Brainstorming Session Results

**Facilitator:** mrqdt
**Date:** 2026-05-20

## Session Overview

**Topic:** Gaps in Epic 34 — security hardening and zero-downtime independent service deployment
**Goals:** Surface anything missing from the current deploy/security epic: attack surfaces not covered, workflow friction not addressed, downtime scenarios not handled, operational blind spots

### Session Setup

Fresh session. Context: Epic 34 covers VPS bootstrap, per-service deploy scripts, and Tailscale + UFW firewall hardening for a single Linode running a crypto trading stack (aggregator, candle-service, bot-service, gateway, dashboard, Redis, QuestDB, Grafana/Prometheus/Loki).

---

---

## Phase 2: Assumption Reversal

**15 assumptions flipped. Key findings:**
- Tailscale ACLs not configured — all enrolled devices have full access by default
- TOTP seed stored in `.env` — if readable, TOTP is entirely broken
- `make deploy-bot` has no position guard or confirmation — one command, instant restart
- Redis session store has no password — proxy revocation mechanism is bypassable
- Reconciliation correctness depends on fire-and-forget persistence that can silently fail
- `Secure` cookie flag missing — session cookies travel in plaintext if Tailscale misconfigured
- `docker compose down -v` deletes all data with no warning — one flag difference
- VPS reboot ignores `depends_on` health conditions — bot crash-loops until QuestDB ready

---

## Phase 3: Chaos Engineering

**21 disaster scenarios run. Sharpest findings:**
- OOM kills QuestDB silently — no health check catches it, no alert fires, fills lost
- Disk full cascades: 4 simultaneous silent failures with no recovery path
- Exchange API down during reconciliation + fire-and-forget failure = position doubling risk
- TOTP lockout with no break-glass procedure = blind on trading system
- Build failure = old container stopped + new image broken + positions unmonitored
- Linode snapshot restore: exchange has current positions, QuestDB has 2-day-old state
- `deploy-all` with open positions: bot + aggregator gap simultaneous, no guard

---

## Session Summary

**93 ideas generated across 3 phases, organised into 6 themes.**

### Story Candidates — Epic 34 Expansion
| Story | Title |
|---|---|
| 34-4 | Go auth proxy — TOTP + 7-day session + machine token + audit log |
| 34-5 | Telegram alerting — two tiers, heartbeat, position alerts, deploy notifications |
| 34-6 | Deploy safety — position guard, confirm pause, rollback command, smoke test |
| 34-7 | Data persistence — Redis AOF, QuestDB backup, Loki retention, restore procedure |
| 34-8 | VPS reboot ordering — systemd unit, QuestDB memory limit, startup fix |

### Deferred Work Items to Add
- Redis `requirepass` not set — internal network has zero auth
- QuestDB HTTP API auth not enabled
- Tailscale ACLs not configured — all enrolled = all trusted
- QuestDB memory limit (8GB) exceeds host RAM on small Linodes
- Strategy hot-reload has no file integrity / hash check
- Promtail mounts Docker socket — privilege escalation vector

---

## Phase 1: Reverse Brainstorming — "How do we get owned?"

**57 ideas generated across 9 clusters.**

### Confirmed Priorities (user-filtered from Phase 1)
- ✅ **Telegram alerts** — two tiers (urgent + digest), deploy notifications, position alerts, heartbeat
- ✅ **Fix VPS reboot restart ordering** — systemd unit or compose delay so depends_on health conditions fire properly
- ✅ **Data persistence over staging** — QuestDB volume backup, Redis AOF, verified restore procedure
- ✅ **Go auth proxy (TOTP + 7-day session)** — single entry point in front of all admin services; TOTP login, 7-day session cookie, machine token for automation, session revocation, rate-limited login, audit log
- ✅ **Defense in depth: Tailscale (network) + auth proxy (application)** — two independent security layers
- ❌ Staging environment — not needed if data is correctly saved
- ❌ Multi-VPS — single VPS is fine

### Key Ideas from Phase 1
- Deploy chain blind trust in git pull (no signature verification)
- SSH agent forwarding pivots to GitHub
- Docker base images unpinned (`latest` tag floats)
- API keys in swapfile plaintext
- `docker inspect` leaks secrets to docker group members
- Bot API has zero authentication — `/stop-all` wide open
- Grafana anonymous viewer enabled in compose
- Redis and QuestDB have no auth on internal network
- Go build can OOM the VPS mid-market causing collateral service kills
- `docker compose up` stop/build overlap — not atomic
- Redis restart can trigger bot emergency close
- Disk full silent failure — no alert, no retention policy
- Redis `noeviction` policy: health check green while pipeline is dead
- QuestDB WAL auto-suspend drops writes silently
- No rollback command — 3-5 min manual recovery while bad code runs
- Tailscale account = master key; no 2FA = one phished password owns everything
- Tailscale outage = locked out if port 22 removed
- Promtail mounts Docker socket — highest-privilege container
- Linode console bypasses all security (hypervisor-level access)
- Hot reload gap: in-flight fill events may be re-delivered as duplicates
- Signal file changes require full restart — highest dev friction path
- New trading symbol requires full restart (BusManager stream key limitation)
- No smoke test after deploy — script declares success before service is ready
- API key rotation under duress forces exactly the downtime you avoid normally
- Telegram: dead-man's switch heartbeat (silence = alert)

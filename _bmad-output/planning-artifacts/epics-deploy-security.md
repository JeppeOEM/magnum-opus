---
status: complete
service: infrastructure
---

# magnum-opus — Deployment & Security Epic Breakdown

_Added 2026-05-20. Epic 34 covering Linode VPS provisioning, per-service deploy scripts, and Tailscale-based private network security._

## Epic 34: Linode Deployment & Security Hardening

mrqdt can deploy any individual service to a Linode VPS with a single command from the monorepo root — no registry, no CI required. The entire VPS admin surface (Grafana, QuestDB UI, Prometheus, Dashboard, bot API) is only reachable from mrqdt's personal devices via Tailscale; the public internet sees nothing except an optional WebSocket port. SSH is hardened and optionally replaced by Tailscale SSH.

The build happens on the VPS (git pull + docker compose --build) so no image registry is needed. Layer caching makes incremental deploys fast after the first build. Per-service deploys do not touch other running services.

### Epic 34 Stories

#### Story 34-1: VPS bootstrap script
One-time provisioning of a fresh Linode Ubuntu instance: Docker, firewall baseline, non-root deploy user, SSH key-only auth, repo clone, and env file stubs.

#### Story 34-2: Per-service deploy scripts and Makefile targets
`scripts/vps-deploy.sh <service>` and Makefile `deploy-*` targets that SSH to the VPS, pull the latest code, and restart only the targeted service without downtime to others. Candle service delegates to the existing `deploy-candle.sh` blue-green script.

#### Story 34-3: Tailscale VPN and firewall hardening
Install Tailscale on VPS and dev machines. Lock down UFW so all admin ports are accessible only via the Tailscale network interface. Public internet access is blocked for all services except an optional gateway WebSocket port. SSH is restricted to Tailscale (or key-only with Tailscale SSH as the preferred path).

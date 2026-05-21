---
id: 34-3
title: Tailscale VPN and firewall hardening
epic: 34
status: ready-for-dev
---

# Story 34-3: Tailscale VPN and firewall hardening

## Context

After stories 34-1 and 34-2, the VPS runs the full stack but all service ports are exposed on the public internet. Anyone who knows the VPS IP can reach Grafana, QuestDB UI, Prometheus, the bot API, and the dashboard. This story locks everything down so only the operator's personal devices can access admin services.

**Design choice: Tailscale over alternatives**

Tailscale creates a private WireGuard mesh between enrolled devices. Each device gets a stable `100.x.x.x` IP on the `tailnet`. Once the VPS and the dev machine(s) are enrolled:
- All traffic on the Tailscale interface (`tailscale0`) is encrypted, authenticated, and private
- UFW allows all traffic on `tailscale0` — admin ports become reachable at `100.x.x.x:<port>`
- UFW blocks those same ports on the public interface — the public internet sees nothing
- No VPN server to maintain; Tailscale's control plane handles key distribution

**Access model:**
```
Public internet    →  UFW blocks all admin ports
                       Only: SSH port 22 (or removed if using Tailscale SSH)
                       Optional: gateway WebSocket :8083 for browser clients

Dev machine        →  Tailscale enrolled → tailscale0 allowed by UFW
  (laptop/desktop)       Access: http://100.x.x.x:3000  (Grafana)
                                 http://100.x.x.x:9000  (QuestDB UI)
                                 http://100.x.x.x:9090  (Prometheus)
                                 http://100.x.x.x:8050  (Dashboard)
                                 http://100.x.x.x:8090  (Bot API)
                         SSH:    ssh deploy@100.x.x.x (Tailscale SSH, no port needed)
```

## What to build

### `scripts/setup-tailscale.sh`

Run on the VPS after story 34-1 bootstrap. Installs Tailscale, advertises no routes (point-to-point only), and enables Tailscale SSH.

```bash
#!/usr/bin/env bash
# setup-tailscale.sh — install and configure Tailscale on the VPS
# Run as root (or with sudo) on the VPS.
#
# Usage:
#   ssh root@<VPS_IP> 'bash -s' < scripts/setup-tailscale.sh
#
# After completion:
#   1. Visit the printed URL to authenticate the VPS into your tailnet
#   2. Run: make setup-firewall VPS=deploy@100.x.x.x
#      (use the Tailscale IP for all subsequent access)

set -euo pipefail

echo "==> Installing Tailscale"
curl -fsSL https://tailscale.com/install.sh | sh

echo "==> Starting Tailscale (SSH enabled)"
# --ssh: enables Tailscale SSH (no separate sshd port needed once connected)
# --accept-dns=false: keep system DNS, don't override with Tailscale DNS
tailscale up --ssh --accept-dns=false

echo ""
echo "✓ Tailscale installed."
echo "  Authenticate via the URL above, then note your Tailscale IP:"
echo "    tailscale ip -4"
echo ""
echo "Next: run setup-firewall.sh to lock down UFW"
```

### `scripts/setup-firewall.sh`

Run on the VPS after Tailscale is authenticated. Applies UFW rules that allow all Tailscale traffic and block admin ports from the public internet.

```bash
#!/usr/bin/env bash
# setup-firewall.sh — UFW rules for Tailscale-private admin access
# Run as root on the VPS after Tailscale is authenticated.
#
# What this does:
#   - Allow ALL traffic on tailscale0 (authenticated, encrypted)
#   - Block all admin ports from public internet
#   - Keep port 22 open as fallback until Tailscale SSH is verified
#   - Optional: open gateway WebSocket port 8083 publicly

set -euo pipefail

echo "==> Configuring UFW"

# Reset to clean state
ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# --- Tailscale: allow everything on the tailscale0 interface ---
# All admin services are reachable at 100.x.x.x without exposing them publicly.
ufw allow in on tailscale0
ufw allow out on tailscale0

# --- SSH fallback (remove after confirming Tailscale SSH works) ---
# Once you can ssh deploy@<tailscale-ip> reliably, remove this rule
# and rely solely on Tailscale SSH (no public SSH port at all).
ufw allow 22/tcp comment 'SSH fallback — remove after Tailscale SSH verified'

# --- Optional: gateway WebSocket for browser clients ---
# Uncomment if the dashboard or external browser clients need to connect
# to the WebSocket gateway directly from the internet.
# ufw allow 8083/tcp comment 'Gateway WebSocket (public)'

ufw --force enable
ufw status verbose

echo ""
echo "✓ Firewall configured."
echo "  Admin services are now only reachable via Tailscale:"
echo "    Grafana:    http://\$(tailscale ip -4):3000"
echo "    QuestDB:    http://\$(tailscale ip -4):9000"
echo "    Prometheus: http://\$(tailscale ip -4):9090"
echo "    Dashboard:  http://\$(tailscale ip -4):8050"
echo "    Bot API:    http://\$(tailscale ip -4):8090"
echo ""
echo "  Test SSH via Tailscale: ssh deploy@\$(tailscale ip -4)"
echo "  Once confirmed, remove the port 22 rule:"
echo "    ufw delete allow 22/tcp"
```

### `Makefile` — security setup targets

Add to the VPS Deploy section:

```makefile
## Install Tailscale on VPS (run as root — prints auth URL)
setup-tailscale:
	ssh root@$(subst deploy@,,$(VPS)) 'bash -s' < scripts/setup-tailscale.sh

## Apply UFW firewall rules (run after Tailscale is authenticated)
setup-firewall:
	ssh $(VPS) 'sudo bash -s' < scripts/setup-firewall.sh

## Show Tailscale status and IP on VPS
vps-tailscale-status:
	ssh $(VPS) 'tailscale status && echo "" && tailscale ip -4'

## Show UFW status on VPS
vps-firewall-status:
	ssh $(VPS) 'sudo ufw status verbose'
```

### `docs/ops.md` — add security section

Add a new section to the existing ops runbook:

```markdown
## VPS Security Model

### Network access

All admin interfaces are private — only accessible via Tailscale:

| Service    | Port  | Tailscale URL                        | Public? |
|------------|-------|--------------------------------------|---------|
| Grafana    | 3000  | http://100.x.x.x:3000               | No      |
| QuestDB UI | 9000  | http://100.x.x.x:9000               | No      |
| Prometheus | 9090  | http://100.x.x.x:9090               | No      |
| Dashboard  | 8050  | http://100.x.x.x:8050               | No      |
| Bot API    | 8090  | http://100.x.x.x:8090               | No      |
| Gateway WS | 8083  | ws://100.x.x.x:8083                 | Optional|
| SSH        | 22    | Tailscale SSH preferred              | Fallback|

### Adding a new device to the tailnet

1. Install Tailscale on the device: https://tailscale.com/download
2. `tailscale up` — authenticate with the same account used for the VPS
3. The device can immediately reach all VPS admin ports at the Tailscale IP

### Removing SSH port 22 (recommended once Tailscale SSH confirmed)

```bash
ssh deploy@<tailscale-ip>   # confirm this works first
sudo ufw delete allow 22/tcp
sudo ufw status
```

After this, `ssh deploy@<public-ip>` will be refused. Only `ssh deploy@<tailscale-ip>` works.

### Rotating Tailscale auth

If a device is lost or compromised: Tailscale admin console → Machines → Revoke.
The revoked device loses access immediately with no VPS changes needed.

### .env file security

- `.env` and `bot-service/.env` are mode 600 (readable only by `deploy` user)
- Never committed to git (`.gitignore` enforced)
- Rotate API keys: update `.env` on VPS → `make deploy-bot` to restart with new keys
```

## Acceptance Criteria

1. After running `scripts/setup-tailscale.sh` and authenticating, `tailscale status` on the VPS shows the VPS and at least one personal device as connected.
2. `curl http://<tailscale-ip>:3000` from an enrolled dev machine returns Grafana's HTML.
3. `curl http://<public-ip>:3000` from the public internet returns connection refused or timeout.
4. `curl http://<tailscale-ip>:9000/exec?query=select+1` from an enrolled dev machine returns a QuestDB response.
5. `ssh deploy@<tailscale-ip>` (Tailscale SSH) succeeds without specifying a port.
6. `sudo ufw status verbose` shows `tailscale0` is fully allowed and port 22 is the only public rule.
7. `make vps-tailscale-status` and `make vps-firewall-status` return readable output without errors.
8. The ops runbook section exists in `docs/ops.md` with the correct port table.

## Dev Notes

- **Tailscale SSH vs sshd:** `tailscale up --ssh` runs Tailscale's own SSH daemon, authenticated by the tailnet (no separate SSH keys needed for enrolled devices). The standard sshd on port 22 stays as a fallback until the operator confirms Tailscale SSH works, then port 22 can be closed entirely. This is the cleanest end state — no public ports at all.
- **UFW + Docker interaction:** Docker bypasses UFW by default via direct iptables rules. This is why we don't restrict ports in the compose file — Docker publishes them on `0.0.0.0` and bypasses UFW. The `ufw allow in on tailscale0` rule works at the interface level, before Docker's iptables rules, so it correctly allows Tailscale traffic. The public internet is blocked by the default `deny incoming` at the same level. **Do not** install `ufw-docker` or other workarounds — the interface-level allow is sufficient.
- **Verifying the UFW+Docker interaction works:** after setup, `nmap -p 3000,9000,8050 <public-ip>` should show all ports filtered. `curl http://<tailscale-ip>:3000` from an enrolled machine should work.
- **Tailscale exit nodes / DNS:** `--accept-dns=false` prevents Tailscale from overwriting the VPS's system resolver. Not needed unless you set up split-DNS in the tailnet admin console.
- **Multiple dev machines:** enroll each device with `tailscale up` on the same Tailscale account. All enrolled devices can access the VPS immediately — no VPS config changes needed.
- **Gateway port 8083:** if the Dash dashboard's WebSocket client connects from a browser, the browser is not on the tailnet. In that case, expose 8083 publicly and keep all other ports private. Add `ufw allow 8083/tcp` and uncomment the line in `setup-firewall.sh`. The gateway is stateless and low-risk to expose.

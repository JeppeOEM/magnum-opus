---
id: 34-11
title: Tailscale ACL policy — device tagging, port-level access control
epic: 34
status: ready-for-dev
---

# Story 34-11: Tailscale ACL policy

## Context

The default Tailscale ACL is `["*:*": ["*"]]` — every enrolled device can reach every other enrolled device on any port. Story 34-3 enrolls the VPS and the operator's workstations. If a second device is enrolled (e.g. a travel laptop, an iPad), it immediately has the same access as the primary workstation — including the bot API and QuestDB. This story tightens ACLs so only explicitly tagged devices can reach the VPS admin ports.

**Promoted from:** D-34-3 (Tailscale ACLs not configured)

**Model after policy is applied:**
- VPS tagged `tag:server` — never initiates connections
- Operator devices tagged `tag:admin` — full access to all VPS ports
- No other device tags defined — untagged devices have no access to the VPS

## What to build

### Tailscale ACL policy — admin console

This is configured in the Tailscale admin console (https://login.tailscale.com/admin/acls), not in code. The ACL file should be committed to the repo as `docs/tailscale-acl.hujson` for version control and documentation.

`docs/tailscale-acl.hujson`:

```hujson
{
  // Tailscale ACL for magnum-opus VPS
  // Apply this in: https://login.tailscale.com/admin/acls
  // Last updated: 2026-05-20

  "tagOwners": {
    // Only the primary Tailscale account owner can tag devices as admin
    "tag:admin":  ["autogroup:owner"],
    "tag:server": ["autogroup:owner"]
  },

  "acls": [
    // Admin devices can reach the VPS on all ports
    {
      "action": "accept",
      "src":    ["tag:admin"],
      "dst":    ["tag:server:*"]
    },
    // VPS can initiate connections outbound (exchange WebSocket, Telegram API, etc.)
    {
      "action": "accept",
      "src":    ["tag:server"],
      "dst":    ["*:*"]
    }
  ],

  // Disable the default allow-all — only the rules above apply
  "acls": [
    {"action": "accept", "src": ["tag:admin"],  "dst": ["tag:server:*"]},
    {"action": "accept", "src": ["tag:server"], "dst": ["*:*"]}
  ],

  // SSH: only admin devices can SSH to the server
  "ssh": [
    {
      "action": "accept",
      "src":    ["tag:admin"],
      "dst":    ["tag:server"],
      "users":  ["deploy", "root"]
    }
  ]
}
```

### Tagging devices — `docs/ops.md`

Add a section documenting how to tag the VPS and workstations after enrollment:

```markdown
## Tailscale ACL Setup

### Tag the VPS

After `scripts/setup-tailscale.sh` runs and the VPS is enrolled:

1. Go to https://login.tailscale.com/admin/machines
2. Find the VPS (hostname: magnum-opus or similar)
3. Click ··· → Edit ACL tags → add `tag:server`
4. Click Save

### Tag your workstations

For each admin device (laptop, desktop):

1. Go to https://login.tailscale.com/admin/machines
2. Find the device
3. Click ··· → Edit ACL tags → add `tag:admin`
4. Click Save

### Apply the ACL policy

1. Go to https://login.tailscale.com/admin/acls
2. Paste the contents of `docs/tailscale-acl.hujson`
3. Click Save

**After saving:** untagged devices lose access to the VPS immediately. Verify your primary workstation has `tag:admin` before saving or you will lock yourself out. The Linode console (LISH) remains accessible as a break-glass regardless of Tailscale.

### Adding a new trusted device

1. Install Tailscale and `tailscale up` on the new device
2. In Tailscale admin: add `tag:admin` to the device
3. Device immediately has full VPS access — no VPS config change needed

### Revoking a device

1. Tailscale admin → Machines → find device → Expire key or Delete
2. Device loses access within seconds — no VPS changes needed
3. If the device had admin tag, remove it before or instead of deletion

### Break-glass if locked out of Tailscale

Linode LISH console (https://cloud.linode.com → your VPS → Launch LISH Console) provides
out-of-band terminal access regardless of Tailscale state. From LISH you can:
- Check tailscale status: `tailscale status`
- Temporarily re-enable port 22: `sudo ufw allow 22/tcp`
- Re-authenticate: `tailscale up --force-reauth`
```

### `Makefile` — ACL status check

```makefile
## Show Tailscale device list and tags (requires Tailscale CLI on dev machine)
tailscale-status:
	tailscale status
	@echo ""
	@echo "VPS should show tag:server, your device should show tag:admin"
	@echo "If neither has tags, visit https://login.tailscale.com/admin/machines"
```

## Acceptance Criteria

1. `docs/tailscale-acl.hujson` exists in the repo with the policy defined above.
2. After applying the ACL, a device tagged `tag:admin` can reach `http://<vps-tailscale-ip>:3000` (Grafana).
3. A device NOT tagged (freshly enrolled, untagged) cannot reach any port on the VPS via Tailscale.
4. The VPS (tagged `tag:server`) can initiate outbound connections (exchange WebSocket, Telegram API).
5. Tailscale SSH from a `tag:admin` device to the VPS works: `ssh deploy@<tailscale-ip>`.
6. `docs/ops.md` contains the device tagging procedure and break-glass instructions.

## Dev Notes

- **ACL applies immediately:** unlike UFW changes, Tailscale ACL changes propagate to all devices within seconds via the Tailscale coordination server. Test ACL changes from a secondary device while keeping the primary connected to avoid lock-out.
- **`tagOwners`:** tags without defined `tagOwners` cannot be applied to devices. The policy uses `autogroup:owner` (the account owner) as the only entity that can apply `tag:admin` and `tag:server`. This prevents a compromised device from self-tagging as admin.
- **Outbound from VPS:** the `"src": ["tag:server"], "dst": ["*:*"]` rule allows the VPS to make outbound connections to anything — exchange WebSockets (api.bybit.com, api.kucoin.com), Telegram API, Tailscale coordination. Without this rule, the VPS would be blocked from initiating any connections.
- **Auth proxy interaction:** the auth proxy (story 34-4) sits in front of admin services on `:8080`. After Tailscale ACLs are applied, only `tag:admin` devices can reach `:8080`. The two layers are complementary: Tailscale ACL limits which devices can connect, auth proxy requires TOTP even from those devices.
- **MagicDNS:** if Tailscale MagicDNS is enabled, devices can reach the VPS by hostname (`magnum-opus.tail<xxxx>.ts.net`) instead of IP. The ACL above applies regardless of whether you use IP or MagicDNS.

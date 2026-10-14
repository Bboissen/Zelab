# Architecture

```mermaid
flowchart LR
  Owner[Owner: laptop / phone] -->|Tailscale or localhost, authenticated| UIs
  Discord[Discord: owner, later friends / family] -->|Distinct bot identities| Gateway
  subgraph VM[Small KVM guest: Debian]
    Gateway[Native Hermes gateway]
    Gateway --> Operations[Operations profile: owner-only]
    Gateway --> Research[Research / reserve profiles: parked]
    HermesUI[Native Hermes dashboard]
  end
  subgraph Host[Linux server]
    UIs[Tailscale Serve] --> Paperclip
    UIs --> Obsidian[Obsidian container]
    UIs --> HermesUI
    Paperclip[Paperclip coordinator + embedded PostgreSQL] --> Workers[Optional Codex / Claude Code workers]
    Workers --> OS[Host OS / Docker / repositories]
    Vault[(Notes vault)] <-->|/vault| Obsidian
    Workers <--> Vault
  end
  Operations -->|Task bridge, project-scoped key| Paperclip
  Paperclip -->|Hermes API adapter, separate key| Gateway
  Operations <-->|SSHFS: Private + Shared| Vault
  Research <-->|SSHFS: Shared only| Vault
```

## Components

| Component | Placement |
| --- | --- |
| Hermes | Unmodified upstream checkout in the guest, pinned by commit; one shared gateway runs every profile under the `zelab-hermes` account |
| Hermes dashboard | Guest service on port 8640, basic-auth; forwarded to host loopback and published through Tailscale Serve |
| Paperclip | Host service under `zelab-paperclip`, authenticated private mode, loopback only; embedded PostgreSQL reachable only by root, the operator and the service account (nftables) |
| Host worker | One Paperclip agent, Operations, running as `zelab-ops` (unattended sudo) through Paperclip's SSH environment driver. Setup offers pinned Codex, Claude Code or skip: a harness creates the agent fully configured except its model, plus a Backlog task that proves host access and sudo; skip creates no worker, account or Discord task bridge. Existing native settings are retained. |
| Notes | Host vault `/var/lib/zelab-vault/{Private,Shared}`; a dedicated notes-only SFTP listener on loopback; SSHFS mounts in the guest per audience |
| Obsidian | linuxserver.io container pinned by digest, loopback-published, password from a file, vault bind-mounted |
| Provisioning | Ansible roles under `deploy/`, systemd units, one Compose file; Python entry points in `deploy/` and `implementation/` |

## Flows

1. The owner asks Operations in an admitted Discord conversation.
2. Hermes runs the bundled `paperclip-task-bridge` skill. Task creation uses
   `submit-task.mjs`, a stateless client that derives a Paperclip idempotency
   key from Hermes' per-message identity, so retries never duplicate work.
3. The task lands in the Operations project assigned to the `zelab-ops` worker.
   Once a harness is selected and authenticated, Paperclip opens the SSH
   environment and runs Codex or Claude Code there with real host access.
   Each run copies Paperclip's own folder to the worker's `workspace` and
   copies it back afterwards; the two folders are never the same path.
4. Operations reads the result through the same scoped key and replies in the
   originating conversation with host identity, timestamp and task ID.
5. Paperclip can also assign work to Hermes through its native gateway adapter,
   using a second, separately scoped key.

## State

| State | Location |
| --- | --- |
| Guest disk and cloud-init | `vm_data` (default `/var/lib/zelab-vm/data`) |
| Hermes profiles, memory, logins, Discord token | `/var/lib/zelab/hermes/home` inside the guest |
| Paperclip database, uploads, secrets key | `paperclip_home` (default `/var/lib/zelab-paperclip`) |
| Coordinator-side worker workspace | `paperclip_home/worker-workspaces/ops` |
| Worker home | `/var/lib/zelab-ops` (`/var/lib/zelab-code` only on sites from the earlier two-worker layout) |
| Notes vault, Obsidian configuration | `/var/lib/zelab-vault`, `/var/lib/zelab-obsidian` |
| Deployment keys, Hermes password hash, resource IDs and optional legacy recovery copies | `private_dir` (default `~/.local/state/zelab-deployment`) |
| UI password working copies | Private temporary `/dev/shm/zelab-passwords-*` directory during `make`; removed on exit |
| Obsidian native startup password | Root-owned `/etc/zelab-obsidian/password` |

Hermes memory stays in Hermes; task history stays in Paperclip; the vault holds
durable human-readable notes. `make backup` archives all of the above together
with the source pins, because a database dump or `.env` alone cannot rebuild a site.

## Network

All backends bind to host loopback. Tailscale Serve publishes the three UIs as
HTTPS on the tailnet only; existing Serve routes are never replaced and Funnel
is refused. The guest reaches the host only on the Paperclip API port and the
notes SFTP port; private ranges, link-local and IPv6 are rejected by nftables
inside the guest, and restricted tool UIDs are additionally blocked from the
Paperclip port. Worker SSH keys carry `from=` and `restrict` options and an
`AllowUsers` entry is added only when the host already uses one.

## Resources

Defaults: 1 vCPU, 2 GiB guest RAM, 50 % CPU quota, 2304 MiB memory cap, no
guest swap, 16 GiB disk; 8 GiB host RAM and 20 GiB disk reserves. A fresh
install may temporarily borrow up to 70 % of idle CPU and RAM for the first
boot and restores the normal limits afterwards (`vm_install_resource_fraction`).
That restore restarts the guest; until Hermes answers it may use whole vCPUs,
then the normal CPU quota is applied live (Hermes takes about a minute to start
under a 50 % quota).
Hermes web assets are built on the host, not inside the small guest.

Install order overlaps host and guest work: once the guest is sized and booting,
the Obsidian image pull, Hermes web build and Paperclip `npm ci` run as background
Ansible jobs, and Paperclip installs while the guest prepares Hermes dependencies.
Reserve profiles are created together and their admission checks run in
parallel; the gateway stage rechecks only active profiles, since parked ones
were checked with their restricted tools. Each role waits for its own job.
A successful run ends with `prune.yaml`, which deletes downloads, build trees,
releases and images the current pins no longer use; the guest disk uses
`discard=unmap`, so space freed in the guest returns to the host.

## Upstream references

- [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles/),
  [shared gateway](https://hermes-agent.nousresearch.com/docs/user-guide/multi-profile-gateways),
  [dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard),
  [Discord](https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord)
- [Paperclip deployment modes](https://docs.paperclip.ing/reference/deploy/deployment-modes/),
  [Hermes gateway adapter](https://docs.paperclip.ing/reference/adapters/hermes-gateway/),
  [bundled task bridge](https://github.com/paperclipai/paperclip/blob/master/packages/adapters/hermes/README.md),
  [Codex adapter](https://docs.paperclip.ing/reference/adapters/codex/),
  [Claude Code adapter](https://docs.paperclip.ing/reference/adapters/claude-code/)
- [Obsidian container](https://docs.linuxserver.io/images/docker-obsidian/),
  [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

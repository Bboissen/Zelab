# Zelab

Chat with your agents, track their work in Paperclip, and keep their notes in
Obsidian. Zelab installs upstream applications on a Linux home server:

- [Hermes](https://github.com/NousResearch/hermes-agent) runs in a KVM guest,
  with one native profile and chat identity per bot.
- [Paperclip](https://github.com/paperclipai/paperclip) runs on the host. It
  tracks tickets and dispatches work to bots or a team running Claude Code and
  Codex over SSH, with one host account per rights tier.
- A shared notes vault stays on the host. `make notes` syncs it with a private
  GitHub repository for desktop Obsidian. Browser Obsidian is optional and off
  by default.

Ask a bot in Discord, Telegram or another supported chat app to research a
topic: it files a ticket, works on it when Paperclip starts its run, writes
notes under `Projects/<slug>/`, and replies in chat. Server work goes to the
team's PM, who assigns it to the appropriate team member.

**Experimental:** source checks and isolated qualification cover parts of the
system. The complete chat-to-team return path, recovery and network acceptance
still have **NOT RUN** items. See [Status](docs/STATUS.md).

## Where everything runs

![Zelab architecture: chat and model services connect to Hermes in a KVM guest; private tailnet HTTPS reaches authenticated UIs; host Paperclip dispatches agents over SSH; SFTP shares the vault with the guest; host snapshots and private Git sync retain notes.](docs/assets/architecture.svg)

Blue links use encrypted transport. Amber links use local HTTP. Green links
stay on the host filesystem. Placement and transport do not imply that an
agent's actions are safe; its account and credentials determine its authority.

| Link | Protection | Limit |
| --- | --- | --- |
| Your devices → administration UIs | Tailnet-only HTTPS through Tailscale Serve, plus native app authentication; public Funnel is refused | Tailnet permissions and app logins both matter. TLS ends at Serve |
| Serve → Paperclip / Hermes dashboard | HTTP on host loopback; Hermes is reached through a loopback QEMU port forward | These local hops have no TLS; they trust the host and VM boundary |
| Chat / model services ↔ agents | Provider TLS, bot tokens, model logins and Hermes chat allowlists | Providers receive the messages or prompts sent to them; TLS does not prevent prompt injection |
| Hermes → Paperclip | HTTP through QEMU's host gateway, authenticated by a task key scoped to selected projects and assignees | The key limits ticket access; the guest-to-host hop has no TLS |
| Paperclip → Hermes runs | HTTP through a host-loopback QEMU forward, with a separate API key per profile | Keys select profiles; all profiles share one guest account |
| Paperclip → host team | SSH with a key per tier, restricted by source address and native SSH options | The tier account grants command execution; the key is not a command allowlist |
| Guest ↔ vault | SSHFS over key-authenticated SFTP, pinned host key, chroot, no shell or forwarding | Every bot can read, edit and delete the whole vault |
| Vault ↔ desktop notes | Deliberate SSH Git sync through a private repository; server deploy key and Git metadata stay outside the vault | Notes leave the server for GitHub and your laptop; keep Obsidian plugins that execute note content off |
| Vault → local history | Root-only Git snapshots every five minutes, outside the shared vault | Protects against direct bot edits; host root can alter it. Use encrypted backups too |

The guest firewall permits public internet traffic, DNS/DHCP for QEMU, and the
Paperclip API and notes SFTP exceptions. It rejects other private networks,
tailnet addresses and IPv6 for normal bot traffic. These rules live inside the
guest; guest root can change them. Read the [architecture](docs/ARCHITECTURE.md)
and [trust model](docs/TRUST_MODEL.md) for the exact boundaries.

## Understand the host privileges

Bots share the guest account, subscription logins and vault: profiles and
project folders are **not isolation boundaries**. Use a separate site for
projects or audiences that must stay separate.

Chat sessions have no command tools, but can create tickets that trigger
command-capable bot runs or reach the host team through the PM. The default
team includes:

| Tier | Default members | Host authority |
| --- | --- | --- |
| `dev` | PM, frontend, backend, mobile, game, data | Own workspaces, configured paths and shared vault; no sudo |
| `ops` | Devops | Docker access, which is root-equivalent on most hosts |
| `root` | Sysadmin | Full sudo: root on the host |

Team runs have no per-command approval or CLI sandbox. A steered bot or PM can
therefore send work to privileged team members without an owner approval step.
Narrow or remove those tiers in `config/team.yaml` if you do not want that
authority. Prompt instructions do not enforce access control. Root can read
host credentials and change note history.

## Install

Use an x86-64 Debian or Ubuntu server with KVM, Docker Compose, OpenSSH,
Python 3.12+ and Make. From the server checkout:

```sh
make
```

Or, from a Mac/Linux laptop with Python 3.9+, Make and SSH, drive an existing
server checkout:

```sh
make REMOTE_HOST=user@server REMOTE_DIR=/absolute/path/to/Zelab
```

Plain `make` installs, reapplies or resumes while keeping existing state. It
asks for your sudo password to configure unattended administration, discovers
host settings into the ignored `config/site.yaml`, and opens Hermes' native
model sign-in and chat-app wizards for each new bot. Passwords and tokens go
into terminal prompts, never Git. It finishes with a private setup guide and
the remaining account steps.

Follow the [setup checklist](docs/SETUP.md) for resources, Tailscale, chat apps
and logins.

## Commands

Run these from the checkout:

| Command | Effect |
| --- | --- |
| `make` | Install, reapply or resume; refresh the setup guide |
| `make bot` | Add, set up or remove Hermes bots; requires a terminal |
| `make notes` | Set up or sync desktop notes through a private Git repository |
| `make backup` | Create and verify an encrypted application backup |
| `make restore` | Verify and restore a backup onto a compatible installed site |
| `make clean` | Reset local deployment choices and generated files; keep installed data |
| `make fclean` | Preview and confirm removal of this site's installation and data |
| `make re` | Full reset, then reinstall |
| `make help` | List all nine commands |

`fclean` and `re` delete notes, tasks and application logins. Noninteractive
destructive runs need `CONFIRM=<host:vm>`; restore also needs
`RESTORE_ARCHIVE`. See [Operations](docs/OPERATIONS.md).

## Configure your site

| Input | Purpose |
| --- | --- |
| `config/site.yaml` | Discovered host settings and local overrides; ignored by Git |
| `config/projects.yaml` | Your projects and tier path grants; ignored by Git; start from [the example](config/projects.example.yaml) |
| `config/team.yaml` and `config/team/` | Team membership, tier presets, harness, model and initial personas |
| `make bot` | Native bot profiles, provider logins, chat apps, personas and project task keys |

Team settings apply when hiring; later edits in Paperclip remain authoritative.
Every upstream download is pinned and verified. Application state, credentials,
private notes and deployment logs belong outside this public source repository.
GitHub account names and commit attribution are public repository metadata.

## Documentation

- [Setup](docs/SETUP.md) · [Operations](docs/OPERATIONS.md) · [Architecture](docs/ARCHITECTURE.md)
- [Trust model](docs/TRUST_MODEL.md) · [Status](docs/STATUS.md)
- [Security policy](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Releasing](docs/RELEASING.md)

Zelab's own source is [MIT licensed](LICENSE). Hermes, Paperclip, Codex, Claude Code,
Obsidian and Tailscale keep their own licenses and account requirements.

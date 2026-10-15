# Zelab

Private Discord agents for a home server, built from upstream parts:
[Hermes](https://github.com/NousResearch/hermes-agent) runs in a small KVM guest
and answers Discord; [Paperclip](https://github.com/paperclipai/paperclip)
coordinates work on the host; optional Codex or Claude Code workers execute it; Obsidian keeps shared
notes. Administration UIs are reachable only through Tailscale or localhost.

The owner asks the Operations bot in Discord for a host report. Hermes submits
a scoped Paperclip task, a host worker gathers the real measurements, and the
answer returns to the same conversation with the host identity, timestamp and
task ID. Restricted profiles (Research, reserve bots) get separate unprivileged
tool accounts and no bridge credential.

## Install

On the Linux server, from this checkout:

```sh
rtk proxy make
```

From a Mac/Linux laptop, driving an existing server checkout over SSH:

```sh
rtk proxy make REMOTE_HOST=user@server REMOTE_DIR=/absolute/path/to/Zelab
```

Plain `make` installs, reapplies or resumes. It asks for your sudo password
once, writes `config/site.yaml` from what it discovers on the host, keeps tool
output in a private log, and ends with the URLs, password-file locations and
the account steps still waiting for you. Setup offers Codex, Claude Code or skip.
Codex or Claude Code creates one Paperclip Operations worker, fully configured
except its model, with a waiting task that proves host access and sudo. Skip
creates no worker.
Rerunning keeps all data and existing native agent settings.

Follow the [setup checklist](docs/SETUP.md). Server requirements: x86-64
Debian or Ubuntu with KVM, Docker Compose, OpenSSH, Python 3.12+, Make and
[RTK](https://github.com/rtk-ai/rtk).

## Commands

| Command | Effect |
| --- | --- |
| `rtk proxy make` | Install, reapply or resume; refresh the guide |
| `rtk proxy make clean` | Reset local deployment choices and generated files; keep installed data |
| `rtk proxy make fclean` | Preview and confirm removal of this site's installation/data, then reset local choices |
| `rtk proxy make re` | Full reset, then reinstall |
| `rtk proxy make backup` | Verified encrypted application backup |
| `rtk proxy make restore` | Verify and restore a backup onto a compatible installed site |
| `rtk proxy make help` | List these commands |

`fclean` and `re` delete notes, tasks and application logins. Noninteractive
destructive runs need `CONFIRM=<host:vm>`. See [operations](docs/OPERATIONS.md).

## Security model in one paragraph

Operations is an owner-only bot whose host worker has real sudo; everyone else
is restricted by OS accounts, scoped API keys and a guest firewall, not by
prompt text. Secrets are generated on the host, stored with mode 0600 outside
Git, and never rendered into logs. Every upstream download is checksum-pinned.
Hermes profiles still share one runtime UID, so mutually untrusted audiences are
not supported yet. Read the [trust model](docs/TRUST_MODEL.md) before inviting
anyone but yourself, and [status](docs/STATUS.md) for what has and has not been
verified. This is experimental software.

## Documentation

- [Setup](docs/SETUP.md) · [Operations](docs/OPERATIONS.md) · [Architecture](docs/ARCHITECTURE.md)
- [Trust model](docs/TRUST_MODEL.md) · [Status](docs/STATUS.md)
- [Security policy](SECURITY.md) · [Contributing](CONTRIBUTING.md) · [Releasing](docs/RELEASING.md)

Zelab's own source is [MIT licensed](LICENSE). Hermes, Paperclip, Codex, Claude Code,
Obsidian and Tailscale keep their own licenses and account requirements.

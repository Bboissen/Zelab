# Zelab

Chat with your bots, give work to a coding team, and keep the results in your
notes. Zelab brings three upstream tools to a Linux home server:

- **[Hermes](https://github.com/NousResearch/hermes-agent):** bots that talk,
  research and reply in Discord, Telegram and other chat apps.
- **[Paperclip](https://github.com/paperclipai/paperclip):** tasks, progress and
  a coding team that builds and maintains your projects.
- **A shared notes vault:** personal and project knowledge you can read and
  edit in desktop Obsidian on your laptop.

## Where everything runs

![Zelab: Hermes bots run in a VM; Paperclip dispatches coding agents on the host to edit repositories and run commands. Bots and team also write shared notes, synced to desktop Obsidian through private Git.](docs/assets/architecture.svg)

Hermes bots use their **bundled Obsidian skill** to write notes directly,
without a ticket or project. Personal work goes in `Hermes/<bot>/`; project
knowledge goes in `Paperclip/Projects/<project>/`. Both bots and the coding
team can edit project notes.

Use Paperclip when work needs tracking, delegation or the host team. A keyed
bot creates and reads tickets through **Paperclip's official MCP server**;
Paperclip starts bot runs through Hermes' gateway API. Simple notes and
research stay in the current chat, avoiding an extra dispatched run.

Bots write Markdown files into the server vault. Desktop Obsidian reads a
laptop clone, synced through a private Git repository with `make notes`
and `git pull`.

## Try it

Ask your bot to save a new note with a unique phrase in its Hermes folder,
read it back and give you its path. No Paperclip ticket is needed. Check the
file on the server before syncing it to your laptop. See the
[step-by-step notes check](docs/NOTES.md#check-that-a-bot-really-wrote-a-note).

## Install

Use an x86-64 Debian or Ubuntu server with KVM, Docker Compose, OpenSSH,
Python 3.12+ and Make. From the server checkout:

```sh
make
```

Or drive an existing server checkout from a Mac/Linux laptop:

```sh
make REMOTE_HOST=user@server REMOTE_DIR=/absolute/path/to/Zelab
```

The laptop needs Python 3.9+, Make and SSH. `make` installs, reapplies or
resumes, keeping existing data. It discovers your host settings, asks for
passwords in your terminal, opens the native account setup wizards and finishes
with a private setup guide. Follow the [setup checklist](docs/SETUP.md).

## Commands

Run these from the checkout:

| Command | Effect |
| --- | --- |
| `make` | Install, reapply or resume |
| `make bot` | Add, set up or remove bots; requires a terminal |
| `make notes` | Set up or sync the private notes repository |
| `make backup` | Create and verify an encrypted backup |
| `make restore` | Verify and restore a backup onto a compatible installed site |
| `make clean` | Reset local choices and generated files; keep installed data |
| `make fclean` | Preview and confirm deletion of this site's installation and data |
| `make re` | Full reset, then reinstall |
| `make help` | List all nine commands |

`fclean` and `re` delete notes, tasks and application logins. Noninteractive
destructive runs need `CONFIRM=<host:vm>`; restore also needs
`RESTORE_ARCHIVE`. See [Operations](docs/OPERATIONS.md).

## Know the boundaries

- Dashboards use private tailnet HTTPS and app logins. Local task/API links
  use HTTP with scoped keys; encrypted access to a dashboard does not encrypt
  every internal link.
- Chat file tools can access what the shared guest account can access, beyond
  the vault. Bots share that account, its credentials and the whole vault.
  Projects and bot profiles do not isolate private data from each other.
- Chat can trigger command-capable bot runs and reach host team members through
  the PM. Team permissions depend on their role: `dev` has no sudo, while
  `ops` has Docker access (root-equivalent) and `root` has full sudo. Runs have
  no per-command approval or CLI sandbox; root can change history and read
  host credentials.

See the [Discord-to-host security diagram](docs/assets/security.svg).
The guest firewall limits normal bot traffic, but guest root can change it.
Choose narrow team permissions and separate sites for audiences that need
isolation. The [architecture](docs/ARCHITECTURE.md#connections) explains each
link; the [trust model](docs/TRUST_MODEL.md) records the limits.

**Experimental:** complete chat-to-team delivery, recovery and network checks
still have **NOT RUN** items. An upstream Feishu webhook security finding
remains open. See [Status](docs/STATUS.md).

## Make it yours

Use the ignored `config/site.yaml` for host settings and
`config/projects.yaml` for your projects and path grants; start from
[the project example](config/projects.example.yaml). Choose team roles,
harnesses and initial personas in `config/team.yaml` and `config/team/`.
Later edits in Paperclip remain authoritative. Use `make bot` for bot profiles
and logins. Keep private inputs, credentials, notes and logs out of this
public source repository.

## Documentation

[Setup](docs/SETUP.md) · [Notes](docs/NOTES.md) · [Operations](docs/OPERATIONS.md) ·
[Architecture](docs/ARCHITECTURE.md) · [Trust model](docs/TRUST_MODEL.md) ·
[Status](docs/STATUS.md) · [Security](SECURITY.md) ·
[Contributing](CONTRIBUTING.md) · [Releasing](docs/RELEASING.md)

Zelab's own source is [MIT licensed](LICENSE). Its upstream applications keep
their own licenses and account requirements.

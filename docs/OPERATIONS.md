# Operations

For installation and Discord configuration, use [SETUP.md](SETUP.md); for
what has been verified, [STATUS.md](STATUS.md). Run commands from the
repository root as the configured operator.

## Routine commands

Run from the checkout with `rtk proxy`. Only these nine Make commands exist:

| Command | Role |
| --- | --- |
| `make` | Install/reapply/resume; keep state and refresh/open the guide |
| `make clean` | Reset local deployment choices and remove generated controller/bytecode; keep installed data |
| `make fclean` | Preview, confirm and remove this site's installation/application data, then reset local choices/generated files |
| `make re` | Complete fclean, then install again; reset finishes before install even under parallel Make |
| `make help` | Print the nine commands |
| `make backup` | Create/verify an encrypted application backup and resume previously running services |
| `make restore` | Select, authenticate, confirm and restore a backup to a compatible installed site |
| `make bot` | Add, set up or remove Hermes bots; terminal only |
| `make notes` | Sync the server vault with its private Git remote, for desktop Obsidian ([NOTES.md](NOTES.md)) |

`make` includes readiness checks, resumable provisioning, private logs and the
guide. Source checks are developer tooling: `rtk proxy python3 tools/check.py`.

A successful `make` ends by deleting what the current pins no longer use:
consumed downloads and the Hermes build tree in `/var/cache/zelab`, superseded
runtimes in `/opt/zelab-tools`, earlier Paperclip releases, npm caches, unused
earlier Obsidian images, guest package files and superseded Hermes installers,
and all but the five newest private install logs. The guest then returns freed
blocks to the host so `disk.qcow2` shrinks. Shared tools stay while another Zelab
guest on the host may use them. A failed run removes nothing, so it can resume.

`clean` removes `.venv`, Python caches, `.cache/`, `dist/`, root `node_modules/`,
test/lint/coverage outputs and clears repo choices from the selected
local, untracked site file: VM resource limits and install boost, host reserve
preferences and optional apps. `config/projects.yaml` is kept.
Host connections, account identities, ports and installed resource paths remain
so subsequent commands can find the same installed site. `.env` contains external
path selectors and is retained. Public defaults/examples, tracked input files
and input files outside this checkout are never reset. Ambiguous YAML
merges/anchors/flow mappings are refused before cleanup; use plain top-level keys.

`fclean`/`re` additionally delete Zelab's VM, bots and their logins, tasks,
notes and their history, Obsidian state, project accounts (with their sudo
grants and path ACLs), owned units/routes and project-installed software/cache.
Host/path inputs, `config/projects.yaml`, backups, recovery key, private logs,
host SSH/Tailscale and unrelated services are preserved.
A later `make` rebuilds the controller.

Full cleanup also finds disposable qualification artifacts in the operator's
`~/.local/state`, `/tmp` and `/var/tmp`. New scratch fixtures carry a
`.zelab-artifact.json` receipt naming their exact directory and checkout;
`tools/check_paperclip.py` writes it before starting its disposable server.
For older private `zelab-v*-qual` workspaces, a qualification report and an
owned script referencing this checkout identify only the known build, download,
runtime and VM subdirectories. Reports, private logs, other saved state and Git
recovery bundles outside those subdirectories stay. A prefix alone is never
enough to remove a directory.

The preview lists these outputs and any qualification services to stop. A VM's
transient systemd unit must run an operator-owned QEMU using the identified qualification
disk; a scratch Paperclip user unit must contain PostgreSQL using the identified
runtime and its qualification database. Other services stay. Cleanup refuses
artifact directories still used by another process, symlink aliases, mounted
trees and generated checkout paths containing tracked files. It stops identified
qualification services before checking for remaining artifact users, and checks
those users before stopping the live site.
Plain `make clean` only clears the checkout; full cleanup requires the existing
site confirmation.

`fclean` prints exact ownership evidence and data loss before asking for the
displayed `host:vm_instance` identity. Noninteractive use supplies `CONFIRM=<site>`.
Wrong/missing confirmation removes nothing. Writers stop and notes exports
unmount first; deletion refuses symlinks, mounts and preserved paths.

From a laptop, use the same install command:
`rtk proxy make REMOTE_HOST=user@server REMOTE_DIR=/absolute/checkout`.
Optional `REMOTE_PORT` and `GUIDE_DEST` select port/local guide path. No laptop
inputs or credentials are uploaded. Run maintenance commands on the server.

Private logs use 0600 files in 0700 directories. Deployment and native
backup/restore/lifecycle helpers share a nonblocking maintenance lock.
Do not mutate the same state manually during maintenance.

## Bots

`make bot` opens a terminal menu; it refuses to run without one. A fresh plain
`make` asks how many bots to set up and runs the same steps for each.

| Menu | What runs | Native to |
| --- | --- | --- |
| Add a bot | `hermes profile create <name> --description <role>`; a new `API_SERVER_KEY` in the profile; Paperclip's task-bridge skill copied into it; `terminal.env_passthrough` for the bridge variables; the `paperclip` MCP server, chat tools without commands (`platform_toolsets`) and `tools.tool_search.enabled: off` through `hermes config set`; Paperclip `/agent-hires` plus approval for a `hermes_gateway` agent at `/p/<name>` | Hermes, Paperclip |
| Model sign-in | `hermes -p <name> setup model`: Hermes' provider menu, including subscription logins | Hermes |
| Chat app | `hermes -p <name> gateway setup`: Hermes' platform menu; the token is typed there. For Discord, make bot then asks whether other bots may hand work over by @mention (`DISCORD_ALLOW_BOTS=mentions`) | Hermes |
| Persona | The profile's `SOUL.md`, edited in nano or pasted | Hermes file |
| Task key | A `task_bridge` key for the projects you pick, allowed to assign to every bot; replaces and revokes the previous key | Paperclip |
| Sign the team in | Paperclip's own local subscription sign-in for Claude (or Codex), saved as an AI connection | Paperclip |
| Remove a bot | After you type its name: terminate its Paperclip agent (its keys are revoked), `hermes profile export` (no logins or tokens) to `profile-exports/`, `hermes profile delete` | Hermes, Paperclip |

On quit, make bot rewrites each bot's `zelab` skill (projects with their IDs and
vault folders, the other bots with their agent IDs) and widens task keys to
every current bot; it restarts the shared gateway once when a bot was added,
signed in, connected to a chat app or removed.

## Projects and the team

`config/projects.yaml` (copy `config/projects.example.yaml`; kept out of Git)
declares projects by slug. Each `make` creates a missing Paperclip project with
the declared name and the vault folder `Projects/<slug>/`. Projects grant no
host rights; removing one keeps its Paperclip project, tickets and notes.

`config/team.yaml` declares the rights tiers and the default team. Each tier is
an account `zelab-team-<tier>` (home and workspace below `projects_dir`) with
its preset's sudoers fragment, groups and recursive ACLs on its `paths`,
read/write on the vault, an SSH key restricted to the host's own address, and
the Paperclip SSH environment `<tier> host`. A site sets a tier's `paths` (or
`preset`) under `tiers:` in `config/projects.yaml`. A removed tier loses its
account, grants and environment on the next `make`, and so do the per-project
accounts `zelab-p-<slug>` of earlier versions.

`make` hires each member once as a Claude Code agent (`claude_local`, Claude
CLI engine, the file's `model` and `effort`) on its tier's environment, the lead
first and the others reporting to it. Its first `AGENTS.md` is
`config/team/<member>.md`, then `config/team/TEAM.md` (shared rules), then a
generated section about this server; Paperclip versions it and `make` never
rewrites it. Runs use the owner's Claude sign-in (`make bot`, option 4, or when
Paperclip asks on the first task). Removing a member from the file does not
fire it: terminate it in Paperclip. A member still in the file is hired again
on the next `make` after it is terminated (Paperclip cannot resume a terminated
agent). Members whose manager was terminated move under the live lead, and
`make` lists open tickets the terminated member still holds; members you put
under another manager or at the top level stay there.

After hiring, change a member's harness, model and AI connection in Paperclip.
`make` preserves those choices. When switching Claude Code to Codex, also select
the shared Codex subscription under **AI connection**, then choose a Codex model
and save. The pinned UI retains the previous connection until you select the new
one, so it shows a compatibility error in between. **Responsible user's
connection** requires that user's personal connection; it does not select the
company-shared subscription. `make bot` option 4 completes a Claude sign-in only
for Claude members still using the initial binding; it preserves other explicit
connections and harnesses. When a subscription login expires ("OAuth access
token has expired"), option 4 signs the existing company-shared connection in
again (Paperclip's reconnect), so every agent bound to it keeps working. `make`
moves Claude members off a Claude connection that needs a new sign-in onto the
connected company-shared one.

Once the lead is hired, `make` files the team's first ticket for it, once per site
(`config/team/ONBOARDING.md`, Paperclip's `idempotencyKey`, and the marker
`private_dir/team-onboarding.json`): map the server, read-only, into the vault
folder `Server/` (Infrastructure.md, Runbooks/, Findings.md, Decisions.md).
Every member and every bot's `zelab` skill points at that map.

Bots may assign only the lead (the PM). Chat has no command tools; the `zelab`
skill lists the team and the recipe: from chat, the bot files the job for the
lead and a reply ticket for itself that waits on the job (`blockedByIssueIds`),
using the Paperclip MCP tools. When the job is done Paperclip wakes the bot once
(`issue_blockers_resolved`); it answers the chat in plain words with the native
profile-scoped `hermes send` CLI and closes the ticket. The pinned Hermes has no
agent-callable `send_message` tool. If delivery fails, the bot files an
unassigned `Chat delivery failed: <bot>` ticket with the answer and the error.
Agents you hire yourself on a `<tier> host` environment are listed too; run
`make` afterwards so the skills know them.

Simple status questions default to **quick**: one narrow current check, its result
and relevant limits. **Standard** covers a reported fault or implementation;
**deep** is an explicitly requested audit. The bot and PM preserve that scope
through the job. Chat gets one short acknowledgement and one short answer, written
for the person who asked, in their language, without technical detail; ticket IDs,
routing and detailed evidence stay in Paperclip unless requested.

Depth describes the work; the native model effort setting controls reasoning.
For an existing team member, edit **Thinking effort** in its Paperclip agent
configuration ([upstream fields](https://docs.paperclip.ing/reference/adapters/claude-code/)).
For a Hermes bot, use its native profile configuration, `agent.reasoning_effort`.
`config/team.yaml` sets effort at hire; changing it does not retune existing
members. No model or effort setting is changed by refreshing the skill.

Plain `make` refreshes the managed bot skills and each bot's Paperclip run
prompt (the delivery and closing steps). Existing team instructions are owned
in Paperclip: to adopt a newer `config/team/` text, replace the member's
`AGENTS.md` in Paperclip (its instruction history can restore the old one).
Reapply preserves existing team instructions and SOUL files.

`config/defaults.yaml` holds public defaults. `config/site.yaml` (generated once
by `make`, never overwritten) holds only host-specific values and any
overrides. Optional `.env` overrides:

| Variable | YAML field |
| --- | --- |
| `ZELAB_PRIVATE_DIR` | `private_dir` |
| `ZELAB_DEPLOY_KEY` | `deploy_key` |
| `ZELAB_KNOWN_HOSTS` | `guest_known_hosts` |

Use absolute paths and private file permissions. No shell expansion or inline
tokens. Unknown/duplicate `.env` keys and component port collisions are rejected.
Use `SITE_CONFIG=... ENV_FILE=...` with Make to select another input pair;
explicitly choose an absent/empty override file for a separate guest test.

| State | Default location |
| --- | --- |
| VM disk and cloud-init | `/var/lib/zelab-vm/{data,bootstrap}` |
| Hermes native state inside VM (one profile per bot) | `/var/lib/zelab/hermes/home` |
| Paperclip database, uploads, agent state, AI connections | `/var/lib/zelab-paperclip` |
| Team tier homes | `/var/lib/zelab-projects/<tier>` |
| Durable notes | `/var/lib/zelab-vault/Projects/<slug>` |
| Vault history (root only) | `/var/lib/zelab-vault-history` |
| Notes sync: Git directory, deploy key, GitHub host keys | `private_dir/notes-sync` (operator only) |
| Browser Obsidian settings (kept when it is off) | `/var/lib/zelab-obsidian` |
| Deployment keys, Hermes password hash and resource IDs | Configured `private_dir` |
| Browser Obsidian startup password | `/etc/zelab-obsidian/password` (root-only) |

Hermes native memory stays in Hermes. Paperclip task state stays in Paperclip.
The vault holds durable notes, one folder per project; every bot sees all of it.

### Restoring notes from the vault history

The host takes a git snapshot of the vault every 5 minutes into a root-only
directory the guest cannot reach. To bring back a folder as it was:

```sh
rtk proxy sudo git --git-dir=/var/lib/zelab-vault-history log --oneline -- Projects/<slug>
rtk proxy sudo git --git-dir=/var/lib/zelab-vault-history checkout <commit> -- Projects/<slug>
```

The restored files appear in the guest at once, and on the laptop after the next
`make notes` and `git pull`. The history and the notes sync are separate: the
history snapshots every change, synced or not.

Administration backends bind to localhost; Tailscale Serve supplies private HTTPS.
No LAN proxy or public tunnel is required. Passwords remain required locally.

Fresh `make` asks for the UI passwords with hidden terminal input and
confirmation. Keep them in your password manager. Hermes retains a native
scrypt hash; Paperclip owner login is staged only for the provisioning run in
a private RAM directory, so future applies request its current password.
A browser Obsidian, if turned on, retains the protected startup file its container requires.
Existing deployment password copies are reused without rotation, and may be
removed after a successful apply only when you confirm password-manager custody
in the terminal. No input comes from chat, command arguments or shell-sourced `.env`.
After cleanup, unattended reapply needs the retained legacy Paperclip copy or
must run interactively; it never invents a replacement owner password.

## Backup and recovery

```sh
rtk proxy make backup
rtk proxy make restore
rtk proxy make restore RESTORE_ARCHIVE=/absolute/path/backup.tar.gpg
```

Backups go to `backup_dir` (default `~/backups/Zelab`); encryption uses
`private_dir/backup-passphrase`, or `BACKUP_KEY_FILE=/absolute/private/key`.
Keep an independent password-manager copy. Backups stop writers, encrypt and
compare their state, then resume only previously running services. Output cannot
overlap sources. The backup directory must be private to root/operator (0700 or
an operator-only ACL). Retain backups for 90 days; there is no scheduled backup
or automatic pruning.

The archive includes VM/backing chain (every bot's profile and logins),
coordinator state/keys, project homes, vault and its history, Obsidian state, deployment credentials, native host configuration and
source/runtime pins. A database dump or `.env` alone cannot restore the site.

Interactive `restore` lists backups and asks which to use. Noninteractive use
requires `RESTORE_ARCHIVE`. Before changing live state it authenticates the
complete archive, validates its member/link graph, extracts privately and compares
files/metadata. The VM path, full state, runtime pins, service ownership and
account UID/GID must match a compatible installed site. After `fclean`, run
`make` first: state restoration does not provision missing software/accounts.
Different-host/version migration is separate.

Restore previews exact application paths and requires the displayed site identity
(`CONFIRM=<site>` noninteractively). Run `make` with the same `config/projects.yaml`
before restoring, so the project accounts exist. Current inputs,
SSH/Tailscale, other services, encrypted backups, recovery key and private logs
stay. Application/VM state, notes/Obsidian configuration and the Paperclip
environment are restored; host-wide SSH files and current systemd/network
configuration stay in place.

Candidate trees stage on each target filesystem. Writers stop and notes exports
unmount before atomic path replacement. Previous trees remain until previously
running applications resume and HTTP readiness passes. Normal activation/readiness
failure rolls back the previous trees with writers stopped. If new worker
activity prevents safe rollback, both copies remain with a private receipt;
resolve the activity before recovery. An uncatchable process kill may also
require receipt-based manual recovery. This site's original VM is stopped before
any restored bot starts.

Receipts, verified extraction and diagnostics stay private under a timestamped
`restore-*` directory in `backup_dir`. Source tests exercise real tar/GPG and
file activation with mocked supervision; a full live restore with Discord
acceptance remains unrun (see [STATUS.md](STATUS.md)).

## Updating and removing

Every plain `make` also reconciles bot integration, before any terminal-only
setup wizard. Existing native profiles keep their personas, logins, model
choices and custom settings. Deployment-owned routing, bundled bridge skills,
required terminal credential passthrough and scoped project tables are repaired.
A second unchanged run does not rotate keys or end conversations.

To retire a project, explicitly map its slug to a currently declared project
in local `config/projects.yaml`:

```yaml
projects:
  homelab: {name: Homelab}
project_replacements:
  old-lab: homelab
```

`make` migrates existing bot grants through that mapping, then archives the old
project if it has no open tickets. It never deletes its tickets or vault folder.
Without a declared replacement, existing grants are preserved. The generated
skill advertises only the projects that the bot's actual key permits.

The first reconciliation, and subsequent changes to a profile's persona,
configuration, managed skills or qualified pins, end its old chat contexts
using the native Hermes API. The transcripts remain in history; the next chat
loads the current instructions. Paperclip run sessions are preserved. If Hermes
has active work, let it finish and rerun `make`. A malformed private context
receipt or an unreachable API fails the step rather than claiming freshness.
An errored or paused team member is reported; inspect its native Paperclip run
before retrying work, since it may already have changed data.

Versions and hashes live in [versions.yaml](../versions.yaml). Qualify updates
against native adapters, subscription inference and database recovery before
adopting new pins. The Hermes role refuses an unexpected source revision;
native UI updates require explicit adoption. Obsidian auto-update is disabled
in initial native preferences; verify the actual installed application version.
The Paperclip lock includes two qualified dependency overrides; the application
release stays pinned. Hermes dependency manifests are qualified as described in
[the dependency README](../deploy/roles/hermes/files/dependencies/README.md).

Paperclip release directories include a digest of Node, the package declaration
and dependency lock. A candidate installs and verifies beside the active release;
its service switches paths only after installation succeeds. The run's final
cleanup then removes earlier release directories; to return to an earlier
runtime, check out its matching source snapshot and rerun `make`, which installs
that release again. Persistent database state is separate: switching binaries alone
is **not** a database rollback. Before a deployed update, take a consistent backup,
keep its matching source snapshot, and verify restore with the previous runtime.
Restore the database/files/keys together before activating an older runtime when
migrations are not backward compatible. Never copy a snapshot over running state.

For component qualification, install the candidate's locked npm dependencies in
a disposable directory and run:

```bash
rtk proxy .venv/bin/python tools/check_paperclip.py --runtime /absolute/candidate-runtime --node /absolute/pinned-node --recovery
```

Optionally add `--previous-runtime /absolute/previous-runtime` to exercise a
native previous → candidate → restored previous transition. The fixture creates
temporary loopback servers, a private owner, company, unassigned task and note;
stops the database before encryption; authenticates and compares the extraction;
then checks restored login, task/note state, key retention and a new task update.
It never imports subscription credentials or submits inference. Successful
fixtures are removed; failure diagnostics remain private under `/tmp`.
This tests native coordinator persistence, not execution by a restored worker,
Discord delivery, guest recovery or compatibility of arbitrary future releases.

Run the same checker with `--project-retirement` instead of `--recovery` to
exercise the deployer's archive request against the actual candidate API. It
uses disposable projects and a closed ticket, checks that history survives and
the replacement stays active, then repeats retirement to verify convergence.
This catches API validation differences that a mocked owner client cannot.

Host distribution upgrades are separate maintenance: take a backup first, keep
console access, and reapply `make` afterwards. The installer reports an
out-of-baseline release as a warning, not a blocker.

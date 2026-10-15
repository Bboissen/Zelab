# Operations

For installation and Discord configuration, use [SETUP.md](SETUP.md); for
what has been verified, [STATUS.md](STATUS.md). Run commands from the
repository root as the configured operator.

## Routine commands

Run from the checkout with `rtk proxy`. Only these seven Make commands remain:

| Command | Role |
| --- | --- |
| `make` | Install/reapply/resume; keep state and refresh/open the guide |
| `make clean` | Reset local deployment choices and remove generated controller/bytecode; keep installed data |
| `make fclean` | Preview, confirm and remove this site's installation/application data, then reset local choices/generated files |
| `make re` | Complete fclean, then install again; reset finishes before install even under parallel Make |
| `make help` | Print the seven commands |
| `make backup` | Create/verify an encrypted application backup and resume previously running services |
| `make restore` | Select, authenticate, confirm and restore a backup to a compatible installed site |

`make` includes readiness checks, resumable provisioning, private logs and the
guide. Source checks are developer tooling: `rtk proxy python3 tools/check.py`.

A successful `make` ends by deleting what the current pins no longer use:
consumed downloads and the Hermes build tree in `/var/cache/zelab`, superseded
runtimes in `/opt/zelab-tools`, earlier Paperclip releases, npm caches, unused
earlier Obsidian images, guest package files and superseded Hermes installers,
and all but the five newest private install logs. The guest then returns freed
blocks to the host so `disk.qcow2` shrinks. Shared tools stay while another Zelab
guest on the host may use them. A failed run removes nothing, so it can resume.

`clean` removes `.venv` and Python caches and clears repo choices from the selected
local, untracked site file: harness/model, VM resource limits and install boost,
host reserve preferences, optional apps, profile counts/audiences and Discord IDs.
Host connections, account identities, ports and installed resource paths remain
so subsequent commands can find the same installed site. `.env` contains external
path selectors and is retained. Public defaults/examples, tracked input files
and input files outside this checkout are never reset. Ambiguous YAML
merges/anchors/flow mappings are refused before cleanup; use plain top-level keys.

`fclean`/`re` additionally delete Zelab's VM,
profiles/logins, tasks, notes, Obsidian state, owned accounts/units/routes and
project-installed software/cache. Host/path inputs, personal Codex login, backups, recovery
key, private logs, host SSH/Tailscale and unrelated services are preserved.
A later `make` rebuilds the controller.

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

## Inputs and ownership

Setup offers `worker_harness: codex`, `claude` or `none` (skip), saving the
choice in the site file. Every interactive run asks again with the saved choice
as its default. Noninteractive setup retains the saved choice or defaults to skip;
the removed `both` stops unattended runs until a terminal run replaces it. Only the
selected pinned CLI is installed during Host workers.

A harness creates one Paperclip agent, **Operations**: its adapter, installed
command (`/usr/local/bin/zelab-codex` or `zelab-claude`), `Zelab Operations host`
SSH environment, adapter environment (Claude: `CLAUDE_CONFIG_DIR`,
`DISABLE_UPDATES=1`, `DISABLE_AUTOUPDATER=1`), permissions and wake on demand are
set, and it is active. Only the model is left to you; `worker_model` presets one,
and otherwise the CLI default applies until you choose. The install also queues
**Prove Operations host access and sudo** in Backlog, assigned to Operations.
Backlog never wakes an agent; moving the task to Todo starts it.

Skip creates no worker, worker account, sudo grant or Discord task bridge; the
Operations project and the Hermes Operations remote agent remain. Choose a harness
later and rerun `rtk proxy make` to add the worker. Existing worker adapters,
models and logins remain as configured in Paperclip; a reapply refuses a worker
moved off its SSH environment, because the adapter would then run as Paperclip's
own account. Selecting `none` does not uninstall an existing CLI or remove a
previously configured worker. Sites from the earlier two-worker layout keep
their Coding agent until `make re`.

Claude Code login belongs to the Operations account, under its `.claude`
directory; worker homes are backed up and deleted by a full clean. Personal
operator logins remain outside that state. Claude worker auto-updates are
disabled through native environment settings; version changes require a new
[qualification](HARNESS_QUALIFICATION.md).

`config/defaults.yaml` holds public defaults. `config/site.yaml` (generated once
by `make`, never overwritten) holds only host-specific values and any
overrides; `discord_owner_id`/`discord_channel_id` may stay empty until the first
Discord test (nobody is admitted meanwhile). Optional `.env` overrides:

| Variable | YAML field |
| --- | --- |
| `ZELAB_PRIVATE_DIR` | `private_dir` |
| `ZELAB_DEPLOY_KEY` | `deploy_key` |
| `ZELAB_KNOWN_HOSTS` | `guest_known_hosts` |
| `ZELAB_CODEX_AUTH_FILE` | `worker_codex_auth_source` |

Use absolute paths and private file permissions. No shell expansion or inline
tokens. Unknown/duplicate `.env` keys and component port collisions are rejected.
Use `SITE_CONFIG=... ENV_FILE=...` with Make to select another input pair;
explicitly choose an absent/empty override file for a separate guest test.

| State | Default location |
| --- | --- |
| VM disk and cloud-init | `/var/lib/zelab-vm/{data,bootstrap}` |
| Hermes native state inside VM | `/var/lib/zelab/hermes/home` |
| Paperclip database, uploads, agent state | `/var/lib/zelab-paperclip` |
| Host worker home | `/var/lib/zelab-ops` |
| Durable notes | `/var/lib/zelab-vault/{Private,Shared}` |
| Obsidian desktop configuration | `/var/lib/zelab-obsidian` |
| Deployment keys, Hermes password hash and resource IDs | Configured `private_dir` |
| Obsidian native startup password | `/etc/zelab-obsidian/password` (root-only) |

Hermes native memory stays in Hermes. Paperclip task state stays in Paperclip.
The vault holds durable notes and runbooks, with task references where useful.
Operations can access Private and Shared; Research gets Shared only.
Reserves have no notes mount until selected in `notes_shared_profiles`.

Administration backends bind to localhost; Tailscale Serve supplies private HTTPS.
No LAN proxy or public tunnel is required. Passwords remain required locally.

Fresh `make` asks for three UI passwords with hidden terminal input and
confirmation. Keep them in your password manager. Hermes retains a native
scrypt hash; Paperclip owner login is staged only for the provisioning run in
a private RAM directory, so future applies request its current password.
Obsidian retains the protected startup file its native container requires.
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

The archive includes VM/backing chain, coordinator state/keys, worker homes,
vault, Obsidian state, deployment credentials, native host configuration and
source/runtime pins. A database dump or `.env` alone cannot restore the site.

Interactive `restore` lists backups and asks which to use. Noninteractive use
requires `RESTORE_ARCHIVE`. Before changing live state it authenticates the
complete archive, validates its member/link graph, extracts privately and compares
files/metadata. The VM path, full state, runtime pins, service ownership and
account UID/GID must match a compatible installed site. After `fclean`, run
`make` first: state restoration does not provision missing software/accounts.
Different-host/version migration is separate.

Restore previews exact application paths and requires the displayed site identity
(`CONFIRM=<site>` noninteractively). Current inputs, personal Codex login,
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

Host distribution upgrades are separate maintenance: take a backup first, keep
console access, and reapply `make` afterwards. The installer reports an
out-of-baseline release as a warning, not a blocker.

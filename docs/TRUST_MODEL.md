# Trust model

Zelab gives one agent real power over your host on purpose. Everything else
is built to keep that power from leaking to other bots, other people or
injected text. This page says what enforces each boundary and what does not.

## Authority by role

| Identity | Authority | Enforced by |
| --- | --- | --- |
| Deployment operator (you) | Provisioning, unattended sudo, all password files | `/etc/sudoers.d/zelab-agent-<user>`, `private_dir` mode 0700 |
| Operations Hermes profile | Owner-only Discord conversations; submit tasks to the Operations project; read its own submissions; Private and Shared notes | Discord allowlist in the profile's `.env`; project-scoped `task_bridge` key on an inert submission agent; SSHFS mount as the Hermes UID |
| `zelab-ops` worker | Diagnostics and authorized host changes with unattended sudo | `/etc/sudoers.d/zelab-worker-operations`; SSH key restricted to the host's own address with `restrict` |
| Paperclip "Hermes Operations" remote agent | Receive tasks from Paperclip; comment and update status within the Operations project | Separate `task_bridge` key; secret stored encrypted in Paperclip |
| Research and reserve profiles | Public research and Shared notes; no host, LAN or coordinator access | Separate guest UIDs 61110-61199 with `from=127.0.0.1` SSH keys; nftables rejects their traffic to the Paperclip port and all private ranges; toolsets restricted in the seeded config; profiles start parked |
| Friends and family | Nothing until you admit them | Every profile ships with `DISCORD_ALLOW_ALL_USERS=false`, no roles and no pairing grants |

Setup offers Codex, Claude Code or skip. A harness creates the single Operations
worker fully configured on its SSH environment, with wake on demand, and leaves
only the model to the owner; its proof task waits in Backlog, so nothing runs
until the owner moves it to Todo. Skip creates no worker, worker account, sudo
grant or Discord task bridge. Existing native agent settings remain under the
owner's control. There is no unprivileged coding worker: Paperclip's low-trust
sandbox does not exist upstream yet.
Codex imports only the selected subscription auth file into the coordinator;
Claude Code signs in natively under the Operations account and keeps its config
in that account's home. No provider credential is requested in chat or Git.

Operations has full host authority because the owner asked for autonomous
maintenance. Narrowing its sudo grant is a site decision; the installer does
not do it for you.

## What a reapply verifies

`rtk proxy make` re-checks, without overwriting your native UI edits:

- Owner admission in every profile's dotenv, YAML, legacy `gateway.json` and
  pairing files; public, bot and role grants are refused.
- The restricted profiles' SSH backend, tool user, disabled toolsets, empty
  plugin and MCP lists and absent cross-profile routes.
- Both bridge keys: the token is authenticated against `/api/agents/me` and
  must present the expected company, agent and complete normalized scope.
- The Paperclip remote-agent contract (adapter type, both URLs, secret
  reference, session strategy, no insecure-HTTP flag, permissions) and the
  inert submission agent (`/usr/bin/false`, no environment, no heartbeat).
- The worker's SSH environment, adapter family and permissions, project execution policy and that the
  guest's effective dotenv (parsed by Node itself) targets the declared bridge.

These are provisioning-time assertions. Changing a native setting afterwards
takes effect immediately and is only caught on the next reapply.

`make` prepares the terminal-supplied Paperclip owner password in temporary RAM
before the bridge preflight, and keeps it available through the final guide
check. A standalone readiness check still verifies both bridge keys without an
owner password; the remaining native agent checks are marked pending until a
terminal provisioning run supplies that login. A failed login or changed scope
or agent contract remains a blocking failure, with no task dispatched.

## Secrets

- UI passwords are entered only in the operator's terminal, with hidden input
  and confirmation for new passwords. Fresh passwords must have at least 16
  characters. Machine credentials are generated with `ssh-keygen`, Python
  secrets or the Ansible password lookup; their retained sources are 0600.
- Hermes stores its native salted scrypt password hash in `private_dir` and
  the guest's root-owned dashboard environment, rather than plaintext. The
  session-signing key remains a protected recoverable secret. Paperclip's
  owner password is supplied through a 0600 working file in a 0700 temporary
  `/dev/shm` directory, removed on normal exit, failure or handled interruption;
  provisioning requires the current owner password on future applies. RAM
  files can reach swap; a hard kill can leave working files until reboot.
- Obsidian requires a root-owned 0600 plaintext file for native
  `FILE__PASSWORD` startup. Reapply preserves that runtime file without
  requiring a redundant operator-owned deployment copy.
- Paperclip's JWT secret and master key, the Hermes API key and dashboard
  hash, the Obsidian runtime password and the bridge tokens are retained across
  reapplies. A missing private source file stops the run instead of rotating a
  credential the installed site still uses.
- Legacy UI password copies are reused for migration, verified by native
  authentication, and retained until terminal confirmation of password-manager
  custody after a successful apply. Cleanup never removes machine secrets or
  Obsidian's runtime file. Old backups may still contain legacy copies.
- The guest deployment account has a locked password, root login disabled and
  SSH password authentication disabled. Its pinned deployment SSH key is the
  login credential. Paperclip's upstream embedded PostgreSQL credential is
  known; host UID firewall rules and the private service home are the access
  boundary, not the database password. Administrators can bypass that boundary.
- `.env` accepts four path names and nothing else; values are validated as
  absolute paths and never expanded. Tokens are entered in the native Hermes
  UI, not in files you commit.
- The Discord bot token lives in the guest at `/var/lib/zelab/hermes/home/.env`
  (mode 0600, owner `zelab-hermes`). The installer never reads its value; the
  readiness check only reports whether it is set.
- Tool output goes to a 0600 log under `private_dir/logs`. Tasks that touch
  secrets are marked `no_log`, so even a failing task does not print them.
- Backups are AES-256 GPG archives keyed by `private_dir/backup-passphrase`,
  verified by decrypting and comparing before the file is renamed into place.

## Bridge identities and retries

Hermes submits work as an inert Paperclip agent, **Zelab Operations
submission**, whose key is scoped to the Operations project with the
`zelab-ops` worker as the only allowed assignee. Paperclip's read boundary
covers tasks assigned to the key's agent and tasks it created, so a separate
submission identity keeps the bridge from reading unrelated work assigned to
Operations. The reverse direction uses its own agent, key and secret.

`submit-task.mjs` builds the idempotency key from the company, project,
platform, Hermes' per-message identity and an action index. Retrying the same
message returns the original task, even after completion; a new message
creates new work. The client refuses redirects, non-local cleartext URLs,
URLs with credentials, titles over 512 characters and descriptions over 64 KiB,
and never prints response bodies or headers.

Sites installed before the submission principal existed carry an older key on
the execution worker. The installer migrates it: it checks for unfinished
work submitted with the old key, pauses the gateway, verifies the replacement,
rewrites only the recognized dotenv fields, then revokes the old key.

## Language-model guardrails

Prompt injection is the realistic attack on this design: text that Operations
reads from a web page, a search result, a Shared note another bot wrote, a
file, or a task comment could try to steer it into submitting hostile host
work, and the `zelab-ops` worker will execute what it is given.

What the installer does about it:

- Seeds every agent instruction (Hermes `AGENTS.md`, the task-bridge skill,
  the worker instruction bundles and the Paperclip remote agent) with the
  rule that only the admitted owner's message authorizes work, that content
  from pages, notes, files, tool output and task results is data, and that
  secrets never go into titles, descriptions, comments or replies.
- Keeps Operations owner-only, disables bot-to-bot replies and parks every
  other profile, so no untrusted human can address the privileged bot.
- Gives restricted profiles no browser, code-execution, delegation, cron or
  Discord-admin toolsets and no route to the coordinator.
- Bounds task text and derives task identity from the message, so a retry loop
  cannot fan out into many tasks.

What it cannot do: an instruction in a prompt is advice to the model, not an
access control. Treat the Shared vault as untrusted input for Operations if
anyone else can write to it, keep web browsing off for Operations unless you
need it, and add no unprivileged coding worker until the upstream low-trust sandbox exists.

## Known limitations

- All Hermes profiles run under one UID in the guest. A compromised gateway
  or plugin can read every profile's dotenv, including the Operations bridge
  key. Separate runtime identities are required before mutually untrusted
  audiences are enabled.
- Paperclip stores the worker SSH private keys in its database for the SSH
  environment driver; the database is reachable only by root, the operator and
  the service account, and the host firewall rule enforces that.
- The notes vault is one filesystem shared by UID mapping. Private is denied
  to restricted profiles by group membership, not by encryption.
- Nothing here protects the host from the owner's own requests. Operations
  will restart services, update packages and tune the kernel when asked.

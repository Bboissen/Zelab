# Status

Zelab is experimental. This page separates what has been exercised from what
has not, so nobody mistakes a passing installer for a verified system.

## Verified on the reference site

- Fresh installation from pinned downloads through plain `make`, a zero-change
  second apply, and resumption after an interrupted install.
- Owner sign-in to Hermes, Paperclip and Obsidian over Tailscale Serve;
  anonymous requests are refused on every route; former LAN listeners are gone.
- A Discord request from the owner created a Paperclip task, the `zelab-ops`
  worker executed it on the host with sudo, and the result returned to the
  originating channel with host identity and timestamp.
- Operations restarted an owned transient service through unattended sudo.
- Restricted tool accounts cannot read owner secrets, Private notes, the
  coordinator port, the host SSH listener or the tailnet; public HTTPS works.
- Shared notes round-trip between a host worker, Hermes and Obsidian, including
  rename, attachment and revocation of a temporary audience grant.
- Encrypted backup, authenticated isolated extraction, and restore activation
  with rollback on fixture sites; recovered Paperclip, Hermes login and notes.
- Native bridge isolation on a disposable Paperclip instance: unrelated task
  reads denied, retries reuse the task before and after completion.
- Dependency audits: zero known advisories in the qualified Hermes inventory
  and the Paperclip npm lock at the pinned versions.

## Not yet verified

- Live whole-site restore and execution of a recovered worker task.
- A second real bot identity, specialist allocation from the pool, and denial
  tests with a non-owner Discord user.
- MacBook and iPhone device checks beyond browser login; IPv6 denial.
- Mixed-workload resource measurement and host reboot ordering.
- The Coding worker; it stays paused until the upstream low-trust sandbox is
  supported.
- Reapplying the hardened bridge (inert submission principal, event-derived
  idempotency) on a site installed before that change; the migration path is
  implemented and unit-tested, not yet exercised live.
- Authenticated Discord → host task execution using the optional harness setup.
  Fresh installation with both harnesses is qualified below. The earlier
  reference-site worker flow used Codex;
  Claude Code binary verification does not establish end-to-end task execution.

## Credential and harness reapply: 2026-10-13

- PASS: plain `make` completed on the reference site with `worker_harness: both`;
  checksum-pinned Codex `0.162.0` and Claude Code `2.1.287` are installed. The
  existing native Codex worker settings and paused Coding account were retained.
- PASS: Hermes migrated the existing password to its native scrypt hash, with
  no plaintext password in the guest's root-owned 0600 dashboard environment.
  Native login established a session that read protected profile state;
  anonymous profile access remains denied. No login password was rotated.
- PASS: Obsidian retained its root-owned 0600 native startup password; both
  anonymous denial and authenticated access were checked during reapply.
- PASS: guest deployment/root passwords and host VM/coordinator/worker passwords
  are locked. Effective guest SSH password and keyboard-interactive authentication
  are disabled. Live PostgreSQL connections from `nobody` and `zelab-code` are denied.
- PASS: native Paperclip Test Environment for Operations, followed by task `ZEL-1`
  (`7ffc6b3c-c070-4146-be22-002b31f7ff88`). Its native Codex run
  `4145492f-6253-4c72-9c73-dc7da2f51e19` succeeded; command-event output matched
  the actual host, worker account, UID and workspace, and the task finished `done`.
  This was a scoped API → Paperclip → SSH → Codex test; Discord was not exercised.
- PASS: full source suite (141 deployment tests, 34 recovery tests, playbook
  syntax and public-source checks), plus 11 focused credential tests after the
  additional cleanup/terminal regressions. A real RTK PTY test confirmed hidden
  input. No UI password values were found in this session's installer logs.
- PASS: temporary RAM password working files were removed when `make` exited.
  Legacy deployment copies remain pending the owner's terminal confirmation
  of password-manager custody; this agent did not assume they were saved.
- NOT RUN: live fresh-site password entry, live cleanup after vault custody,
  Claude Code subscription sign-in/task execution, browser/device acceptance,
  Discord delivery and whole-site restore of this migrated state.
- Host limitation: Ubuntu 25.04 is outside the supported deployment baseline
  and reached end of life on 2026-01-15; the credential checks do not establish
  OS security maintenance. See the [Ubuntu announcement](https://lists.ubuntu.com/archives/ubuntu-announce/2026-January/000320.html).

Follow-up prompt correction: instructions and hidden prompts now share the same
flushed terminal stream. A matching confirmation returns immediately, without
the redundant password-manager/Enter prompt. PASS: 12 focused credential tests,
including real RTK terminal ordering, hidden input and advancing from Hermes to
Paperclip without a third prompt. Host/guest service changes: none. Live fresh
installation with this prompt correction: NOT RUN.

## Fresh-site worker creation failure: 2026-10-13

- FAIL: the fresh install with `worker_harness: both` stopped creating Operations
  at 21:19:24 UTC. The Paperclip journal records HTTP 422; Ansible's `no_log`
  censored the task result, so the installer incorrectly referred to a private
  log that also lacked the cause.
- Cause: the installer attached an SSH environment to its inert `process`
  placeholder. The installed pinned Paperclip package permits only local
  environments for that adapter; Codex and Claude Code permit SSH.
- Repository fix: inert workers have no default execution environment;
  selecting a single harness attaches the declared SSH environment during
  activation. Agent-creation failures report a numeric HTTP status with fixed
  guidance, retaining credential and response-body protection. The failure
  summary uses that diagnostic and retains the original failed step.
- PASS: disposable API fixtures cover fresh placeholders, reapply, harness
  activation, Coding pause, retained owner settings and censored HTTP failures;
  failure-summary regressions cover safe rescue output and censored logs.
- PASS: `rtk proxy python3 tools/check.py`: 149 deployment tests, 34 recovery
  tests, playbook syntax, whitespace and public-source checks.
- Host/guest changes by this fix: none. The failed install's cleanup log records
  restoration to 1 vCPU, 2048 MiB RAM and 50% CPU quota.
- NOT RUN: live resumed installation and authenticated worker task execution
  after this correction. Resume with `rtk proxy make` in the owner's terminal;
  enter the current Paperclip owner password there.

Harness prompt follow-up: interactive runs now offer the selection even when
`worker_harness` exists, using its saved value as the default. Unattended runs
visibly retain the saved choice, or skip when none exists. Only the selected
scalar is updated; unrelated inputs, comments and file permissions are retained.
PASS: nine configuration tests and a real RTK PTY exercise starting from `both`,
showing the prompt and saving `claude`. Full source checks: 151 deployment tests,
34 recovery tests, playbook syntax and public-source validation. Host/guest
changes: none. Live install with the new prompt: NOT RUN. At this point `fclean`
retained all site inputs; the cleanup correction below supersedes that behavior.

Cleanup correction: `clean` and successful `fclean` now clear local harness/model,
resource, optional app and profile choices from the selected untracked site file.
Host connections and installed resource identities/locations remain, as do `.env`
path selectors. External and tracked input files are untouched. A failed or
cancelled full reset keeps all choices.

- PASS: 154 deployment tests, 34 recovery tests, playbook syntax, whitespace and
  public-source checks; ten focused cleanup tests also passed after the final
  ambiguous-YAML regression cases were added.
- PASS: real repo-only `make clean` cleared `worker_harness` and the two Discord
  input fields, removed the controller/caches, and preserved source files,
  retained host/path settings and `.env`. VM/Paperclip active states matched
  before and after; this is preservation evidence, not service acceptance.
- Host/guest service or network changes: none. Live destructive `fclean` with
  this correction and subsequent installation: NOT RUN.

## Fresh reset with both harnesses: 2026-10-14

The reported guide failure was an installer credential-lifecycle defect. The
bridge checker still required the retired persistent owner-password file after
provisioning had switched to temporary RAM files. `make` now prepares that input
before bridge preflight and supplies the same directory through guide generation.
Standalone checks distinguish a pending owner login from actual key/scope drift;
explicit missing inputs, failed authentication and changed contracts still fail.

Testing from a full reset under `umask 077` also exposed two artifact-permission
defects: the host-built dashboard archive was unreadable by the controller, and
embedded PostgreSQL tried to chmod its binaries inside Paperclip's read-only
service filesystem. Provisioning now declares the public archive mode `0644`
and native database executable modes `0755` before service startup. Upstream
code and `ProtectSystem=strict` remain unchanged.

- PASS: `rtk proxy python3 tools/check.py`: 166 deployment tests, 34 recovery
  tests, playbook syntax, whitespace and public-source checks.
- PASS: live `make fclean CONFIRM=homelab:zelab`, followed by plain `make` with
  **3 — Both** selected in its real terminal menu. All three generated test UI
  passwords went through hidden input and confirmation; no legacy deployment
  password copies existed. The uninterrupted fresh run finished all five stages,
  generated the guide, and exited zero in 997 seconds.
- PASS: a second plain `make` retained **Both**, requested only the current
  Paperclip owner password, completed the guide and exited zero in 558 seconds.
  Authenticated bridge checks passed before deployment and after completion.
  Fingerprints of all ten checked machine-credential/resource-ID files were
  unchanged. Six profile audience-seeding commands report changes while Discord
  IDs remain unset; all other deployment stages reported no changes.
- PASS: authenticated native bridge inspection after installation; both fresh
  workers are paused native process placeholders with no execution environment.
  Codex `0.162.0` and Claude Code `2.1.287` both execute as `zelab-ops`.
- PASS: temporary installer password files were removed; test password values
  were absent from the installer log. Recovery copies of the generated test UI
  passwords are operator-owned `0600` files in the private acceptance directory.
- PASS: cleanup preserved 29 unrelated containers, one other Serve route and
  52 unrelated accounts. The pre-test encrypted backup was verified by decrypted
  comparison and retained as `zelab-applications-20261013T222004.610702Z.tar.gpg`.
- Repository changes: credential handoff and safe bridge diagnostics, explicit
  artifact permissions, regression tests and this qualification criterion.
- Host changes: the requested reset recreated Paperclip and its database,
  worker accounts and both pinned CLIs, notes/SFTP, Obsidian and owned UI routes.
- Guest changes: a fresh pinned Hermes guest and profiles; installation resources
  returned to 1 vCPU, 2048 MiB RAM and 50% CPU quota before final verification.
- Fresh-install evidence: private log
  `install-20261013T230215.809349Z.log`; reapply:
  `install-20261013T231900.607598Z.log`. Sanitized acceptance results are under
  `private_dir/acceptance-20261014/`.
- NOT RUN: authenticated Codex/Claude task execution, Discord delivery,
  browser/device acceptance and restore of this newly generated state.

## Acceptance checklist

Record PASS, FAIL or NOT RUN with versions, timestamp and sanitized evidence.
An HTTP 200 or a parsed configuration is not acceptance.

Installer qualification requires a live `make fclean` → `make` cycle, selecting
**Both** in the harness menu, followed by a second `make`. Both installations
must exit zero with a completed guide and authenticated native bridge checks;
both pinned CLIs must run. Exercise the fresh terminal password flow without
legacy deployment password copies, verify temporary password cleanup, and
record preservation of unrelated resources during `fclean`. Source tests and
reapplying an older installation alone do not satisfy this criterion. Harness
sign-in and task execution remain separate acceptance checks below.

1. **Host diagnostics.** From an admitted owner conversation ask for running
   containers, CPU, memory and disk. Require a Paperclip task, host (not guest)
   measurements comparable with `lscpu`, `free`, `df` and `docker ps`, a reply
   in the same conversation, and a successful follow-up status query.
2. **Authority and denial.** Operations performs a reversible host change
   without an approval stall. A non-owner cannot invoke Operations; a
   restricted profile cannot obtain a bridge key, read owner auth or dispatch
   privileged work, through terminal, file and child-process tools alike.
3. **Identities.** Two profiles reply with separate personality, memory and
   session history; a specialist can be allocated from the pool and retired
   without duplicating a token consumer.
4. **Failure behavior.** A failed worker, unreachable API and quota failure
   produce truthful status; reconnects do not duplicate tasks.
5. **Notes and UIs.** Shared notes round-trip; Private is unavailable to
   shared-only profiles; UIs reject public, LAN and unauthenticated access over
   IPv4 and IPv6.
6. **Recovery.** Service and VM restarts keep identities, memories, tasks,
   secrets and notes; a restore into an installed site completes a real task
   and note flow; a pinned update can be rolled back with its matching backup.

## Open work

- Separate runtime identities per Hermes profile before enabling audiences
  other than the owner.
- Upstream sandbox support for Coding, then its audience denial tests.
- Optional: overlap independent host and guest preparation during install.
- Optional: interactive coding through T3 Code with private pairing.

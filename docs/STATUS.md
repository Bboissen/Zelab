# Status

Zelab is experimental. This page separates what has been exercised from what
has not. An HTTP 200, a parsed configuration or a unit test is not acceptance;
anything not exercised live is NOT RUN.

Versions: Hermes `3637c512` (v0.21.5+9529), Paperclip and its MCP server `2026.1005.0`, Debian 13
`20261001-2618`, Node `26.7.0`, Codex `0.162.0`, Claude Code `2.1.287`.

## Public source review and native commands: 2026-10-16

At 20:44 UTC, GitHub reported the source repository as public and anonymous
browser access succeeded. Public heads, tags and retained pull request heads
were fetched into a fresh, disposable bare repository: Gitleaks scanned all
56 reachable commits with no leaks found. No private checkpoint refs were
advertised. Searches of historical blobs and commit messages found no known
private site identifiers. Reviewed address and ID matches were QEMU defaults,
network ranges, synthetic fixtures, dependency versions or public citations.
The 23 public issue/PR entries and eight issue comments contained no known
private site identifiers either. GitHub account identity, commit attribution
and older command examples remain public metadata/history; this is not an
anonymity guarantee or a rewrite of published history.

Make, deployment subprocesses, recovery fixtures, CI and all operator guides
now invoke native commands directly. Installation no longer copies an extra
command wrapper into the privileged executable path. The README and shared
SVG diagram distinguish encrypted transport from local HTTP, shared guest
credentials, whole-vault access, ticketed host execution and privileged tiers.
They also state that guest root can change the guest's firewall; no root-level
network-denial guarantee has been demonstrated for X1.

Source checks passed: 273 tests, public source checks and playbook parsing,
with only `/usr/bin:/bin` on the subprocess search path. `make help`, SVG XML
parsing and visual inspection passed. The working-tree Gitleaks scan passed.
These are repository checks, not deployment acceptance.

Repository changes are source, documentation, fixtures and CI. Production host
changes: none; guest changes: none. Fresh installation, full live reapply,
chat-to-team delivery, live recovery and root-level network acceptance:
**NOT RUN** for this change.

## Dependency alert applicability review: 2026-10-16

GitHub reported 13 open Dependabot alerts in the universal Hermes `uv.lock`
after the public-source review. They are distinct from personal-data or secret
leaks. The production-inventory CI audits passed, but they do not assess every
optional extra or the local Hermes application itself.

| Alerts | Evidence | Finding |
| --- | --- | --- |
| Three Tornado alerts, including two high severity | `tornado==6.5.8` is reachable only through the optional `python-telegram-bot[webhooks]` dependency; no hard-dependency parent; absent from the qualified 108-package runtime inventory | Outside that qualified selection. Enabling this extra needs a new audit; the universal lock still contains the affected package |
| Nine Hermes alerts, including the high-severity WebSocket DNS rebinding advisory | The editable lock root reports `hermes-agent==0.0.0`. For each advisory with a referenced source fix, GitHub's commit comparison reports that fix as an ancestor of pinned source `3637c512fc3211bdccb61b7140d09ac1018508ba` | Version metadata matches older advisory ranges even though the referenced fixes are included. Ancestry is source evidence, not a replay of every exploit |
| One Hermes Feishu webhook resource-exhaustion alert | [GHSA-pmqc-57g8-c22c](https://github.com/advisories/GHSA-pmqc-57g8-c22c), [upstream issue 29154](https://github.com/NousResearch/hermes-agent/issues/29154); the referenced [fix PR 73406](https://github.com/NousResearch/hermes-agent/pull/73406) remains open and unmerged | **OPEN security finding** for Feishu webhook handling. No fix was adopted or qualified; this review does not accept or resolve it |

The nine ancestry checks covered GHSA-33qv-c5qm-799v, GHSA-xq8w-9jvx-gm3v,
GHSA-mv8x-fg99-32mf, GHSA-cv5c-mh6j-wvp9, GHSA-wm96-9gfh-vvgq,
GHSA-pgp4-xr4j-h5cg, GHSA-238w-f66p-w349, GHSA-99f9-j8r3-p853 and
GHSA-4pqm-j46f-795x. None of the 13 GitHub alerts was dismissed. Source pins,
dependency manifests and deployed settings are unchanged. Live installed-extra
inventory and exploitation tests: **NOT RUN**. Additional owner-installed
plugins and chat platforms are outside the recorded inventory qualification.

## Quiet Discord tool progress: 2026-10-16

Every bot now receives Hermes' native per-platform setting
`display.platforms.discord.tool_progress: false` during installation and
reconciliation. In the pinned `gateway/display_config.py`, this resolves to
explicit `off`, ahead of global and legacy environment settings. The gateway
loads this display configuration for each turn.

Applied to all six live profiles at 20:16–20:18 UTC using Hermes' native
configuration setter. For each profile, its effective gateway config resolved
to `off` even with a synthetic verbose environment fallback. The unmodified
native event dispatcher received three representative tool events and queued
zero tool messages; two normal reply events still reached a local test sink.
Other settings, including skill auto-loading, were retained. The probe sent no
Discord messages and made no model or Paperclip calls.

Repository changes are the installer setting and operations/status docs. Guest
changes are the six native display settings; host changes: none. A real Discord
turn after this setting and a full-site reapply: **NOT RUN**.

## Bot project instructions during setup: 2026-10-16

A live bot had Paperclip's create-ticket tool and a valid scoped key, but no
generated `zelab` skill. Its request omitted `projectId` and Paperclip returned
HTTP 403 (`deny_scope`). Restoring the generated skills and refreshing old chat
contexts through Hermes' native API repaired this. At 19:56 UTC the configured
model, run from its own profile directory with the chat toolsets, read the skill,
created one unassigned backlog verification ticket in its permitted project and
read it back through the MCP tools. That probe explicitly asked it to read the
skill. No Discord message or team job was sent.
An initial CLI probe from the deployment account's private directory could not
start MCP; the corrected working directory passed.

The installer now writes a chat-only skill when adding a bot, and publishes the
selected project IDs and team routing after verifying its task key, before
enabling Paperclip tools or revoking old keys. Setup reconciles each bot and its
chat context before the next bot or team sign-in wizard. Plain `make` still
restores missing skills from the managed key's scope. The generated skill
explicitly requires `projectId` on every create-ticket call.

The subsequent real Discord retry at 19:58 UTC again omitted `projectId` and
received the same 403. Its stored prompt mentioned the skill index but contained
neither the permitted project ID nor the full Zelab workflow; it read the
upstream task-bridge skill and searched the earlier failed chat instead. Writing
the skill alone was insufficient. Reconciliation now also adds `zelab` to
Hermes' native `skills.auto_load`, retaining the owner's other entries. The
pinned `agent/system_prompt.py` injects these complete skill blocks into the
stable prompt for messaging agents with skill tools.

At 20:12 UTC, an unmodified native `AIAgent` used the real configured model,
Discord's managed toolsets, the gateway's native provider resolver and an
explicit profile-home context as the shared gateway does. Its complete prompt
contained the permitted project ID, team lead and create-ticket rule, and it
offered the three Paperclip tools without command tools. Asked only to create
and reread an unassigned backlog test ticket, without a skill-reading directive,
it made one successful create call with `projectId` and one successful read.
The host database confirmed the bot as creator and the selected project. The
probe used a separate session database and sent no Discord message or team job.
The live gateway was restarted; all six profiles received the native auto-load
setting and current generated skills, and one old Discord context was ended
with its transcript retained. A second context pass changed nothing.

Regression coverage exercises setup completion before later wizards, skipped
setup, skill publication before MCP activation, failed writes retaining old
keys, recovery on reapply, omission of projects outside the selected scope, and
retention of other auto-loaded skills on an unchanged second pass.
The full source suite (273 tests, repository and playbook checks) and gitleaks
passed. Repository changes are in the installer and documentation. Guest
changes are generated skills, native settings and chat boundaries; host changes
are the two verification tickets from the probes. Fresh installation, live
task-key rotation with this change, a real Discord retry after auto-loading and
the full Discord-to-team return path: **NOT RUN**.

## Paperclip harness switch Test/Save mismatch: 2026-10-16

Diagnosis at 19:33–19:43 UTC on Paperclip `2026.1005.0`: switching an existing
Claude Code team member to Codex produced HTTP 422 on save. The owner showed
**Codex CLI** selected and a successful Test. The failed save requests omitted
`engine`, including after the owner selected CLI and tested again.

The installed upstream UI's `buildAdapterConfigForTest` merges the saved
adapter configuration with the draft, retaining `engine: cli`.
`buildAgentUpdatePatch` instead retains only adapter-agnostic saved fields
when switching adapters, plus explicitly changed fields; `engine` is omitted
unless changed. The runtime defaults an absent engine to ACP. Exercising the
installed request builder with synthetic configuration reproduced the omission;
explicitly setting the draft engine to `cli` included it in the save payload.
The engine dropdown's change handler writes that explicit draft field, so the
documented workaround changes away from CLI and back before saving.

On the actual team SSH environment, the native Codex adapter test with no
engine returned `adapter_engine_unavailable`: ACP cannot use that remote
environment. With explicit `engine: cli`, the existing company-shared Codex
subscription and the selected model, the native probe returned **PASS**, with
`codex_command_resolvable` and `codex_hello_probe_passed`. This was an
authenticated model response over SSH, without saving agent settings. The
diagnostic wrapper subsequently errored closing its database clients; its
managed credential cleanup had already run and the process exited.

Repository changes document the upstream UI workaround and its validation
limits. Host changes were limited to the native diagnostic probe's temporary
runtime and normal credential handling; guest changes: none. Authenticated UI
save/reload with the workaround, a full ticket after switching, and equivalent
Claude switching acceptance: **NOT RUN**. No upstream code or pins were changed.

## Optional task keys and projects: 2026-10-16

A hired bot without an active managed task key no longer stops plain `make` or
the exit from `make bot`. Reconciliation still refreshes its bundled skill,
adapter, chat tools, terminal credential passthrough and Zelab skill, then
continues to the next bot. Keyless skills explain that the owner must give the
bot a task key before ticketed work. The guide keeps setup pending, with project
setup first when no projects are declared. The public project example declares
`projects: {}`; existing local project inputs are unchanged.

Checked the installed Hermes checkout at `3637c512` and the pinned Paperclip MCP
server `2026.1005.0`. Starting the MCP executable with a synthetic URL and an
empty API key exits 1 with `Missing PAPERCLIP_API_KEY`. Hermes' pinned
`tools/mcp_tool_config.py` preserves unresolved `${VAR}` references; starting
the MCP executable with those literal references and closed stdin exits 0
without stderr, but does not prove usable ticket access. The pinned discovery
code logs failed connections and retries after a cooldown. In isolated native
Hermes discovery under temporary guest homes (19:16 UTC), the empty key returned
no tools and logged a connection failure after three attempts; a second immediate
discovery added no warnings. Unresolved references exposed the three tools
without warnings. The `enabled: false` entry returned no tools or warnings on
both passes. Zelab now uses that native disabled entry without
Paperclip in the chat toolsets for keyless profiles, and enables it after a
verified key is written.

Source regressions cover keyless profiles, a later keyed bot, zero projects,
skip guidance, chat-only skills, MCP activation and retained scope/hire stops.
The full source suite and gitleaks passed. Full-site `make` with five keyless
bots, a real chat turn, and interactive task-key creation followed by the guide
update: **NOT RUN**; the owner password and interactive project choice were not
available to this agent. The runtime probes used synthetic settings, no model,
no chat messages and no production API calls.

## Team harness per member: 2026-10-16

`config/team.yaml` now declares a default `harness` (`claude` or `codex`) with a
model and effort block per harness, and a member may set its own. `make` hires
Claude members as `claude_local` (`engine: cli`, `model`, `effort`) and Codex
members as `codex_local` (`engine: cli`, `model`, `modelReasoningEffort`,
`dangerouslyBypassApprovalsAndSandbox`), each bound to its provider's
company-shared subscription when one is connected, else the hiring owner's
default. `make bot` option 4 signs one harness in at a time and binds that
harness's members; `make` moves members off a broken connection of their own
provider only. Field names were read from the pinned Paperclip source
(`packages/adapters/codex-local`, `claude-local`, `shared/src/ai-connections.ts`
at `467125fa`): the `openai` subscription method lists `codex_local` as its only
adapter, as `anthropic` does `claude_local`.

Unit tests cover the file (defaults, per-member override, rejected keys), hiring
a Codex lead, binding members of both harnesses and leaving changed harnesses or
explicit connections alone. The host is fresh (nothing installed), so hiring,
both sign-ins and a Codex run over SSH: **NOT RUN** (T1, T4).

## Cleanup coverage: 2026-10-16

`clean` now covers checkout builds/exports and test/lint/coverage caches as well
as the controller and Python caches. `fclean` includes receipt-bound disposable
scratch directories and identified legacy qualification build/download/runtime/VM
outputs. It stops only transient qualification QEMU/Paperclip units whose
disk or PostgreSQL runtime/data match those outputs, then refuses other active
artifact users before stopping the live site.
Reports, private logs and Git recovery bundles outside disposable outputs stay.

Focused regressions passed for checkout/path receipts, other checkouts, renamed
and symlinked directories, tracked files, mounts, legacy evidence preservation,
QEMU disk/cgroup identity and a real open file blocking cleanup until closed.
Read-only host discovery identified the two legacy qualification workspaces and
the running qualification VM and scratch Paperclip user unit. Actual production
deletion, qualification service shutdown through `fclean`, clean reinstall and
application E2E: **NOT RUN**.

## Chat without commands: 2026-10-16

In the live Discord, a bot asked chat members to approve a Python heredoc it wrote
to read Paperclip: chat sessions had the terminal, and Hermes' default `smart`
approval escalated the command to Allow/Deny buttons. Chat now runs no commands.
Each chat platform `make bot` sets up gets Hermes' native `platform_toolsets` without
terminal, code, file, browser, cron, delegation or kanban tools, plus Paperclip's
official MCP server (`@paperclipai/mcp-server` 2026.1005.0, the Paperclip release)
limited to creating and reading tickets. From chat the bot files the job for the
team lead and a reply ticket for itself that waits on the job; Paperclip wakes it
once when the job is done, and that run answers the chat in plain words and closes
the ticket. This replaces the earlier handoff, where the bot's own run created the
child and recorded the blocker with curl.

Qualified without production state, inference or chat:

- The MCP lock: 98 registry packages with integrity hashes, no install scripts;
  `npm audit --omit=dev` found 0 vulnerabilities.
- `tools/check_paperclip.py --chat-handoff --mcp-runtime <dir>` against the pinned
  Paperclip runtime, in disposable authenticated private state, through the real MCP
  server started without a run ID (as Hermes starts it): the task key filed the job
  for the lead, a `blocked` reply ticket waiting on it and an unassigned delivery
  report; it read the job; company-wide listing and ticket updates outside a run were
  refused; no bot run started. Finishing the job woke the bot once
  (`issue_blockers_resolved`); the run read the result through the MCP server, the
  generated `update-status` command closed the ticket, and no disposition repair
  followed.
- Pinned Hermes built from the qualified manifests with native `pm.build_env` in a
  disposable host directory, separate `HERMES_HOME`, against that Paperclip and a stub
  model: native resolution gave Discord and Telegram exactly the chat list and
  `api_server` its terminal; a `hermes chat` turn configured like a chat platform was
  offered no terminal, code or file tool and the three Paperclip tools, filed the job
  and the waiting reply ticket, read the job, and was refused a `terminal` call
  (`Tool 'terminal' does not exist`).
- Found on the way: closing a ticket inside a run needs the run ID, which the
  long-lived MCP server cannot send, so the run closes with the bundled helper; and
  Paperclip refuses `blocked` without a blocker and `in_review` without a review
  path, so a failed delivery becomes an unassigned ticket instead.

Production reapply, a live Discord turn, a real model following the new skill and
the reply run's `hermes send`: **NOT RUN**.

## Final v1.1 dependency qualification: 2026-10-16

MCP and MCP types 2.2.0 replace 2.0.0 in the qualified dependency manifests;
all other locked package versions and the upstream source pins stay unchanged.
Native PM built the `all` + `ddgs` runtime in disposable guest state, and the
regenerated 108-package inventory passed `pip-audit` 2.10.1. A separate native
dev environment passed 202 upstream MCP/OAuth tests. Hermes discovered and
called a real synthetic tool over both stdio and Streamable HTTP. No production
credentials, inference or live bot configuration were used. The production
Paperclip npm lock passed its moderate audit. See the
[dependency qualification](../deploy/roles/hermes/files/dependencies/README.md).

Reapply now recognizes the previous qualified metadata while continuing to
reject unknown local edits. Bot context freshness also covers dependency-only
upgrades. Production dependency installation and Discord acceptance: **NOT RUN**.

## v1.1 qualification: 2026-10-15

Run in a disposable guest with the current pins (2 vCPU / 4 GiB / 20 GiB, Hermes
installed like `deploy/roles/hermes`) and a scratch Paperclip from the pinned lock,
with a stub OpenAI-compatible model on the host that echoes its model name and
the profile's SOUL marker. The live site was not touched.

| # | Question | Result |
| --- | --- | --- |
| 1 | Can Paperclip's Hermes adapter address one profile of a shared gateway? | **PASS.** Hermes multiplexes profiles by default: the default profile's API listener serves every profile at `/p/<profile>/…`, each authenticated by that profile's own `API_SERVER_KEY` (other profiles' keys and the default key get 401; an unknown profile 404). Paperclip's `hermes_gateway` adapter has no profile field, and `apiBaseUrl …/p/<profile>` works unchanged. Two hired agents' tickets (QUA-1, QUA-2) were answered by their own profile: `model=stub-alpha soul=SOUL-MARKER-alpha` and `…beta…`. |
| 2 | Does each profile keep its own subscription login? | **Source: yes, with one caveat; live: NOT RUN** (moved to validation on the real install, owner's choice). Logins are stored in the profile's `auth.json`; a profile with no entry falls back to the default profile's and refreshes it there, so the default profile stays without a login. |
| 3 | Does one gateway run several Discord bots at once? | **Source: yes; live: NOT RUN** (moved to validation). One adapter per profile token; a duplicated token is refused; token changes are picked up on rescan. |
| 4 | How is a Hermes command run for one profile? | `hermes -p <profile> …` (sets `HERMES_HOME` to the profile). `hermes -p gamma config set` changed only gamma's `config.yaml`. `setup model` refuses without a terminal. `profile create --description`; `profile export` omits `auth.json` and `.env`; `profile delete` keeps no copy. |
| 5 | What do 5 idle profiles cost? | **PASS.** One gateway process, 296 MB RSS; guest 720 MiB used of 3922, load 0.3, 3.2 GB disk. Hermes' pinned agent-browser and Chromium need Debian libraries; two headless tabs raised used memory to 971 MiB (2950 MiB still available). |

Also checked: Paperclip's bundled task-bridge helper, copied into a profile by
`make bot` and run as that profile with its key, created QUA-3 in the key's
project; `make bot`'s add, task key, skill refresh and remove ran against the
disposable guest and scratch Paperclip (wizards excluded).

## v1.1 acceptance

Host-agent harness switching (2026-10-16): the checksum-verified pinned
Paperclip UI retains the old AI binding when the adapter changes. Choosing the
matching shared connection replaces its provider and grant together. Reapply
regressions cover retaining native harness, model and connection selections,
including responsible-user bindings, and completing explicit Claude sign-in
without overwriting a changed harness or connection. Authenticated live UI
switch/save and inference after switching: **NOT RUN**.

Bot reconciliation regression coverage (2026-10-16): unattended plain `make`
invokes refresh; explicit project replacements migrate scoped grants; generated
instructions omit inaccessible projects; interrupted rotations converge;
bundled skill updates retain identical-file timestamps; native settings and
secret references survive routing repairs; open tickets prevent project
archiving. Context tests cover first adoption, persona/skill/pin changes,
unchanged reapplies, retries after failure, pagination and preserving Paperclip
sessions and transcripts. Against the pinned live Hermes API, a synthetic
Discord-source session was created, ended with `session_reset`, and read back
with its row and metadata retained; the synthetic fixture was then removed.
No model call, real chat boundary or outbound message was used. Read-only
reconciliation preflight passed for the live profiles. Full live deployment
and the complete Discord-to-team return path: **NOT RUN** for these changes.

Project retirement correction (2026-10-16): the owner's subsequent live `make`
updated bot project grants and refreshed six profiles, retaining two old chat
sessions, then failed archiving a retired project. Paperclip's pinned validator
rejects an ISO timestamp with `+00:00`; it requires the UTC `Z` suffix. The old
payload reproduced HTTP 400 in disposable native Paperclip state. With the
corrected deployer payload, `tools/check_paperclip.py --project-retirement`
passed archiving, retained ticket history, an untouched replacement project and
an unchanged second pass. No production project was changed by that fixture.
Successful full live reapply after the correction: **NOT RUN**.

Record PASS, FAIL or NOT RUN with timestamp, steps and sanitized evidence.

| ID | Criterion | Result |
| --- | --- | --- |
| G1 | In a project's Discord channel, "@bot do research on X" ends with a Paperclip ticket and notes with sources under `Projects/<slug>/` in Obsidian | NOT RUN |
| G2 | A bot hands a chat question to the PM by child ticket; the PM splits it across the team; members do the work on their tier accounts; the bot is woken and answers in the same channel without the owner | NOT RUN |
| T1 | make hires the default team once (lead first, others reporting to it, each member on its `config/team.yaml` harness with Paperclip's CLI engine and that harness's model, persona as AGENTS.md) and a second make hires nobody and leaves edited instructions alone | NOT RUN |
| T3 | After the lead is terminated, `make` hires a new lead, moves the orphaned members under it and lists the tickets the old lead held; after a login expires, `make bot` option 4 renews the same connection and every member of that harness runs again | NOT RUN |
| T4 | A team mixing Claude Code and Codex members: option 4 signs both in; a Codex member completes a ticket on its tier account with sudo where its tier allows it, and a Claude member likewise | NOT RUN |
| T2 | The onboarding ticket is filed once; the PM splits it; sysadmin and devops write `Server/` (no secret values, nothing changed on the host) | NOT RUN |
| G3 | A ticket the owner assigns to a bot in Paperclip is done by that bot and answered on the ticket | NOT RUN |
| G4 | Two bots hand work over by chat @mention and by ticket | NOT RUN |
| B1 | `make bot` adds a bot, signs it in to a model and a chat app through Hermes' menus and hires it in Paperclip, without restarting the guest | NOT RUN |
| B2 | `make bot` changes an existing bot's model or chat app, and removes a bot (agent terminated, key revoked, profile archived) | NOT RUN |
| B3 | A bot on Telegram (or another non-Discord app) works like a Discord bot | NOT RUN |
| F1 | In one bot: code execution, browser, web extraction, an MCP server, a plugin with a Python dependency, a cron job and delegation work | NOT RUN |
| X1 | As root in the guest: the host, LAN, tailnet, other host ports and IPv6 are unreachable; only the internet, the Paperclip API and the notes share answer | NOT RUN |
| X2 | A bot's task key cannot create tickets outside its projects or read other tickets | NOT RUN |
| X3 | After a bot wipes the vault, the owner restores it from the history | NOT RUN |
| O1 | A second `make` changes nothing; a killed `make` resumes | NOT RUN |
| O2 | `make backup`, `make fclean`, `make restore` bring back Paperclip, the vault and its history, every bot and its logins; a host task then runs | NOT RUN |
| O3 | Source checks and gitleaks are clean; the docs describe v1.1 | NOT RUN |
| N1 | With the browser Obsidian off: an agent reads and writes notes; `make` and a reboot keep the container and its route gone; root-only snapshots still run | NOT RUN |
| N2 | `make notes`: a server edit reaches desktop Obsidian, a laptop edit reaches the server and an agent, a conflict keeps both versions, `.obsidian/` never travels, a public repository is refused | NOT RUN (unit-tested against a local bare remote) |

## Earlier versions

v1 (single Operations worker, Research and reserve profiles, custom ticket
bridge) and the abandoned v2 (per-bot containers and a sync service) are
recorded in Git history; their results do not carry over to v1.1.

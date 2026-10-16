# Status

Zelab is experimental. This page separates what has been exercised from what
has not. An HTTP 200, a parsed configuration or a unit test is not acceptance;
anything not exercised live is NOT RUN.

Versions: Hermes `3637c512` (v0.21.5+9529), Paperclip `2026.1005.0`, Debian 13
`20261001-2618`, Node `26.7.0`, Codex `0.162.0`, Claude Code `2.1.287`.

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
| T1 | make hires the default team once (lead first, others reporting to it, Claude CLI engine, Opus 5.5, persona as AGENTS.md) and a second make hires nobody and leaves edited instructions alone | NOT RUN |
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

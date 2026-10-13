# Real Discord bot pool

The owner selected five reserve bot identities, additional to Operations and
Research. Each specialist will have a separate real Discord account, name,
avatar and DMs. Slots use native Hermes profiles with separate memories and
unprivileged SSH tool accounts. Slot names stay stable when a personality is
assigned; Discord's visible bot name/avatar can change independently.

Plain `make` prepares `hermes_reserve_count` slots (default 5) without cloning
owner credentials or memories. It seeds settings once and parks reserves.
Subsequent native UI edits and lifecycle choices are retained. Allocation,
retirement and Discord acceptance remain unverified; prepared empty slots are
not usable bot identities. An independently created, renamed or removed managed
slot stops provisioning for reconciliation rather than recreating its saved
identity automatically.

## One-time owner inputs and exact destinations

Create these applications in the
[Discord Developer Portal](https://discord.com/developers/applications).
Provisional names are sufficient; no specialist name or avatar is needed yet.

| Portal application | Hermes profile | Tool account |
| --- | --- | --- |
| Zelab Reserve 01 | `reserve-01` | `zelab-reserve-01` |
| Zelab Reserve 02 | `reserve-02` | `zelab-reserve-02` |
| Zelab Reserve 03 | `reserve-03` | `zelab-reserve-03` |
| Zelab Reserve 04 | `reserve-04` | `zelab-reserve-04` |
| Zelab Reserve 05 | `reserve-05` | `zelab-reserve-05` |

Follow the Discord step in [SETUP.md](SETUP.md) for Portal creation, intents,
permissions, invitations and token entry. Select
the matching native Hermes profile before saving each token. Keep unused slots
parked and retain owner-only admission until audience denial tests pass.
Research uses its own `research` profile and a separate application. Operations
uses the default profile. Reuse an existing application if its token is available;
application creation is not required on every server reinstall.

## Allocation contract

Allocate only a declared unused slot. Configure the requested specialist's
SOUL, model, tools, name/avatar and audience, then start its native profile and
verify its real replies/DMs. The API supports updating a bot's
[username/avatar](https://docs.discord.com/developers/resources/user#modify-current-user).
Respect Discord rate limits. Keep the slot's internal profile name/tool account
stable; do not rename it while provisioning expects that identity.

Never copy one bot token into multiple adapters. Report pool exhaustion instead
of silently reusing an active bot. Retirement parks the profile and preserves
its credentials, native memory/history and workspace. Reusing retired personal
state is an explicit lifecycle decision, not an automatic reset or allocation.
An unallocated slot receives no Operations bridge credential or host privilege.

Account creation/token retrieval follow
[Discord's documented Portal bootstrap](https://docs.discord.com/developers/quick-start/getting-started).
This initial owner step is separate from server provisioning; no owner shell
command or password in chat is required.

# Trust model

The KVM guest is the only hard wall. A hijacked bot can reach everything inside
the guest and the whole shared vault, but not the host. Team members (host
agents) have the rights of their tier's account, set in `config/team.yaml`. Prompt text is
advice to a model, never an access control: every boundary below is enforced by
an OS account, a scoped key, a firewall rule or a native product setting.

## What a hijacked bot can and cannot do

| A hijacked bot can | It cannot, because |
| --- | --- |
| Read and change every bot's memory, sessions and logins in the guest | Reach the host: KVM guest boundary |
| Read, change and delete any note in the shared vault | Erase note history: a root-only git snapshot on the host every 5 minutes, outside the share |
| Create tickets in the projects of its own task key, assigned to itself, to another bot or unassigned | Create tickets elsewhere or read others' tickets: Paperclip's `task_bridge` key scope |
| Assign the team lead (the PM), which then runs at once on its tier account and can hand work to any team member | Assign any other team member, or act on the host itself: `allowedAssigneeAgentIds` of its key, the guest boundary |
| Use the internet | Reach the LAN, the tailnet, other host ports or IPv6: guest nftables |

## Authority by role

| Identity | Authority | Enforced by |
| --- | --- | --- |
| Deployment operator (you) | Provisioning, unattended sudo, all password files | `/etc/sudoers.d/zelab-agent-<user>`, `private_dir` mode 0700 |
| Hermes bots (all profiles) | Everything the `zelab-hermes` account can do in the guest; the whole vault through SFTP; the Paperclip API with the bot's task key; the internet | One guest account for every profile; guest nftables; the notes-only SFTP listener |
| A bot's Paperclip agent | Paperclip runs it for tickets assigned to it | Its `hermes_gateway` adapter reaches only `/p/<bot>` with that profile's own API key (other profiles' keys are refused) |
| A bot's task key | Create tickets in the picked projects, assigned to itself, another bot, the team lead, or nobody; comment on and update its own or assigned tickets inside a Paperclip run | `task_bridge` scope: `projectIds`, `allowedAssigneeAgentIds` |
| Team member on tier `<tier>` | The account `zelab-team-<tier>`: its home and workspace, the preset's groups, sudo commands and path rights, read/write on the whole vault; Paperclip's standard agent key | OS account, `/etc/sudoers.d/zelab-team-<tier>`, POSIX ACLs on the tier's paths and the vault, SSH key with `from=<host>` and `restrict` |
| Chat users | Whatever each bot's own allowlist admits | Hermes' native allowlist, set in `hermes gateway setup` |

Default tiers (`config/team.yaml`): `dev` is `repo-dev` (PM, frontend,
backend, mobile, game, data: own paths, no sudo), `ops` is `docker` (devops:
the docker group, root-equivalent on most hosts, and read-only
`systemctl`/`journalctl`/`docker` through sudo), `root` is `full-sudo`
(sysadmin). `monitor-readonly` is also available.

## Accepted risks

These are gaps in the tools as shipped. Zelab records them instead of adding a
layer.

- **No isolation between bots or between projects inside the guest.** Every
  profile runs as `zelab-hermes` and the vault is shared whole. A project that
  must stay separate gets its own guest (a second site), not file permissions.
- **Any agent key can create, rename, archive or set the lead of any Paperclip
  project, and list agents and projects** (found in v2 validation). No access
  rule relies on project leads.
- **Host agents run with Paperclip's standard agent key and their account's
  rights.** A `full-sudo` host agent is root on the host.
- **Chat reaches the team, and through the PM the root tier, without the owner.**
  A bot's key may assign only the team lead, but every team member holds
  Paperclip's standard agent key, which may assign any agent in the company
  (Paperclip's default). So anyone a bot's chat allowlist admits, or text that
  steers a bot or the PM, can get work to the `full-sudo` sysadmin. Routing to
  root is the PM's judgment, written in its instructions, not a control.
  Remove the root tier (or set it to a narrower preset) if that is too much.
- **Claude Code runs with Paperclip's default `dangerouslySkipPermissions`.**
  Members act without per-command approval; their tier account is the limit.
- **Team members write the whole vault.** A mistaken or steered member can change
  any note; the root-only history undoes it.
- **A hijacked bot can steal any bot's subscription login.** Logins live in each
  profile under the same account; revoke a stolen one at the provider.
- **Discord channels and who sees them are set up by hand in Discord.** A bot
  that allows other bots (`DISCORD_ALLOW_BOTS=mentions`, offered by `make bot`)
  answers any bot in that server that @mentions it.
- **Chats on other platforms are not managed by Zelab.** Each bot's allowlist is
  whatever its owner entered in Hermes' wizard.
- **A task key comments on or updates tickets only inside a Paperclip run.**
  Outside one (straight from chat) a bot can create tickets but not comment;
  it assigns the ticket to itself so Paperclip runs it.

## Secrets

- UI passwords are entered only in the operator's terminal, with hidden input
  and confirmation for new passwords; working copies live in a 0700 `/dev/shm`
  directory removed on exit, and stale ones are removed by the next `make`.
  Hermes keeps its native salted scrypt hash.
- Model logins and chat tokens are typed into Hermes' own wizards
  (`hermes setup model`, `hermes gateway setup`) in the owner's terminal, and
  stay in the bot's profile in the guest. `make bot` reads only whether they are
  set, never their values.
- Each bot's gateway API key and task key are generated by `make bot`, sent to
  the guest on standard input and written to the profile's `.env` (0600). The
  API key is also stored encrypted in Paperclip as the agent's secret.
- Host agents' subscription logins are Paperclip AI connections, created by
  Paperclip's own sign-in command run as its service account.
- Project SSH keys are generated per project in `private_dir/projects`; Paperclip
  stores the private key as an encrypted company secret for its SSH environment.
- Machine credentials (Paperclip JWT secret and master key, deployment keys,
  the Obsidian runtime password) are retained across reapplies; a missing
  source stops the run instead of rotating a credential the site still uses.
- `.env` accepts three path names and nothing else; values are data, never
  shell code. Tool output goes to a 0600 log under `private_dir/logs`; tasks that
  touch secrets are `no_log`.
- Backups are AES-256 GPG archives keyed by `private_dir/backup-passphrase`,
  verified by decrypting and comparing before the file is renamed into place.

## Language-model guardrails

Prompt injection is the realistic attack: text a bot reads from a web page, a
note another bot wrote, a file or a ticket could steer it. The installer only
tells each bot, in its Paperclip agent instructions, that such text is data.
That is advice, not a control. The controls are the ones above: the guest wall,
the key scopes and the tier accounts. Each team member's AGENTS.md carries the
working rules (no destructive command without the owner's yes, and so on); that
is advice too. Give tiers the narrowest preset that does the job.

## Known limitations

- Paperclip stores the tier SSH private keys in its database; the database
  is reachable only by root, the operator and the service account.
- Nothing here protects the host from the owner's own requests to a `full-sudo`
  team member.

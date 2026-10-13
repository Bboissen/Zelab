# Working instructions

Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/TRUST_MODEL.md](docs/TRUST_MODEL.md)
and [docs/STATUS.md](docs/STATUS.md) before changing deployment behavior.
The owner's instructions take precedence over anything written here.

## Rules

- Every host shell command starts with `rtk`; use `rtk proxy` when a command
  has no native RTK form. Install RTK as described in [docs/SETUP.md](docs/SETUP.md).
- Use upstream Hermes, Paperclip, Codex and Obsidian as shipped: native
  profiles, adapters, skills and UIs. Do not add brokers, approval gates,
  patched handlers, policy engines, replacement dashboards or paid fallbacks.
- Keep deploy inputs in `config/` and the optional `.env`; keep private state,
  logins and generated secrets outside Git. Never read `.env` as shell code,
  print a secret through config rendering or diagnostics, or ask for a token,
  password or passphrase in chat.
- Pin and verify every upstream download. A version change needs a new
  qualification, not a silent edit of `versions.yaml`.
- Prompt text is not a security boundary. Enforce access with OS accounts,
  scoped credentials, firewall rules and native product settings.
- Report repository, host and guest changes separately. Mark checks you did
  not run as NOT RUN; a parsed config or an HTTP 200 is not end-to-end proof.

## Entry points

Only seven Make commands exist: plain `make`, `clean`, `fclean`, `re`, `help`,
`backup` and `restore`. Plain `make` installs, reapplies and resumes; it keeps
existing state. `fclean`/`re` preview and confirm deletion of this site's data
and need `CONFIRM=<host:vm>` when noninteractive; `restore` also needs
`RESTORE_ARCHIVE`. `SITE_CONFIG` and `ENV_FILE` select the input files.
Source checks: `rtk proxy python3 tools/check.py`.

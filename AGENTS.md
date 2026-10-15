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

Only nine Make commands exist: plain `make`, `clean`, `fclean`, `re`, `help`,
`backup`, `restore`, `bot` and `notes`. Plain `make` installs, reapplies and resumes; it keeps
existing state. `bot` adds, sets up or removes Hermes bots and needs a terminal. `notes` syncs the vault with its private Git remote. `fclean`/`re` preview and confirm deletion of this site's data
and need `CONFIRM=<host:vm>` when noninteractive; `restore` also needs
`RESTORE_ARCHIVE`. `SITE_CONFIG` and `ENV_FILE` select the input files.
Source checks: `rtk proxy python3 tools/check.py`.

## Git

Agents manage branches, commits, pull requests and merges on their own under
these rules; the owner's instructions override them. `main` is public and
protected by the `protect-main` ruleset (pull requests only, squash or rebase,
linear history, the `check` job green, conversations resolved, no bypass).

- **Branches:** start from an up-to-date `main`, one topic per branch, named
  `<type>/<topic>` (`feat`, `fix`, `docs`, `chore`, `refactor`). Stack a branch
  on another only when it depends on it, and rebase it onto `main` once its base
  merges. Never commit to `main` directly.
- **Commits:** small and self-contained; each passes
  `rtk proxy python3 tools/check.py`. Imperative subject of at most 72
  characters, a body that says why. Run
  `rtk proxy gitleaks detect --source . --no-git --redact` before committing.
  Never commit secrets, `.env`, `config/site.yaml`, other local inputs,
  generated files or caches.
- **Public tree:** no personal identifiers (names, emails beyond the commit
  author, user IDs, hostnames, tailnet names, LAN addresses) in tracked files.
  Never push `codex/*`, `refs/codex/*`, `refs/t3/*` or anything from the ignored
  `archive/` directory; `.git/hooks/pre-push` refuses the first three.
- **Pull requests:** one per branch, opened when the checks pass locally. The
  description says what changed and why, the checks run, what was exercised
  live and what is NOT RUN, and the risks. Follow the pull request template.
- **Merging:** merge your own pull request once the required checks are green,
  the branch is up to date with `main` and every conversation is resolved. Use
  squash (one commit per pull request); use rebase only for a deliberate series
  of commits. Never use `--admin`, force-push `main`, or rewrite published
  history. After merging, update the local `main` and delete the local branch
  (the remote branch is deleted automatically).
- **Ask the owner first** before merging a pull request that weakens a trust
  boundary or adds an accepted risk, changes a pin in `versions.yaml` without a
  recorded qualification, changes these rules, deletes user data on upgrade,
  or before pushing a release tag.
- **Abandoned work:** close the pull request with a one-line reason. Before
  deleting the branch, save it with
  `git bundle create ~/.local/state/<name>.bundle <branch>` (mode 0600).
- **End of a task:** leave the working tree clean (committed, or listed in the
  report as uncommitted with the reason) and no stale local branches.

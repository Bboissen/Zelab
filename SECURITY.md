# Security

Zelab provisions an owner-authorized Operations worker with real host sudo.
Run it on a host you administer. Administration UIs use authenticated
localhost or Tailscale access; never expose them through Funnel or a public
relay. The complete threat model, trust boundaries and known limitations are
in [docs/TRUST_MODEL.md](docs/TRUST_MODEL.md).

## Supported scope

This is an experimental single-owner deployment, not a qualified multi-tenant
service. Hermes profiles share one runtime Unix identity, so restricted SSH tool
accounts limit what a profile's tools can do but do not isolate a compromised
gateway or plugin. Keep every bot owner-only, and add no unprivileged coding
worker until the upstream sandbox exists and the audience denial checks in
[docs/STATUS.md](docs/STATUS.md) have passed on your site.

Reapplying the installer re-checks native owner allowlists, pairing grants,
cross-profile routes, restricted tool settings and the scope and identity of
every bridge credential. Those checks run during provisioning; native UI edits
made afterwards are not continuously policed.

## Report a vulnerability

Use the repository's **Security → Report a vulnerability** form when private
reporting is enabled. If it is absent, ask the maintainer for a private contact
without posting exploit details, credentials, private logs or vault contents.
There is no monitored mailbox or response-time promise.

Include the affected revision, a minimal reproduction with synthetic data,
expected versus observed behavior, and the access the attack requires.

## Secrets and history

Keep `.env`, `config/site.yaml`, native logins, password files, backups and VM
images outside the published source; never paste them into an issue. The
installer generates every secret on the host with mode 0600 and writes none of
them to Git, logs or command arguments.

The published history is a single reviewed snapshot. The maintainers' private
development history was scanned with Gitleaks before publication: the only
token-shaped string it contained was a synthetic placeholder in a deleted test
script, and deleted notes carried a personal Discord user ID. Neither is in the
published tree. Source scans are a safeguard, not proof that every possible
secret has been found; see [docs/RELEASING.md](docs/RELEASING.md).

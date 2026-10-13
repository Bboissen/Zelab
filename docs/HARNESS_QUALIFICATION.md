# Optional worker harness qualification

Codex retains the previously qualified `0.162.0` package and SHA-256 pin.
The setup choice adds Claude Code without changing that version or Paperclip's
pinned upstream release (`v2026.1005.0`, commit
`467125fafb47a8520856504fecc48d6e32055db1`).

Claude Code `2.1.287` (Linux x86-64) was inspected on 2026-10-13:

- PASS: upstream `manifest.json` detached GPG signature verified against the
  documented Anthropic release key fingerprint
  `31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE` in an isolated temporary keyring.
- PASS: native binary SHA-256 matches the signed manifest and the pin in
  `versions.yaml`:
  `3920489a5109cff5786a1a392c25277408ff22bc796d5edb9c16a60e5a1718f0`.
- PASS: downloaded unmodified binary reports `2.1.287 (Claude Code)`;
  `auth login --help` supports the native subscription login (`--claudeai`).
- PASS: the checksum-verified pinned Paperclip source supports `claude_local`,
  `engine: cli`, explicit remote `CLAUDE_CONFIG_DIR`, native paused process agents,
  and adapter replacement through the owner API.
- NOT RUN: subscription authentication, an actual Claude worker task over SSH,
  Discord delivery, live whole-site restore and live reapply of an existing site.

Reference-site reapply on 2026-10-13 subsequently installed both pinned CLIs
through plain `make`, preserving the existing Codex agents. Claude's qualified
binary was checked against the pin again before populating the native download
cache. Codex task `ZEL-1` completed through Paperclip's native SSH environment,
with host identity verified from command-event output. Claude subscription
authentication and Claude task execution remain NOT RUN; see [STATUS.md](STATUS.md).

Source and configuration tests exercise selection, skipped workers, adapter
configuration and retained security. Their results are separate from the
authenticated acceptance checklist in [STATUS.md](STATUS.md).

Anthropic guidance checked on 2026-10-13 permits an end user to sign in to an
unmodified Claude Code binary with their own subscription, including on a
platform hosting that binary. Its support guidance also covers `claude -p`
under subscription limits. Zelab selects Paperclip's CLI engine and the
unmodified native binary; the owner signs in through Anthropic's native flow
under the worker account. This matches the described authentication route;
it is not permission to share an account, bypass limits, resell usage or reuse
subscription tokens in another model client. Claude sign-in and task execution
on this site remain NOT RUN.

References: [Claude Code legal and authentication guidance](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use),
[Claude plan usage for headless CLI and SDK](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan).

Upstream references: [Claude binary integrity and code signing](https://code.claude.com/docs/en/setup#binary-integrity-and-code-signing),
[Paperclip Claude Code adapter](https://docs.paperclip.ing/reference/adapters/claude-code/).

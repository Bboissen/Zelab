# Releasing

Publish Zelab as experimental until the checks in [STATUS.md](STATUS.md) pass.
A source archive and green tests do not establish Discord delivery, safe
multi-audience use or complete disaster recovery.

## Checks before a release

```bash
rtk proxy python3 tools/check.py
rtk proxy .venv/bin/python tools/check_repository.py
rtk proxy gitleaks detect --no-git --source . --redact
```

`tools/check.py` runs the unit suites, parses every playbook against the public
examples and runs the source checks; it ignores `.env` and connects to nothing.
`check_repository.py` refuses private filenames, symlinks, broken local links
and common secret patterns in the publishable file set. The same checks, plus
the npm audit, the pip audit of the qualified Hermes inventory and the Node
bridge tests, run in [CI](../.github/workflows/check.yaml).

Hermes dependency manifests are qualified separately; see
[the dependency README](../deploy/roles/hermes/files/dependencies/README.md).
Review generated package locks as code.

## What the public history must not contain

The private development history was scanned with Gitleaks before the first
publication. It contained no live credential: the single token-shaped string
was a sequential placeholder in a deleted test script. It did contain the
maintainer's Discord user and channel IDs, local usernames, home paths and a
tailnet hostname inside deleted notes and evidence. Those are personal, not secret, and they
stay out of the published tree by publishing a snapshot instead of the history.

If you fork this repository, keep `config/site.yaml`, `.env`, password files,
backups and VM images out of Git; `.gitignore` already covers them.

## Publish a snapshot

The public `main` is a single-commit orphan branch with no ancestor in the
private history, so the private branch is never merged into it or proposed as
a pull request. Commit the reviewed working tree on the private branch first,
then create a fresh snapshot from it and push only that branch:

```bash
rtk proxy python3 tools/check.py
rtk proxy git checkout --orphan snapshot
rtk proxy git commit -m "Zelab: private Discord agents on a home server"
rtk proxy git branch -M main                          # replaces the previous local snapshot
rtk proxy gitleaks detect --source . --redact --log-opts=main
rtk proxy git push -u origin main                     # add --force-with-lease to replace a published snapshot
```

`.gitleaks.toml` keeps the default rules and ignores local caches and the
synthetic fixture IDs, so a clean scan reports no leaks. Replacing a published
snapshot rewrites public history and breaks open forks and pull requests; after
the first publication prefer developing on branches of the public `main`.

Never push the private development branches or their checkpoint refs
(`refs/codex/*`, `refs/t3/*`); use plain `git push -u origin main`, never
`--all` or `--mirror`. To
distribute a tarball instead, run
`rtk proxy .venv/bin/python tools/check_repository.py --archive /absolute/path/zelab-source.tar.gz`;
it stages the publishable files, re-checks them, runs Gitleaks on the staged
tree and prints the archive's SHA-256.

## Release notes

Name the exact tested pins from `versions.yaml`, the host distribution the
release was exercised on, and which acceptance items remain NOT RUN. Enable
private vulnerability reporting on the hosting platform and keep scanner logs
private.

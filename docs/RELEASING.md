# Releasing

Publish Zelab as experimental until the checks in [STATUS.md](STATUS.md) pass.
A source archive and green tests do not establish Discord delivery, safe
multi-audience use or complete disaster recovery.

## Checks before a release

```bash
python3 tools/check.py
.venv/bin/python tools/check_repository.py
gitleaks detect --no-git --source . --redact
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

## Publish changes and source archives

The first public commit was an orphan snapshot: the private development
history is not an ancestor of public `main`. Later public changes use topic
branches and pull requests, following [AGENTS.md](../AGENTS.md). Do not replace
public history, force-push `main`, or merge private development branches into it.

Before publishing, scan both the current source and the branch history:

```bash
python3 tools/check.py
gitleaks detect --source . --redact --log-opts=HEAD
```

Secret scanning does not find every personal identifier. Review the publishable
files and deleted historical content for personal names, home paths, real chat
IDs, private hostnames and site addresses. Commit attribution and the GitHub
owner remain public metadata; publishing a snapshot does not hide them.

Never push private development branches or checkpoint refs (`refs/codex/*`,
`refs/t3/*`), or use `git push --all` or `--mirror`. Push only the reviewed topic
branch and merge it through the protected pull request workflow.

To distribute a tarball, run
`.venv/bin/python tools/check_repository.py --archive /absolute/path/zelab-source.tar.gz`;
it stages the publishable files, re-checks them, runs Gitleaks on the staged
tree and prints the archive's SHA-256.

## Repository settings

The public repository is configured so that nothing lands on `main` without
review and a passing check, and so that forks cannot run arbitrary code:

- Rulesets `protect-main` and `protect-release-tags`: no deletion, force push
  or non-linear history on `main`; changes arrive through a pull request with
  the `check` workflow job green and conversations resolved; `v*` tags are
  immutable. There are no bypass actors, so the maintainer uses the same path.
- Actions may use only SHA-pinned GitHub-owned actions; the workflow token is
  read-only; Actions cannot approve pull requests; every pull request from an
  outside fork waits for maintainer approval before a workflow runs.
- Secret scanning with push protection and Dependabot alerts are on. Automatic
  dependency update pull requests stay off: a version change needs a new
  qualification, not an unreviewed lock edit.
- Wiki and projects are off; merged branches are deleted automatically.

## Release notes

Name the exact tested pins from `versions.yaml`, the host distribution the
release was exercised on, and which acceptance items remain NOT RUN. Enable
private vulnerability reporting on the hosting platform and keep scanner logs
private.

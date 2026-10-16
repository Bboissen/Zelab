# Contributing

Zelab is provisioning code for upstream applications. Keep changes small,
declarative and consistent with the [architecture](docs/ARCHITECTURE.md) and
[trust model](docs/TRUST_MODEL.md). Prefer native product features, Ansible
roles, systemd and Compose over a new service or integration layer.

## Local checks

Install Python 3.12 or newer with venv support, Make, Git, GNU tar, GnuPG,
`qemu-img` (the `qemu-utils` package; no KVM needed), then run:

```bash
python3 tools/check.py
```

This bootstraps the hash-locked controller, runs the unit suites, checks the
public source tree and parses every playbook against the example inputs. It
needs no sudo, KVM, account login, private site file or running deployment.
The first run downloads the controller from PyPI; later runs reuse it. CI runs
the same command plus the npm, pip and Node bridge checks in
[.github/workflows/check.yaml](.github/workflows/check.yaml).

Python uses four spaces, YAML two, Make recipes
tabs. Tests must use temporary fixtures, never a contributor's `.env`, login or
host services.

## Changes

Read [AGENTS.md](AGENTS.md) before changing deployment behavior. Keep
credentials and generated state out of Git. In a pull request, state the
concrete behavior change and the checks you ran; distinguish source checks from
live acceptance and record unrun account or device checks as **NOT RUN**.
Update the matching guide when commands or configuration change.

The controller lock is generated from `deploy/requirements-controller.in`;
keep exact versions and hashes. Application versions live in `versions.yaml`
and Paperclip's npm lock. Qualify an update and its recovery in an isolated site
before deploying it; never use an automatic dependency downgrade as a fix.

To exercise a Paperclip runtime candidate against a real database and
authenticated API boundary, install its locked npm dependencies in a disposable
directory and run:

```bash
.venv/bin/python tools/check_paperclip.py --runtime /absolute/runtime --node /absolute/pinned/node
```

See [SECURITY.md](SECURITY.md) for sensitive reports and
[docs/RELEASING.md](docs/RELEASING.md) before publishing a snapshot.

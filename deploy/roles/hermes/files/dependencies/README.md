# Qualified Hermes dependency manifests

These two package manifests derive from NousResearch/hermes-agent at the commit
recorded in `manifest.json`. Its MIT license is included. No application code is
modified. The native installer and Hermes PM still own dependency installation,
hash verification, generation selection and optional tools.

Security updates: PyJWT 2.15.0, httpx2/httpcore2 2.12.0, multidict 6.9.1,
oauthlib 4.0.0 and urllib3 2.8.0. The core/client pair must move together. The
remaining upstream selections are retained; this is not a blanket dependency
upgrade. `uv.lock` was resolved using native `python3 -m pm.build_env --lock-only`.

Qualification uses a disposable copy of the verified source, with these
manifests, never a running installation:

```sh
rtk proxy python3 -m pm.build_env --source /absolute/disposable/source --lock-only
rtk proxy python3 -m pm.build_env --source /absolute/disposable/source --out /absolute/disposable/runtime --extra all --extra ddgs
```

Set `HERMES_HOME` and `HERMES_RUNTIME_DIR` to disposable directories for both
commands. Native PM validates the installed dependency graph. Export installed
registry package names/versions through `importlib.metadata` into
`runtime-requirements.txt`, excluding the local `hermes-agent` application.
The inventory describes Linux x86_64, Python 3.14, extras `all` and `ddgs`.
CI checks its integrity against the lock and audits it without installing it.
Additional owner-installed plugins/extras require their own audit.

Update the manifest hashes and inventory together after requalification. A
source-pin change requires a new qualification; provisioning refuses unrelated
local package-manifest edits. The dependency revision is part of installation
markers, so a security update is applied even when the Hermes commit is unchanged.
The next ordinary installer run loads the new native generation and restarts
the gateway/dashboard. No additional service or per-request check is added.

# Qualified Hermes dependency manifests

These two package manifests derive from NousResearch/hermes-agent at the commit
recorded in `manifest.json`. Its MIT license is included. No application code is
modified. The native installer and Hermes PM still own dependency installation,
hash verification, generation selection and optional tools.

Security updates: PyJWT 2.15.0, httpx2/httpcore2 2.12.0, multidict 6.9.1,
oauthlib 4.0.0, urllib3 2.8.0 and MCP/MCP types 2.2.0. The core/client pair must move together. The
remaining upstream selections are retained; this is not a blanket dependency
upgrade. `uv.lock` was resolved using native `python3 -m pm.build_env --lock-only`.

Qualification uses a disposable copy of the verified source, with these
manifests, never a running installation:

```sh
python3 -m pm.build_env --source /absolute/disposable/source --lock-only
python3 -m pm.build_env --source /absolute/disposable/source --out /absolute/disposable/runtime --extra all --extra ddgs
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

## MCP 2.2.0 qualification: 2026-10-16

The [upstream OAuth credential-binding advisory](https://github.com/modelcontextprotocol/python-sdk/security/advisories/GHSA-qx49-fqc8-xw99)
affects MCP 2.0.0 and is fixed in 2.2.0. Only `mcp` and `mcp-types` changed
versions in the native resolved lock. Their wheel SHA-256 values were checked
against the upstream PyPI metadata; native PM verified installation from that
lock. Other package versions and the Hermes source pin are unchanged.

Qualified in a disposable archive of the pinned source in the Debian guest,
using native Python 3.14.7 and PM, separate `HERMES_HOME`, `HERMES_RUNTIME_DIR`
and cache directories, and extras `all` plus `ddgs`. The installed inventory has
108 registry distributions. The native dev group was built separately for tests;
its packages are not added to the deployment inventory.

- 202 upstream tests passed, one file per process: `test_mcp_tool.py`,
  `test_mcp_oauth.py`, `test_mcp_oauth_integration.py`,
  `test_mcp_oauth_issuer_binding.py`, `test_mcp_oauth_issuer_origin.py`,
  `test_mcp_streamable_http_arity.py`, `test_mcp_sse_transport.py` and
  `test_mcp_sse_fallback.py` under `tests/tools/`. Point `npm_config_cache` at a
  disposable directory outside the test process's home so the upstream home I/O
  guard stays enabled when tests inspect the npx cache.
- Native Hermes `MCPServerTask` discovered and called an echo tool on a real
  disposable MCP server over stdio and Streamable HTTP. No provider credentials,
  inference or production MCP configuration were used.
- `pip-audit` 2.10.1 found no known vulnerabilities in the regenerated inventory;
  the unchanged Paperclip production npm lock also passed its moderate audit.

The manifest retains the preceding qualified hashes so ordinary `make` can
upgrade that known installation while still rejecting unrecognized edits.
Context freshness includes these dependency inputs as well as `versions.yaml`.
The production dependency upgrade and Discord acceptance are **NOT RUN**.

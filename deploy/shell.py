"""Host command and Tailscale status helpers shared by deployment tools.

Standard library only: install.py imports this before the controller exists.
Privileged commands use native sudo. Output is captured and returned, never
echoed, because probes can contain private host or account details.
"""

import json
import subprocess


def command(*argv):
    return list(map(str, argv))


def as_root(*argv, prompt=False):
    """Native sudo; without prompt, sudo must not ask."""
    return ['sudo', *([] if prompt else ['-n']), *map(str, argv)]


def quiet(argv, *, text=False, timeout=None):
    """Run with captured output; the caller decides what a failure means."""
    return subprocess.run(command(*argv), capture_output=True, text=text, timeout=timeout)


def output(argv, timeout=15):
    """Standard output of a successful command, otherwise None."""
    try:
        result = quiet(argv, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout if result.returncode == 0 else None


def load_json(text):
    """A `tailscale ... --json` document; {} when unavailable or malformed."""
    try:
        data = json.loads(text or '{}')
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def tailnet_name(status):
    """(backend running, MagicDNS name or None) from `tailscale status --json`."""
    try:
        name = status['Self']['DNSName'].rstrip('.')
    except (KeyError, TypeError, AttributeError):
        name = None
    return status.get('BackendState') == 'Running', name


def serve_routes(serve):
    """(host, port, backend target) per web endpoint of `tailscale serve status --json`."""
    for endpoint, web in (serve.get('Web') or {}).items():
        host, _, port = endpoint.rpartition(':')
        yield host, int(port), (((web or {}).get('Handlers') or {}).get('/') or {}).get('Proxy')

#!/usr/bin/env python3
"""Check public source files; optionally export a scanned, history-free archive."""

import argparse
import hashlib
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = {".env", "auth.json", "credentials.json", "backup-passphrase"}
PRIVATE_DIRS = {".git", ".venv", "node_modules", "__pycache__", "private", "raw", "backups", "secrets", "state", "runtime", "dist"}
PRIVATE_SUFFIXES = {".key", ".pem", ".token", ".gpg", ".qcow2", ".iso", ".sqlite", ".db", ".log", ".password", ".password-hash"}
SECRET_PATTERNS = {
    "private key": rb"-----BEGIN (?:RSA |OPENSSH |EC |DSA |PGP )?PRIVATE KEY",
    "GitHub token": rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})",
    "OpenAI key": rb"sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}",
    "Discord token": rb"[MN][A-Za-z0-9_-]{23,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}",
}


def source_files(root=ROOT):
    result = subprocess.run(
        ["rtk", "proxy", "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root, check=True, capture_output=True,
    )
    return sorted({Path(name.decode()) for name in result.stdout.split(b"\0") if name
                   and ((root / name.decode()).exists() or (root / name.decode()).is_symlink())})


def check(root, files):
    errors = []
    for relative in files:
        path = root / relative
        if (relative.is_absolute() or ".." in relative.parts or path.is_symlink()
                or any((root / parent).is_symlink() for parent in relative.parents)
                or not path.is_file()):
            errors.append(f"{relative}: source must be a regular file within the repository")
            continue
        if (relative.name in PRIVATE_NAMES or set(relative.parts) & PRIVATE_DIRS
                or relative.suffix in PRIVATE_SUFFIXES
                or relative.as_posix() in {"config/site.yaml", "config/fresh.yaml"}
                or (relative.name.startswith(".env.") and relative.name != ".env.example")):
            errors.append(f"{relative}: private/generated file selected for publication")
            continue
        data = path.read_bytes()
        for label, pattern in SECRET_PATTERNS.items():
            if re.search(pattern, data):
                errors.append(f"{relative}: possible {label}; contents withheld")
        if path.suffix == ".md":
            for target in re.findall(r"\[[^\]\n]*\]\(([^)\s]+)\)", data.decode()):
                link = urlsplit(target.strip("<>"))
                if link.scheme or link.netloc or not link.path:
                    continue
                destination = (path.parent / unquote(link.path)).resolve()
                if not destination.is_relative_to(root) or not destination.exists():
                    errors.append(f"{relative}: broken local link: {target}")
    if errors:
        raise ValueError("\n".join(errors))


def export(root, files, destination):
    if not shutil.which("gitleaks"):
        raise ValueError("Install Gitleaks before exporting a release; see docs/RELEASING.md")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("Release output already exists; choose a new --archive path")
    with tempfile.TemporaryDirectory(prefix="zelab-release-") as folder:
        stage = Path(folder) / "zelab"
        for relative in files:
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(root / relative, target)
            target.chmod(0o755 if (root / relative).stat().st_mode & 0o111 else 0o644)
        # Check the copied bytes, then scan exactly the tree that will be shipped.
        check(stage, files)
        subprocess.run(["rtk", "proxy", "gitleaks", "detect", "--no-git", "--source", str(stage), "--redact"], check=True)
        with destination.open("xb") as output, tarfile.open(fileobj=output, mode="w:gz") as archive:
            for relative in files:
                info = archive.gettarinfo(stage / relative, arcname=str(Path("zelab") / relative))
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with (stage / relative).open("rb") as source:
                    archive.addfile(info, source)
    with destination.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    print(f"Source archive: {destination}\nSHA256: {digest}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    files = source_files()
    check(ROOT, files)
    print(f"Public source checks passed: {len(files)} files; no private inputs read.")
    if args.archive:
        export(ROOT, files, args.archive.resolve())


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None

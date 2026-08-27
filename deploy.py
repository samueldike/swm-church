"""Deploy SWM to the VPS.

Credentials come from the environment, never from this file. Set them once in
a local `.env` (which is gitignored) or export them in your shell:

    SWM_VPS_HOST=80.241.212.159
    SWM_VPS_USER=root
    SWM_VPS_PASSWORD=...            # or SWM_VPS_KEY=/path/to/id_ed25519
    SWM_VPS_PATH=/opt/swm

Usage:
    python deploy.py                # pull, rebuild, restart, verify
    python deploy.py --run "cmd"    # run one command on the server

Every deploy rebuilds — see deploy()'s docstring for why "skip the rebuild"
isn't actually a safe option with this Dockerfile.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't encode the box-drawing
# characters used in this script's progress labels.
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import paramiko
except ImportError:
    sys.exit("paramiko is required:  pip install paramiko")


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env reader so credentials can live outside the shell profile."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _config() -> dict:
    _load_dotenv()
    host = os.getenv("SWM_VPS_HOST")
    user = os.getenv("SWM_VPS_USER", "root")
    password = os.getenv("SWM_VPS_PASSWORD")
    key = os.getenv("SWM_VPS_KEY")

    missing = [n for n, v in (("SWM_VPS_HOST", host),) if not v]
    if not (password or key):
        missing.append("SWM_VPS_PASSWORD or SWM_VPS_KEY")
    if missing:
        sys.exit(
            "Missing credentials: " + ", ".join(missing) + "\n"
            "Set them in .env or your environment — see the docstring in this file."
        )
    return {
        "host": host,
        "user": user,
        "password": password,
        "key": key,
        "path": os.getenv("SWM_VPS_PATH", "/opt/swm"),
    }


def _client(cfg: dict) -> "paramiko.SSHClient":
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {"hostname": cfg["host"], "username": cfg["user"], "timeout": 20}
    if cfg["key"]:
        kwargs["key_filename"] = cfg["key"]
    else:
        kwargs["password"] = cfg["password"]
    ssh.connect(**kwargs)
    return ssh


def run(command: str, timeout: int = 900, quiet: bool = False) -> int:
    """Run a command on the VPS, streaming its output. Returns the exit code."""
    cfg = _config()
    ssh = _client(cfg)
    try:
        _, stdout, stderr = ssh.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
        if out and not quiet:
            print(out.rstrip())
        if err.strip() and not quiet:
            print(f"[stderr] {err.rstrip()}", file=sys.stderr)
        return code
    finally:
        ssh.close()


def deploy(build: bool = True) -> int:
    """Pull, rebuild, restart, and smoke-test.

    docker-compose.yml is deliberately preserved: the server's copy differs
    from the repo's (bind mounts, no bundled nginx — the host nginx proxies),
    so a plain pull would clobber a real production config.

    The rebuild is NOT optional. The Dockerfile bakes the whole tree in via
    `COPY . .` — only ./app/media is bind-mounted — so `docker compose up -d`
    alone reuses the old image verbatim even after a git pull, silently
    serving stale templates and code. Docker's layer cache keeps this fast:
    only the late COPY layers rebuild when just source files changed, not the
    slow pip-install layer.
    """
    cfg = _config()
    app = cfg["path"]
    steps = [
        ("Preserving server docker-compose.yml",
         f"cd {app} && cp docker-compose.yml /tmp/compose.keep && git checkout -- docker-compose.yml"),
        ("Pulling main",
         f"cd {app} && git pull --ff-only origin main"),
        ("Restoring server docker-compose.yml",
         f"cd {app} && cp /tmp/compose.keep docker-compose.yml"),
        ("Rebuilding images",
         f"cd {app} && docker compose build app worker beat"),
        ("Restarting containers",
         f"cd {app} && docker compose up -d app worker beat"),
        ("Waiting for startup", "sleep 12"),
        ("Container status",
         'docker ps --filter name=swm --format "{{.Names}}\\t{{.Status}}"'),
        ("Smoke test",
         "for p in / /about /messages /give /contact; do "
         'printf "%-14s %s\\n" "$p" '
         '"$(curl -s -o /dev/null -w \'%{http_code}\' http://127.0.0.1:8085$p)"; done'),
    ]

    for label, cmd in steps:
        print(f"\n── {label}")
        code = run(cmd)
        if code != 0 and label not in ("Container status", "Smoke test"):
            print(f"\nFAILED at: {label} (exit {code})", file=sys.stderr)
            return code
    print("\nDone.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", metavar="CMD", help="run a single command on the server")
    args = ap.parse_args()

    if args.run:
        sys.exit(run(args.run))
    sys.exit(deploy())

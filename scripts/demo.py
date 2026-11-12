"""One cross-platform script for the demo stack (Windows, macOS, Linux).

  python scripts/demo.py setup      generate .env, docker compose up, import credential + workflows, publish
  python scripts/demo.py run        reset + harness against all three workflows (pass harness args after --)
  python scripts/demo.py teardown   docker compose down -v (removes the demo volumes)

Secrets are generated into .env and never printed.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dedupe_demo.config import read_env_file  # noqa: E402

ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
IMPORT_DIR = ROOT / "data" / "import"
CRED_ID = "dedupeDemoPg0001"
WORKFLOWS = {"naive.json": "dedupeNaive00001", "builtin.json": "dedupeBuiltin002", "fixed.json": "dedupeFixed00003"}


def sh(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    print("+", " ".join(args), flush=True)
    return subprocess.run(args, cwd=ROOT, check=check, text=True, capture_output=capture)


def compose(*args: str, **kw) -> subprocess.CompletedProcess:
    return sh("docker", "compose", *args, **kw)


def ensure_env() -> None:
    if not ENV.exists():
        ENV.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
        print("created .env from .env.example")
    lines = ENV.read_text(encoding="utf-8").splitlines()
    generated = []
    for i, line in enumerate(lines):
        key, sep, val = line.partition("=")
        if sep and key.strip() in ("N8N_ENCRYPTION_KEY", "POSTGRES_PASSWORD") and not val.strip():
            lines[i] = f"{key.strip()}={secrets.token_hex(24)}"
            generated.append(key.strip())
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if generated:
        print(f"generated secrets in .env: {', '.join(generated)} (values not shown)")


def write_credential(env: dict[str, str]) -> Path:
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = IMPORT_DIR / "credentials.json"
    cred = [
        {
            "id": CRED_ID,
            "name": "Dedupe demo Postgres",
            "type": "postgres",
            "data": {
                "host": "postgres",
                "port": 5432,
                "database": env.get("POSTGRES_DB", "dedupe"),
                "user": env.get("POSTGRES_USER", "dedupe"),
                "password": env["POSTGRES_PASSWORD"],
                "ssl": "disable",
                "allowUnauthorizedCerts": False,
                "sshTunnel": False,
            },
        }
    ]
    path.write_text(json.dumps(cred), encoding="utf-8")
    return path


def wait_webhooks(port: str, timeout_s: float = 90) -> None:
    """A published POST webhook answers GET with 404 'not registered for GET'; an unknown one says 'not registered'."""
    import httpx

    deadline = time.monotonic() + timeout_s
    pending = {"dedupe-naive", "dedupe-builtin", "dedupe-fixed"}
    while pending and time.monotonic() < deadline:
        for path in list(pending):
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/webhook/{path}", timeout=3)
                if "POST" in r.text:
                    pending.discard(path)
            except httpx.HTTPError:
                pass
        if pending:
            time.sleep(2)
    if pending:
        raise SystemExit(f"webhooks not registered: {sorted(pending)} (see: docker compose logs n8n)")
    print("all three webhooks are live")


def setup() -> None:
    ensure_env()
    env = read_env_file(ENV)
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)  # bind-mounted into n8n
    compose("up", "-d", "--wait", "postgres", "mock-llm")
    # Import while the n8n server is stopped: the CLI and a running server must not
    # migrate or write the same SQLite file at the same time, and a running server
    # only picks up CLI changes after a restart anyway.
    compose("stop", "n8n")
    cli = ["n8n import:credentials --input=/import/data/credentials.json"]
    for name, wf_id in WORKFLOWS.items():
        cli.append(f"n8n import:workflow --input=/import/workflows/{name}")
        cli.append(f"n8n publish:workflow --id={wf_id}")
    cred = write_credential(env)
    try:
        compose("run", "--rm", "--no-deps", "-T", "--entrypoint", "sh", "n8n", "-c", " && ".join(cli))
    finally:
        cred.unlink(missing_ok=True)
    compose("up", "-d", "--wait")
    wait_webhooks(env.get("N8N_PORT", "5678"))
    print(f"n8n editor: http://localhost:{env.get('N8N_PORT', '5678')} (create the owner account on first visit)")


def run(extra: list[str]) -> None:
    sh(sys.executable, "-m", "dedupe_demo.harness", *(extra or ["--runs", "1"]))


def teardown() -> None:
    compose("down", "-v", "--remove-orphans")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("setup", "run", "teardown"):
        print(__doc__)
        return 2
    cmd, extra = sys.argv[1], [a for a in sys.argv[2:] if a != "--"]
    {"setup": setup, "run": lambda: run(extra), "teardown": teardown}[cmd]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

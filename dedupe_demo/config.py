"""Settings from environment variables, with .env (project root) as a fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_env_file(path: Path | None = None) -> dict[str, str]:
    path = path or ROOT / ".env"
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Settings:
    n8n_url: str
    mock_url: str
    pg_host: str
    pg_port: int
    pg_db: str
    pg_user: str
    pg_password: str

    def pg_conninfo(self) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={self.pg_user} password={self.pg_password} connect_timeout=5"
        )


def load_settings() -> Settings:
    file_vals = read_env_file()

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key) or file_vals.get(key) or default

    return Settings(
        n8n_url=f"http://127.0.0.1:{get('N8N_PORT', '5678')}",
        mock_url=f"http://127.0.0.1:{get('MOCK_LLM_PORT', '8000')}",
        pg_host="127.0.0.1",
        pg_port=int(get("POSTGRES_PORT", "5433")),
        pg_db=get("POSTGRES_DB", "dedupe"),
        pg_user=get("POSTGRES_USER", "dedupe"),
        pg_password=get("POSTGRES_PASSWORD"),
    )

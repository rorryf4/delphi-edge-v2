"""Environment configuration. Values come from the repo-root .env locally
and from GitHub Actions secrets in CI."""

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable {name}. See .env.example.")
    return value

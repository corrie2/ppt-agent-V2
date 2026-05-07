from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
SCHEMA_PATH = REPO_ROOT / "db" / "schema" / "001_project_memory.sql"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from agent_long_memory.memory_config import load_memory_config  # noqa: E402
from agent_long_memory.schema import SCHEMA_SQL  # noqa: E402


def schema_path() -> Path:
    return SCHEMA_PATH


def main() -> int:
    config = load_memory_config()
    if not config.database_url:
        print(
            "Memory database initialization failed: PPT_AGENT_MEMORY_DATABASE_URL is not set. "
            "Set PPT_AGENT_MEMORY_DATABASE_URL before running this script.",
            file=sys.stderr,
        )
        return 1

    try:
        import psycopg
    except ImportError:
        print(
            "Memory database initialization failed: psycopg is not installed. "
            'Install the long-term memory extra first: pip install -e ".[long-term-memory]"',
            file=sys.stderr,
        )
        return 1

    try:
        schema_sql = schema_path().read_text(encoding="utf-8")
    except OSError:
        schema_sql = SCHEMA_SQL

    try:
        with psycopg.connect(config.database_url) as conn:
            with conn.cursor() as cursor:
                cursor.execute(schema_sql)
            conn.commit()
    except Exception as exc:
        print(f"Memory database initialization failed: {exc}", file=sys.stderr)
        return 1

    print(f"Memory database initialized successfully using schema {schema_path()}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


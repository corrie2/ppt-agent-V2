from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "init_memory_db.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("init_memory_db_test_module", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_main_reports_missing_database_url(monkeypatch, capsys):
    module = load_script_module()
    monkeypatch.delenv("PPT_AGENT_MEMORY_DATABASE_URL", raising=False)

    assert module.main() == 1

    captured = capsys.readouterr()
    assert "PPT_AGENT_MEMORY_DATABASE_URL is not set" in captured.err
    assert "Set PPT_AGENT_MEMORY_DATABASE_URL" in captured.err


def test_main_reports_missing_psycopg(monkeypatch, capsys):
    module = load_script_module()
    monkeypatch.setenv("PPT_AGENT_MEMORY_DATABASE_URL", "postgresql://example")
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("missing psycopg")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    assert module.main() == 1

    captured = capsys.readouterr()
    assert "psycopg is not installed" in captured.err
    assert 'pip install -e ".[long-term-memory]"' in captured.err


def test_main_falls_back_to_packaged_schema_when_project_schema_file_is_missing(monkeypatch, tmp_path):
    module = load_script_module()
    monkeypatch.setenv("PPT_AGENT_MEMORY_DATABASE_URL", "postgresql://example")
    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, sql):
            captured["sql"] = sql

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            captured["committed"] = True

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=lambda database_url: Connection()))
    missing_schema = tmp_path / "missing.sql"
    monkeypatch.setattr(module, "SCHEMA_PATH", missing_schema)

    assert module.main() == 0

    assert "CREATE TABLE IF NOT EXISTS memory_projects" in captured["sql"]
    assert captured["committed"] is True


def test_main_reports_database_connection_or_sql_failure(monkeypatch, capsys, tmp_path):
    module = load_script_module()
    monkeypatch.setenv("PPT_AGENT_MEMORY_DATABASE_URL", "postgresql://example")
    schema_path = tmp_path / "schema.sql"
    schema_path.write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr(module, "SCHEMA_PATH", schema_path)

    def fail_connect(database_url):
        raise RuntimeError("connection refused")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(connect=fail_connect))

    assert module.main() == 1

    captured = capsys.readouterr()
    assert "Memory database initialization failed: connection refused" in captured.err

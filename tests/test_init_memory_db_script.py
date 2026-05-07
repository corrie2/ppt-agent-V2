import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def import_script_module():
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module("scripts.init_memory_db")


def test_schema_path_resolves_project_schema_file():
    module = import_script_module()

    path = module.schema_path()

    assert path.exists()
    assert path.name == "001_project_memory.sql"


def test_main_returns_nonzero_when_database_url_is_missing(monkeypatch):
    module = import_script_module()
    monkeypatch.delenv("PPT_AGENT_MEMORY_DATABASE_URL", raising=False)

    result = module.main()

    assert result != 0


def test_import_does_not_require_psycopg(monkeypatch):
    monkeypatch.delitem(sys.modules, "scripts.init_memory_db", raising=False)
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise AssertionError("psycopg should not be imported at module import time")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    module = import_script_module()

    assert hasattr(module, "main")
    assert hasattr(module, "schema_path")

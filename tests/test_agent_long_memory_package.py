from __future__ import annotations

import importlib
import sys


def test_agent_long_memory_imports_without_ppt_agent_storage(monkeypatch):
    for name in list(sys.modules):
        if name.startswith("agent_long_memory") or name.startswith("ppt_agent.storage.memory"):
            monkeypatch.delitem(sys.modules, name, raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("ppt_agent.storage"):
            raise AssertionError(f"{name} should not be required by standalone memory package")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    package = importlib.import_module("agent_long_memory")

    assert hasattr(package, "write_semantic_memory")
    assert hasattr(package, "resolve_memory_scope")


def test_agent_long_memory_schema_is_packaged():
    from agent_long_memory.schema import SCHEMA_SQL

    assert "CREATE TABLE IF NOT EXISTS memory_projects" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS memory_embeddings" in SCHEMA_SQL


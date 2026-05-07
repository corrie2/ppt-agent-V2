import importlib.util
import sys
from pathlib import Path


def test_debug_semantic_memory_import_has_no_runtime_side_effects(monkeypatch):
    monkeypatch.delitem(sys.modules, "scripts.debug_semantic_memory", raising=False)
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name in {"psycopg", "sentence_transformers"}:
            raise AssertionError(f"{name} should not be imported at debug script import time")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "debug_semantic_memory.py"
    spec = importlib.util.spec_from_file_location("debug_semantic_memory_under_test", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert hasattr(module, "main")

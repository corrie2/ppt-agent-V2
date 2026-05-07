import importlib
import sys

import pytest

from ppt_agent.storage.memory_config import MemoryConfig
from ppt_agent.storage.memory_db import CreateMemoryRecordInput, MemoryEmbedding, MemoryProject, MemoryRecord, MemorySearchResult


def test_semantic_memory_plain_import_does_not_require_database_or_model():
    module = importlib.import_module("ppt_agent.storage.semantic_memory")

    assert hasattr(module, "write_semantic_memory")


def test_semantic_memory_import_has_no_embedding_or_database_side_effects(monkeypatch):
    monkeypatch.delitem(sys.modules, "ppt_agent.storage.semantic_memory", raising=False)
    monkeypatch.delitem(sys.modules, "agent_long_memory.embeddings", raising=False)
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise AssertionError("semantic_memory import should not import psycopg")
        if name == "agent_long_memory.embeddings":
            raise AssertionError("semantic_memory import should not import embeddings")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    module = importlib.import_module("ppt_agent.storage.semantic_memory")

    assert hasattr(module, "create_semantic_memory_record")


def test_create_semantic_memory_record_runs_explicit_flow(monkeypatch):
    from ppt_agent.storage import semantic_memory

    calls = []
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)
    record_input = CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content")
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model-a")
    created_record = MemoryRecord(
        id="record-7",
        project_id="project-1",
        memory_type="preference",
        title="Title",
        content="Content",
        source_type=None,
        source_ref=None,
        module_path=None,
        tags=[],
        importance=0.5,
        confidence=0.5,
    )
    stored_embedding = MemoryEmbedding(id="embedding-1", record_id="record-7", embedding_model="model-a", embedding=[0.1] * 384)

    def fake_create_memory_record(project_arg, record_arg, *, config):
        calls.append(("create", project_arg, record_arg, config))
        return created_record

    def fake_embed_text(text, *, model_name):
        calls.append(("embed", text, model_name))
        return [0.1] * 384

    def fake_upsert_memory_embedding(record_id, *, embedding_model, embedding, config):
        calls.append(("upsert", record_id, embedding_model, embedding, config))
        return stored_embedding

    monkeypatch.setattr(semantic_memory, "create_memory_record", fake_create_memory_record)
    monkeypatch.setattr(semantic_memory, "upsert_memory_embedding", fake_upsert_memory_embedding)
    monkeypatch.setitem(sys.modules, "agent_long_memory.embeddings", type("Embeddings", (), {"embed_text": staticmethod(fake_embed_text)}))

    result = semantic_memory.create_semantic_memory_record(project, record_input, config=config)

    assert result == semantic_memory.SemanticMemoryWriteResult(project=project, record=created_record, embedding=stored_embedding)
    assert result.project == project
    assert result.record == created_record
    assert result.embedding == stored_embedding
    assert calls[0] == ("create", project, record_input, config)
    assert calls[1] == ("embed", "Title\nContent", "model-a")
    assert calls[2] == ("upsert", "record-7", "model-a", [0.1] * 384, config)


def test_write_semantic_memory_requires_database_url(tmp_path):
    from ppt_agent.storage import semantic_memory

    config = MemoryConfig(enabled=False, database_url=None, embedding_model="model-a")
    record = CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content")

    with pytest.raises(ValueError, match="PPT_AGENT_MEMORY_DATABASE_URL is required"):
        semantic_memory.write_semantic_memory(tmp_path, record, config=config)


def test_write_semantic_memory_without_embedding(monkeypatch, tmp_path):
    from ppt_agent.storage import semantic_memory

    calls = []
    config = MemoryConfig(enabled=False, database_url="postgresql://example", embedding_model="model-a")
    project = MemoryProject(id="project-1", name="repo", root_path=str(tmp_path), git_remote=None)
    created_record = MemoryRecord(
        id="record-7",
        project_id="project-1",
        memory_type="preference",
        title="Title",
        content="Content",
        source_type=None,
        source_ref=None,
        module_path=None,
        tags=[],
        importance=0.5,
        confidence=0.5,
    )
    record = CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content")

    monkeypatch.setattr(semantic_memory, "resolve_project_scope", lambda workspace: calls.append(("scope", workspace)) or "scope")
    monkeypatch.setattr(semantic_memory, "ensure_memory_project", lambda scope, *, config: calls.append(("project", scope, config)) or project)
    monkeypatch.setattr(
        semantic_memory,
        "create_memory_record",
        lambda project_arg, record_arg, *, config: calls.append(("record", project_arg, record_arg, config)) or created_record,
    )

    def fail_upsert(*args, **kwargs):
        raise AssertionError("upsert_memory_embedding should not be called when create_embedding=False")

    def fail_embed_text(*args, **kwargs):
        raise AssertionError("embed_text should not be called when create_embedding=False")

    monkeypatch.setattr(semantic_memory, "upsert_memory_embedding", fail_upsert)
    monkeypatch.setitem(sys.modules, "agent_long_memory.embeddings", type("Embeddings", (), {"embed_text": staticmethod(fail_embed_text)}))

    result = semantic_memory.write_semantic_memory(tmp_path, record, config=config, create_embedding=False)

    assert result == semantic_memory.SemanticMemoryWriteResult(project=project, record=created_record, embedding=None)
    assert calls == [
        ("scope", tmp_path),
        ("project", "scope", config),
        ("record", project, record, config),
    ]


def test_write_semantic_memory_with_embedding(monkeypatch, tmp_path):
    from ppt_agent.storage import semantic_memory

    calls = []
    config = MemoryConfig(enabled=False, database_url="postgresql://example", embedding_model="model-a")
    project = MemoryProject(id="project-1", name="repo", root_path=str(tmp_path), git_remote=None)
    record = CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content")
    created_record = MemoryRecord(
        id="record-7",
        project_id="project-1",
        memory_type="preference",
        title="Title",
        content="Content",
        source_type=None,
        source_ref=None,
        module_path=None,
        tags=[],
        importance=0.5,
        confidence=0.5,
    )
    stored_embedding = MemoryEmbedding(id="embedding-1", record_id="record-7", embedding_model="model-a", embedding=[0.2] * 384)

    monkeypatch.setattr(semantic_memory, "resolve_project_scope", lambda workspace: calls.append(("scope", workspace)) or "scope")
    monkeypatch.setattr(semantic_memory, "ensure_memory_project", lambda scope, *, config: calls.append(("project", scope, config)) or project)
    monkeypatch.setattr(
        semantic_memory,
        "create_memory_record",
        lambda project_arg, record_arg, *, config: calls.append(("record", project_arg, record_arg, config)) or created_record,
    )

    def fake_embed_text(text, *, model_name):
        calls.append(("embed", text, model_name))
        return [0.2] * 384

    def fake_upsert(record_id, *, embedding_model, embedding, config):
        calls.append(("upsert", record_id, embedding_model, embedding, config))
        return stored_embedding

    monkeypatch.setitem(sys.modules, "agent_long_memory.embeddings", type("Embeddings", (), {"embed_text": staticmethod(fake_embed_text)}))
    monkeypatch.setattr(semantic_memory, "upsert_memory_embedding", fake_upsert)

    result = semantic_memory.write_semantic_memory(tmp_path, record, config=config)

    assert result == semantic_memory.SemanticMemoryWriteResult(project=project, record=created_record, embedding=stored_embedding)
    assert calls == [
        ("scope", tmp_path),
        ("project", "scope", config),
        ("record", project, record, config),
        ("embed", "preference\nTitle\nContent", "model-a"),
        ("upsert", "record-7", "model-a", [0.2] * 384, config),
    ]


def test_write_semantic_memory_loads_config_when_missing(monkeypatch, tmp_path):
    from ppt_agent.storage import semantic_memory

    config = MemoryConfig(enabled=False, database_url="postgresql://example", embedding_model="model-a")
    project = MemoryProject(id="project-1", name="repo", root_path=str(tmp_path), git_remote=None)
    record = CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content")
    created_record = MemoryRecord(
        id="record-7",
        project_id="project-1",
        memory_type="preference",
        title="Title",
        content="Content",
        source_type=None,
        source_ref=None,
        module_path=None,
        tags=[],
        importance=0.5,
        confidence=0.5,
    )

    monkeypatch.setattr(semantic_memory, "load_memory_config", lambda: config)
    monkeypatch.setattr(semantic_memory, "resolve_project_scope", lambda workspace: "scope")
    monkeypatch.setattr(semantic_memory, "ensure_memory_project", lambda scope, *, config: project)
    monkeypatch.setattr(semantic_memory, "create_memory_record", lambda project, record, *, config: created_record)

    result = semantic_memory.write_semantic_memory(tmp_path, record, create_embedding=False)

    assert result.project == project
    assert result.record == created_record
    assert result.embedding is None


def test_search_semantic_memory_rejects_blank_query(tmp_path):
    from ppt_agent.storage import semantic_memory

    config = MemoryConfig(enabled=False, database_url="postgresql://example", embedding_model="model-a")

    with pytest.raises(ValueError, match="query must be non-empty"):
        semantic_memory.search_semantic_memory(tmp_path, "   ", config=config)


def test_search_semantic_memory_requires_database_url(tmp_path):
    from ppt_agent.storage import semantic_memory

    config = MemoryConfig(enabled=False, database_url=None, embedding_model="model-a")

    with pytest.raises(ValueError, match="PPT_AGENT_MEMORY_DATABASE_URL is required"):
        semantic_memory.search_semantic_memory(tmp_path, "query", config=config)


def test_search_semantic_memory_loads_config_when_missing(monkeypatch, tmp_path):
    from ppt_agent.storage import semantic_memory

    config = MemoryConfig(enabled=False, database_url="postgresql://example", embedding_model="model-a")
    project = MemoryProject(id="project-1", name="repo", root_path=str(tmp_path), git_remote=None)

    monkeypatch.setattr(semantic_memory, "load_memory_config", lambda: config)
    monkeypatch.setattr(semantic_memory, "resolve_project_scope", lambda workspace: "scope")
    monkeypatch.setattr(semantic_memory, "ensure_memory_project", lambda scope, *, config: project)
    monkeypatch.setitem(sys.modules, "agent_long_memory.embeddings", type("Embeddings", (), {"embed_text": staticmethod(lambda query, *, model_name: [0.1] * 384)}))
    monkeypatch.setattr(semantic_memory, "search_memory_records_by_embedding", lambda *args, **kwargs: [])

    assert semantic_memory.search_semantic_memory(tmp_path, "query") == []


def test_search_semantic_memory_runs_explicit_search_flow(monkeypatch, tmp_path):
    from ppt_agent.storage import semantic_memory

    calls = []
    config = MemoryConfig(enabled=False, database_url="postgresql://example", embedding_model="model-a")
    project = MemoryProject(id="project-1", name="repo", root_path=str(tmp_path), git_remote=None)
    record = MemoryRecord(
        id="record-7",
        project_id="project-1",
        memory_type="preference",
        title="Title",
        content="Content",
        source_type=None,
        source_ref=None,
        module_path=None,
        tags=[],
        importance=0.5,
        confidence=0.5,
    )
    expected = [MemorySearchResult(record=record, similarity=0.9, embedding_model="model-a")]

    monkeypatch.setattr(semantic_memory, "resolve_project_scope", lambda workspace: calls.append(("scope", workspace)) or "scope")
    monkeypatch.setattr(semantic_memory, "ensure_memory_project", lambda scope, *, config: calls.append(("project", scope, config)) or project)

    def fake_embed_text(query, *, model_name):
        calls.append(("embed", query, model_name))
        return [0.2] * 384

    def fake_search(project_arg, *, query_embedding, embedding_model, memory_types, limit, config):
        calls.append(("search", project_arg, query_embedding, embedding_model, memory_types, limit, config))
        return expected

    monkeypatch.setitem(sys.modules, "agent_long_memory.embeddings", type("Embeddings", (), {"embed_text": staticmethod(fake_embed_text)}))
    monkeypatch.setattr(semantic_memory, "search_memory_records_by_embedding", fake_search)

    result = semantic_memory.search_semantic_memory(
        tmp_path,
        "query text",
        config=config,
        memory_types=["preference"],
        limit=11,
    )

    assert result == expected
    assert calls == [
        ("scope", tmp_path),
        ("project", "scope", config),
        ("embed", "query text", "model-a"),
        ("search", project, [0.2] * 384, "model-a", ["preference"], 11, config),
    ]


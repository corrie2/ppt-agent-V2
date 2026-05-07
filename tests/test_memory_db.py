from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest

from ppt_agent.storage.memory_config import MemoryConfig
from ppt_agent.storage.memory_db import CreateMemoryRecordInput
from ppt_agent.storage.memory_scope import ProjectScope


def test_memory_db_dataclass_defaults():
    from ppt_agent.storage.memory_db import CreateMemoryRecordInput, MemoryProject, MemoryRecord, MemorySearchResult

    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)
    record_input = CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content")
    record = MemoryRecord(
        id="record-1",
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

    assert project.root_path == "E:/repo"
    assert record_input.source_type is None
    assert record_input.tags is None
    assert record_input.importance == 0.5
    assert record_input.confidence == 0.5
    assert record.id == "record-1"
    assert MemorySearchResult(record=record, similarity=0.9, embedding_model="model").embedding_model == "model"


def test_format_pgvector_literal():
    from ppt_agent.storage.memory_db import _format_pgvector_literal

    literal = _format_pgvector_literal([1, 2.5, -3])

    assert literal == "[1.0,2.5,-3.0]"
    assert " " not in literal
    assert "{" not in literal
    assert "}" not in literal


def test_memory_db_import_does_not_require_psycopg(monkeypatch):
    monkeypatch.delitem(sys.modules, "ppt_agent.storage.memory_db", raising=False)
    monkeypatch.delitem(sys.modules, "psycopg", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise AssertionError("psycopg should not be imported at module import time")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    module = importlib.import_module("ppt_agent.storage.memory_db")

    assert hasattr(module, "PostgresMemoryStore")


def test_memory_store_from_config_requires_database_url():
    from ppt_agent.storage.memory_db import MemoryDbError, PostgresMemoryStore

    config = MemoryConfig(enabled=True, database_url=None, embedding_model="model")

    with pytest.raises(MemoryDbError, match="PPT_AGENT_MEMORY_DATABASE_URL"):
        PostgresMemoryStore.from_config(config)


def test_connect_memory_db_requires_database_url():
    from ppt_agent.storage.memory_db import connect_memory_db

    config = MemoryConfig(enabled=True, database_url=None, embedding_model="model")

    with pytest.raises(ValueError, match="PPT_AGENT_MEMORY_DATABASE_URL is required"):
        connect_memory_db(config)


def test_connect_memory_db_reports_missing_psycopg(monkeypatch):
    from ppt_agent.storage.memory_db import connect_memory_db

    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("missing psycopg")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    with pytest.raises(RuntimeError, match='pip install -e ".\\[memory\\]"'):
        connect_memory_db(config)


def test_connect_memory_db_returns_psycopg_connection_without_schema_init(monkeypatch):
    from ppt_agent.storage.memory_db import connect_memory_db

    calls = {}
    connection = object()

    class Psycopg:
        @staticmethod
        def connect(database_url):
            calls["database_url"] = database_url
            return connection

    monkeypatch.setitem(sys.modules, "psycopg", Psycopg)
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    assert connect_memory_db(config) is connection
    assert calls == {"database_url": "postgresql://example"}


def test_ensure_memory_project_upserts_by_root_path(monkeypatch, tmp_path):
    from ppt_agent.storage.memory_db import MemoryProject, ensure_memory_project

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return ("project-1", "repo", str(tmp_path), "git@example.com:test/repo.git")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            captured["committed"] = True

    monkeypatch.setitem(ensure_memory_project.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")
    scope = ProjectScope(name="repo", root_path=tmp_path, git_remote="git@example.com:test/repo.git")

    project = ensure_memory_project(scope, config=config)

    query = " ".join(captured["query"].split())
    assert "INSERT INTO memory_projects" in query
    assert "ON CONFLICT (root_path)" in query
    assert "DO UPDATE SET name = EXCLUDED.name" in query
    assert "git_remote = EXCLUDED.git_remote" in query
    assert "updated_at = now()" in query
    assert "RETURNING id, name, root_path, git_remote" in query
    assert captured["params"] == ("repo", str(tmp_path), "git@example.com:test/repo.git")
    assert captured["committed"] is True
    assert project == MemoryProject(
        id="project-1",
        name="repo",
        root_path=str(tmp_path),
        git_remote="git@example.com:test/repo.git",
    )


def test_memory_store_reports_missing_psycopg(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryDbError, PostgresMemoryStore

    monkeypatch.delitem(sys.modules, "psycopg", raising=False)
    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("missing psycopg")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    store = PostgresMemoryStore("postgresql://example")

    with pytest.raises(MemoryDbError, match='pip install -e ".\\[memory\\]"'):
        store._connect()


def test_search_by_embedding_filters_by_project_before_vector_order(monkeypatch):
    from ppt_agent.storage.memory_db import PostgresMemoryStore

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    store = PostgresMemoryStore("postgresql://example")
    monkeypatch.setattr(store, "_connect", lambda: Connection())

    assert store.search_by_embedding(project_id="project-1", embedding=[0.1, 0.2], memory_type="preference") == []

    query = " ".join(captured["query"].split())
    assert "WHERE r.project_id = %s" in query
    assert "AND r.memory_type = %s" in query
    assert "AND r.superseded_by IS NULL" in query
    assert "AND (r.valid_until IS NULL OR r.valid_until > now())" in query
    assert "ORDER BY e.embedding <=> %s::vector" in query
    assert captured["params"] == ["[0.1,0.2]", "project-1", "preference", "[0.1,0.2]", 10]


def test_search_memory_records_by_embedding_validates_inputs():
    from ppt_agent.storage.memory_db import MemoryProject, search_memory_records_by_embedding

    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)

    with pytest.raises(ValueError, match="project.id must be non-empty"):
        search_memory_records_by_embedding(
            MemoryProject(id="", name="repo", root_path="E:/repo", git_remote=None),
            query_embedding=[0.1] * 384,
            embedding_model="model",
            config=config,
        )
    with pytest.raises(ValueError, match="query_embedding must be non-empty"):
        search_memory_records_by_embedding(project, query_embedding=[], embedding_model="model", config=config)
    with pytest.raises(ValueError, match="query_embedding must contain exactly 384 values"):
        search_memory_records_by_embedding(project, query_embedding=[0.1], embedding_model="model", config=config)
    with pytest.raises(ValueError, match="embedding_model must be non-empty"):
        search_memory_records_by_embedding(project, query_embedding=[0.1] * 384, embedding_model="", config=config)


def test_search_memory_records_by_embedding_filters_and_clamps_limit(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryProject, MemoryRecord, MemorySearchResult, search_memory_records_by_embedding

    captured = {}
    query_embedding = [0.2] * 384

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchall(self):
            return [
                (
                    "record-7",
                    "project-1",
                    "preference",
                    "Title",
                    "Content",
                    None,
                    None,
                    None,
                    ["tag"],
                    0.3,
                    0.4,
                    0.88,
                    "model-a",
                )
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(search_memory_records_by_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model-a")
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)

    result = search_memory_records_by_embedding(
        project,
        query_embedding=query_embedding,
        embedding_model="model-a",
        memory_types=["preference"],
        limit=500,
        config=config,
    )

    assert result == [
        MemorySearchResult(
            record=MemoryRecord(
                id="record-7",
                project_id="project-1",
                memory_type="preference",
                title="Title",
                content="Content",
                source_type=None,
                source_ref=None,
                module_path=None,
                tags=["tag"],
                importance=0.3,
                confidence=0.4,
            ),
            similarity=0.88,
            embedding_model="model-a",
        )
    ]
    query = " ".join(captured["query"].split())
    assert "FROM memory_records r" in query
    assert "JOIN memory_embeddings e ON e.record_id = r.id" in query
    assert "WHERE r.project_id = %s" in query
    assert "AND e.embedding_model = %s" in query
    assert "AND r.memory_type = ANY(%s)" in query
    assert "AND r.superseded_by IS NULL" in query
    assert "AND (r.valid_until IS NULL OR r.valid_until > now())" in query
    assert "1 - (e.embedding <=> %s::vector) AS similarity" in query
    assert "ORDER BY e.embedding <=> %s::vector" in query
    assert captured["params"] == (
        "[" + ",".join(["0.2"] * 384) + "]",
        "project-1",
        "model-a",
        ["preference"],
        "[" + ",".join(["0.2"] * 384) + "]",
        20,
    )
    assert isinstance(captured["params"][0], str)
    assert isinstance(captured["params"][4], str)
    assert captured["params"][0] == captured["params"][4]


def test_search_memory_records_by_embedding_does_not_generate_or_write(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryProject, search_memory_records_by_embedding

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            assert query.lstrip().upper().startswith("SELECT")
            assert "INSERT" not in query.upper()
            assert "UPDATE" not in query.upper()

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(search_memory_records_by_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model-a")
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)

    assert (
        search_memory_records_by_embedding(
            project,
            query_embedding=[0.3] * 384,
            embedding_model="model-a",
            config=config,
        )
        == []
    )


def test_add_embedding_uses_upsert_without_pgvector_dependency(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryEmbedding, PostgresMemoryStore

    captured = {}
    embedding = [0.1] * 384

    def fake_upsert_memory_embedding(record_id, *, embedding_model, embedding, config):
        captured["record_id"] = record_id
        captured["embedding_model"] = embedding_model
        captured["embedding"] = embedding
        captured["config"] = config
        return MemoryEmbedding(id="embedding-1", record_id=record_id, embedding_model=embedding_model, embedding=embedding)

    store = PostgresMemoryStore("postgresql://example")
    monkeypatch.setitem(PostgresMemoryStore.add_embedding.__globals__, "upsert_memory_embedding", fake_upsert_memory_embedding)

    store.add_embedding("1", "model", embedding)

    assert captured["record_id"] == "1"
    assert captured["embedding_model"] == "model"
    assert captured["embedding"] == embedding
    assert captured["config"].database_url == "postgresql://example"


def test_add_record_uses_create_memory_record_input_defaults(monkeypatch):
    from ppt_agent.storage.memory_db import CreateMemoryRecordInput, MemoryRecord, PostgresMemoryStore

    captured = {}

    def fake_create_memory_record(project, record, *, config):
        captured["project"] = project
        captured["record"] = record
        captured["config"] = config
        return MemoryRecord(
            id="record-7",
            project_id=project.id,
            memory_type=record.memory_type,
            title=record.title,
            content=record.content,
            source_type=record.source_type,
            source_ref=record.source_ref,
            module_path=record.module_path,
            tags=record.tags or [],
            importance=record.importance,
            confidence=record.confidence,
    )

    store = PostgresMemoryStore("postgresql://example")
    monkeypatch.setitem(PostgresMemoryStore.add_record.__globals__, "create_memory_record", fake_create_memory_record)

    record_id = store.add_record(
        "project-1",
        CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content"),
    )

    assert record_id == "record-7"
    assert captured["project"].id == "project-1"
    assert captured["record"].tags is None
    assert captured["record"].importance == 0.5
    assert captured["record"].confidence == 0.5
    assert captured["config"].database_url == "postgresql://example"


def test_create_memory_record_inserts_record_without_embedding(monkeypatch):
    from ppt_agent.storage.memory_db import CreateMemoryRecordInput, MemoryProject, MemoryRecord, create_memory_record

    captured = {}

    def fail_upsert_memory_embedding(*args, **kwargs):
        raise AssertionError("create_memory_record must not create memory embeddings")

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return (
                "record-7",
                "project-1",
                "preference",
                "Title",
                "Content",
                None,
                None,
                "src/module.py",
                [],
                0.5,
                0.4,
            )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            captured["committed"] = True

    monkeypatch.setitem(create_memory_record.__globals__, "connect_memory_db", lambda config: Connection())
    monkeypatch.setitem(create_memory_record.__globals__, "upsert_memory_embedding", fail_upsert_memory_embedding)
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)
    record = CreateMemoryRecordInput(
        memory_type="preference",
        title="Title",
        content="Content",
        module_path="src/module.py",
        tags=None,
        confidence=0.4,
    )

    result = create_memory_record(project, record, config=config)

    assert result == MemoryRecord(
        id="record-7",
        project_id="project-1",
        memory_type="preference",
        title="Title",
        content="Content",
        source_type=None,
        source_ref=None,
        module_path="src/module.py",
        tags=[],
        importance=0.5,
        confidence=0.4,
    )
    query = " ".join(captured["query"].split())
    assert "INSERT INTO memory_records" in query
    assert "memory_embeddings" not in query
    assert "embedding" not in query.lower()
    assert captured["params"] == (
        "project-1",
        "preference",
        "Title",
        "Content",
        None,
        None,
        "src/module.py",
        [],
        0.5,
        0.4,
    )
    assert captured["committed"] is True


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (CreateMemoryRecordInput(memory_type="", title="Title", content="Content"), "memory_type must be non-empty"),
        (CreateMemoryRecordInput(memory_type="preference", title="", content="Content"), "title must be non-empty"),
        (CreateMemoryRecordInput(memory_type="preference", title="Title", content=""), "content must be non-empty"),
        (
            CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content", importance=-0.1),
            "importance must be between 0 and 1",
        ),
        (
            CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content", importance=1.1),
            "importance must be between 0 and 1",
        ),
        (
            CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content", confidence=-0.1),
            "confidence must be between 0 and 1",
        ),
        (
            CreateMemoryRecordInput(memory_type="preference", title="Title", content="Content", confidence=1.1),
            "confidence must be between 0 and 1",
        ),
    ],
)
def test_create_memory_record_validates_input(record, message):
    from ppt_agent.storage.memory_db import MemoryProject, create_memory_record

    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)

    with pytest.raises(ValueError, match=message):
        create_memory_record(project, record, config=config)


def test_get_memory_record_returns_record(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryRecord, get_memory_record

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return (
                "record-7",
                "project-1",
                "preference",
                "Title",
                "Content",
                "user_feedback",
                "chat",
                "src/module.py",
                ["tag"],
                0.4,
                0.5,
            )

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(get_memory_record.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    assert get_memory_record("record-7", project_id="project-1", config=config) == MemoryRecord(
        id="record-7",
        project_id="project-1",
        memory_type="preference",
        title="Title",
        content="Content",
        source_type="user_feedback",
        source_ref="chat",
        module_path="src/module.py",
        tags=["tag"],
        importance=0.4,
        confidence=0.5,
    )

    query = " ".join(captured["query"].split())
    assert "FROM memory_records" in query
    assert "WHERE id = %s" in query
    assert "memory_embeddings" not in query
    assert "JOIN" not in query.upper()
    assert captured["params"] == ("record-7", "project-1")


def test_get_memory_record_requires_project_id(monkeypatch):
    from ppt_agent.storage.memory_db import get_memory_record

    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    with pytest.raises(TypeError):
        get_memory_record("record-7", config=config)
    with pytest.raises(ValueError, match="project_id must be non-empty"):
        get_memory_record("record-7", project_id="", config=config)


def test_get_memory_record_returns_none_when_missing(monkeypatch):
    from ppt_agent.storage.memory_db import get_memory_record

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            pass

        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(get_memory_record.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    assert get_memory_record("record-999", project_id="project-1", config=config) is None


def test_list_memory_records_filters_by_project_and_valid_records(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryProject, MemoryRecord, list_memory_records

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchall(self):
            return [
                (
                    "record-7",
                    "project-1",
                    "preference",
                    "Title",
                    "Content",
                    None,
                    None,
                    None,
                    ["tag"],
                    0.3,
                    0.4,
                )
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(list_memory_records.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)

    assert list_memory_records(project, config=config) == [
        MemoryRecord(
            id="record-7",
            project_id="project-1",
            memory_type="preference",
            title="Title",
            content="Content",
            source_type=None,
            source_ref=None,
            module_path=None,
            tags=["tag"],
            importance=0.3,
            confidence=0.4,
        )
    ]

    query = " ".join(captured["query"].split())
    assert "WHERE project_id = %s" in query
    assert "superseded_by IS NULL" in query
    assert "(valid_until IS NULL OR valid_until > now())" in query
    assert "ORDER BY created_at DESC" in query
    assert "LIMIT %s" in query
    assert "memory_embeddings" not in query
    assert captured["params"] == ("project-1", 20)


def test_list_memory_records_filters_by_memory_types_and_clamps_limit(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryProject, list_memory_records

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(list_memory_records.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")
    project = MemoryProject(id="project-1", name="repo", root_path="E:/repo", git_remote=None)

    assert list_memory_records(project, memory_types=["preference", "fact"], limit=500, config=config) == []

    query = " ".join(captured["query"].split())
    assert "WHERE project_id = %s" in query
    assert "AND memory_type = ANY(%s)" in query
    assert captured["params"] == ("project-1", ["preference", "fact"], 100)


def test_list_memory_records_uses_project_id_as_filter_parameter(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryProject, list_memory_records

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchall(self):
            return []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(list_memory_records.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")
    project = MemoryProject(id="project-abc", name="repo", root_path="E:/repo", git_remote=None)

    list_memory_records(project, config=config)

    assert "WHERE project_id = %s" in " ".join(captured["query"].split())
    assert captured["params"][0] == "project-abc"


def test_upsert_memory_embedding_validates_inputs():
    from ppt_agent.storage.memory_db import upsert_memory_embedding

    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    with pytest.raises(ValueError, match="record_id must be non-empty"):
        upsert_memory_embedding("", embedding_model="model", embedding=[0.1] * 384, config=config)
    with pytest.raises(ValueError, match="embedding_model must be non-empty"):
        upsert_memory_embedding("record-1", embedding_model="", embedding=[0.1] * 384, config=config)
    with pytest.raises(ValueError, match="embedding must be non-empty"):
        upsert_memory_embedding("record-1", embedding_model="model", embedding=[], config=config)
    with pytest.raises(ValueError, match="embedding must contain exactly 384 values"):
        upsert_memory_embedding("record-1", embedding_model="model", embedding=[0.1, 0.2], config=config)


def test_upsert_memory_embedding_upserts_and_returns_embedding(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryEmbedding, upsert_memory_embedding

    captured = {}
    embedding = [0.25] * 384

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return ("embedding-1", "record-7", "model", "[0.25,0.5]", "created")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            captured["committed"] = True

    monkeypatch.setitem(upsert_memory_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    result = upsert_memory_embedding("record-7", embedding_model="model", embedding=embedding, config=config)

    query = " ".join(captured["query"].split())
    assert "INSERT INTO memory_embeddings" in query
    assert "VALUES (%s, %s, %s::vector)" in query
    assert "ON CONFLICT (record_id, embedding_model) DO UPDATE" in query
    assert "SET embedding = EXCLUDED.embedding" in query
    assert "RETURNING id, record_id, embedding_model, embedding, created_at" in query
    assert captured["params"] == ("record-7", "model", "[" + ",".join(["0.25"] * 384) + "]")
    assert isinstance(captured["params"][2], str)
    assert captured["committed"] is True
    assert result == MemoryEmbedding(
        id="embedding-1",
        record_id="record-7",
        embedding_model="model",
        embedding=[0.25, 0.5],
        created_at="created",
    )


def test_upsert_memory_embedding_uses_record_id_and_embedding_model_conflict_key(monkeypatch):
    from ppt_agent.storage.memory_db import upsert_memory_embedding

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return ("embedding-1", "record-7", "model", [0.1] * 384, None)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            pass

    monkeypatch.setitem(upsert_memory_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    upsert_memory_embedding("record-7", embedding_model="model", embedding=[0.1] * 384, config=config)

    query = " ".join(captured["query"].split())
    assert "ON CONFLICT" in query
    assert "record_id" in query
    assert "embedding_model" in query
    assert "ON CONFLICT (record_id, embedding_model)" in query
    assert captured["params"][0] == "record-7"
    assert captured["params"][1] == "model"


def test_upsert_memory_embedding_does_not_call_embeddings_module(monkeypatch):
    from ppt_agent.storage.memory_db import upsert_memory_embedding

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            pass

        def fetchone(self):
            return ("embedding-1", "record-7", "model", [0.1] * 384, None)

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

        def commit(self):
            pass

    monkeypatch.setitem(upsert_memory_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    result = upsert_memory_embedding("record-7", embedding_model="model", embedding=[0.1] * 384, config=config)

    assert result.record_id == "record-7"


def test_get_memory_embedding_returns_embedding(monkeypatch):
    from ppt_agent.storage.memory_db import MemoryEmbedding, get_memory_embedding

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return ("embedding-1", "record-7", "model", [1, 2], "created")

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(get_memory_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    assert get_memory_embedding("record-7", embedding_model="model", project_id="project-1", config=config) == MemoryEmbedding(
        id="embedding-1",
        record_id="record-7",
        embedding_model="model",
        embedding=[1.0, 2.0],
        created_at="created",
    )
    query = " ".join(captured["query"].split())
    assert "FROM memory_embeddings e" in query
    assert "WHERE e.record_id = %s AND e.embedding_model = %s" in query
    assert "ORDER BY" not in query
    assert captured["params"] == ("record-7", "model", "project-1")


def test_get_memory_embedding_returns_none_when_missing(monkeypatch):
    from ppt_agent.storage.memory_db import get_memory_embedding

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            pass

        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(get_memory_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    assert get_memory_embedding("record-7", embedding_model="model", project_id="project-1", config=config) is None


def test_get_memory_embedding_uses_record_id_embedding_model_and_project_parameters(monkeypatch):
    from ppt_agent.storage.memory_db import get_memory_embedding

    captured = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, query, params):
            captured["query"] = query
            captured["params"] = params

        def fetchone(self):
            return None

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def cursor(self):
            return Cursor()

    monkeypatch.setitem(get_memory_embedding.__globals__, "connect_memory_db", lambda config: Connection())
    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    assert get_memory_embedding("record-42", embedding_model="model-b", project_id="project-1", config=config) is None

    query = " ".join(captured["query"].split())
    assert "JOIN memory_records r ON r.id = e.record_id" in query
    assert "WHERE e.record_id = %s AND e.embedding_model = %s AND r.project_id = %s" in query
    assert captured["params"] == ("record-42", "model-b", "project-1")


def test_get_memory_embedding_requires_project_id(monkeypatch):
    from ppt_agent.storage.memory_db import get_memory_embedding

    config = MemoryConfig(enabled=True, database_url="postgresql://example", embedding_model="model")

    with pytest.raises(TypeError):
        get_memory_embedding("record-7", embedding_model="model-b", config=config)
    with pytest.raises(ValueError, match="project_id must be non-empty"):
        get_memory_embedding("record-7", embedding_model="model-b", project_id="", config=config)

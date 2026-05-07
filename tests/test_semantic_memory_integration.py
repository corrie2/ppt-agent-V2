from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from ppt_agent.storage.memory_config import MemoryConfig
from ppt_agent.storage.memory_db import CreateMemoryRecordInput, get_memory_embedding, search_memory_records_by_embedding, upsert_memory_embedding
from ppt_agent.storage.semantic_memory import write_semantic_memory


RUN_ENV = "PPT_AGENT_RUN_MEMORY_INTEGRATION"
DATABASE_ENV = "PPT_AGENT_MEMORY_DATABASE_URL"
EMBEDDING_MODEL = "integration-test-vector"


pytestmark = pytest.mark.integration


def test_semantic_memory_real_postgres_pgvector_write_embed_and_search(tmp_path):
    if os.environ.get(RUN_ENV) != "1":
        pytest.skip(f"set {RUN_ENV}=1 to run real semantic memory integration tests")
    database_url = os.environ.get(DATABASE_ENV)
    if not database_url:
        pytest.skip(f"set {DATABASE_ENV} to run real semantic memory integration tests")

    psycopg = pytest.importorskip("psycopg")
    _ensure_schema(psycopg, database_url)

    config = MemoryConfig(enabled=True, database_url=database_url, embedding_model=EMBEDDING_MODEL)
    workspace = tmp_path / f"semantic-memory-integration-{uuid4().hex}"
    workspace.mkdir()
    memory_type = f"integration_{uuid4().hex}"
    title = "Semantic memory integration smoke"
    vector = _test_vector()
    project_id = None

    try:
        write_result = write_semantic_memory(
            workspace,
            CreateMemoryRecordInput(
                memory_type=memory_type,
                title=title,
                content="Verify semantic memory record writes, embedding writes, and pgvector retrieval.",
                source_type="integration_test",
                source_ref="pytest",
                tags=["integration"],
                importance=0.8,
                confidence=0.9,
            ),
            config=config,
            create_embedding=False,
        )
        project_id = write_result.project.id

        embedding = upsert_memory_embedding(
            write_result.record.id,
            embedding_model=EMBEDDING_MODEL,
            embedding=vector,
            config=config,
        )
        stored_embedding = get_memory_embedding(
            write_result.record.id,
            embedding_model=EMBEDDING_MODEL,
            project_id=write_result.project.id,
            config=config,
        )
        results = search_memory_records_by_embedding(
            write_result.project,
            query_embedding=vector,
            embedding_model=EMBEDDING_MODEL,
            memory_types=[memory_type],
            limit=3,
            config=config,
        )

        assert write_result.record.id
        assert write_result.record.memory_type == memory_type
        assert embedding.record_id == write_result.record.id
        assert stored_embedding is not None
        assert stored_embedding.record_id == write_result.record.id
        assert results
        assert results[0].record.id == write_result.record.id
        assert results[0].record.title == title
        assert results[0].embedding_model == EMBEDDING_MODEL
        assert results[0].similarity > 0.99
    finally:
        _delete_project(psycopg, database_url, project_id=project_id, root_path=workspace)


def _ensure_schema(psycopg, database_url: str) -> None:
    schema_path = Path(__file__).resolve().parents[1] / "db" / "schema" / "001_project_memory.sql"
    schema_sql = schema_path.read_text(encoding="utf-8")
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute(schema_sql)
        conn.commit()


def _delete_project(psycopg, database_url: str, *, project_id: str | None, root_path: Path) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cursor:
            if project_id:
                cursor.execute("DELETE FROM memory_projects WHERE id = %s", (project_id,))
            else:
                cursor.execute("DELETE FROM memory_projects WHERE root_path = %s", (str(root_path),))
        conn.commit()


def _test_vector() -> list[float]:
    return [1.0] + [0.0] * 383

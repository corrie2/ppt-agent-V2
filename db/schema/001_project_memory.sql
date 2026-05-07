-- Project-scoped long-term memory database schema.
-- This optional PostgreSQL store augments .ppt-agent/memory/*.json and *.jsonl files.
-- Local files remain the compatibility baseline.
-- Retrieval must filter by project_id before vector similarity search to avoid cross-project memory mixing.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS memory_projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    git_remote TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES memory_projects(id) ON DELETE CASCADE,
    memory_type TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT,
    module_path TEXT,
    tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    importance REAL NOT NULL DEFAULT 0.5 CHECK (importance >= 0 AND importance <= 1),
    confidence REAL NOT NULL DEFAULT 0.5 CHECK (confidence >= 0 AND confidence <= 1),
    valid_until TIMESTAMPTZ,
    superseded_by UUID REFERENCES memory_records(id) ON DELETE SET NULL,
    content_hash TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id UUID NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    embedding_model TEXT NOT NULL,
    embedding vector(384) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (record_id, embedding_model)
);

CREATE INDEX IF NOT EXISTS idx_memory_records_project_id
    ON memory_records (project_id);

CREATE INDEX IF NOT EXISTS idx_memory_records_memory_type
    ON memory_records (memory_type);

CREATE INDEX IF NOT EXISTS idx_memory_records_project_id_memory_type
    ON memory_records (project_id, memory_type);

CREATE INDEX IF NOT EXISTS idx_memory_records_module_path
    ON memory_records (module_path);

CREATE INDEX IF NOT EXISTS idx_memory_records_tags
    ON memory_records USING gin (tags);

CREATE INDEX IF NOT EXISTS idx_memory_records_metadata
    ON memory_records USING gin (metadata);

CREATE INDEX IF NOT EXISTS idx_memory_embeddings_hnsw_cosine
    ON memory_embeddings USING hnsw (embedding vector_cosine_ops);

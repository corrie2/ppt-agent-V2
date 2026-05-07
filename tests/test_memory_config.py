from ppt_agent.storage.memory_config import DEFAULT_EMBEDDING_MODEL, MemoryConfig, load_memory_config


def test_memory_config_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("PPT_AGENT_VECTOR_MEMORY", raising=False)
    monkeypatch.delenv("PPT_AGENT_MEMORY_DATABASE_URL", raising=False)
    monkeypatch.delenv("PPT_AGENT_EMBEDDING_MODEL", raising=False)

    config = load_memory_config()

    assert config == MemoryConfig(enabled=False, database_url=None, embedding_model=DEFAULT_EMBEDDING_MODEL)


def test_memory_config_enables_when_vector_memory_is_one(monkeypatch):
    monkeypatch.setenv("PPT_AGENT_VECTOR_MEMORY", "1")

    assert load_memory_config().enabled is True


def test_memory_config_disables_when_vector_memory_is_zero(monkeypatch):
    monkeypatch.setenv("PPT_AGENT_VECTOR_MEMORY", "0")

    assert load_memory_config().enabled is False


def test_memory_config_disables_when_vector_memory_is_false(monkeypatch):
    monkeypatch.setenv("PPT_AGENT_VECTOR_MEMORY", "false")

    assert load_memory_config().enabled is False


def test_memory_config_treats_empty_database_url_as_none(monkeypatch):
    monkeypatch.setenv("PPT_AGENT_MEMORY_DATABASE_URL", "")

    assert load_memory_config().database_url is None


def test_memory_config_reads_database_url(monkeypatch):
    database_url = "postgresql://ppt_agent:ppt_agent@localhost:54329/ppt_agent_memory"
    monkeypatch.setenv("PPT_AGENT_MEMORY_DATABASE_URL", database_url)

    assert load_memory_config().database_url == database_url


def test_memory_config_reads_custom_embedding_model(monkeypatch):
    monkeypatch.setenv("PPT_AGENT_EMBEDDING_MODEL", "custom-model")

    assert load_memory_config().embedding_model == "custom-model"

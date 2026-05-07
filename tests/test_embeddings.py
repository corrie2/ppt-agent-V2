import importlib
import sys
from types import ModuleType


def test_embeddings_import_does_not_import_sentence_transformers(monkeypatch):
    monkeypatch.delitem(sys.modules, "ppt_agent.storage.embeddings", raising=False)
    monkeypatch.delitem(sys.modules, "sentence_transformers", raising=False)

    original_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "sentence_transformers":
            raise AssertionError("sentence_transformers should not be imported at module import time")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    module = importlib.import_module("ppt_agent.storage.embeddings")

    assert hasattr(module, "load_embedding_model")


def test_load_embedding_model_imports_sentence_transformers_lazily(monkeypatch):
    from ppt_agent.storage import embeddings

    calls = []

    class FakeSentenceTransformer:
        def __init__(self, model_name):
            calls.append(model_name)

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    embeddings.load_embedding_model.cache_clear()

    first = embeddings.load_embedding_model("model-a")
    second = embeddings.load_embedding_model("model-a")

    assert first is second
    assert calls == ["model-a"]


def test_embed_texts_uses_cached_model(monkeypatch):
    from ppt_agent.storage import embeddings

    class FakeModel:
        def encode(self, texts, normalize_embeddings):
            assert texts == ["a", "b"]
            assert normalize_embeddings is True
            return [[1, 2], [3.5, 4]]

    monkeypatch.setattr(embeddings, "load_embedding_model", lambda model_name: FakeModel())

    assert embeddings.embed_texts(["a", "b"], model_name="model-a") == [[1.0, 2.0], [3.5, 4.0]]


def test_embed_text_returns_single_vector(monkeypatch):
    from ppt_agent.storage import embeddings

    monkeypatch.setattr(embeddings, "embed_texts", lambda texts, *, model_name: [[0.1, 0.2]])

    assert embeddings.embed_text("hello", model_name="model-a") == [0.1, 0.2]

"""Embedding providers (C5 ``EmbeddingProvider``): Ollama (default runtime) and a fake."""

from kb_providers.embedding.fake import FakeEmbeddingProvider
from kb_providers.embedding.ollama import OllamaEmbeddingProvider

__all__ = ["FakeEmbeddingProvider", "OllamaEmbeddingProvider"]

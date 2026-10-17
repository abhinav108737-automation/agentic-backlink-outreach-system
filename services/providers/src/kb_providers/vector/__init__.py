"""Vector providers (C5 `VectorProvider`). Qdrant is derived state: everything stored here can be
rebuilt from PostgreSQL (see `kb_core.db.vector_rebuild`)."""

from kb_providers.vector.embeddings import HashEmbeddingProvider, OllamaEmbeddingProvider
from kb_providers.vector.memory import InMemoryVectorProvider
from kb_providers.vector.qdrant import (
    REQUIRED_PAYLOAD_KEYS,
    InvalidPayload,
    QdrantVectorProvider,
    validate_payload,
)

__all__ = [
    "REQUIRED_PAYLOAD_KEYS",
    "HashEmbeddingProvider",
    "InMemoryVectorProvider",
    "InvalidPayload",
    "OllamaEmbeddingProvider",
    "QdrantVectorProvider",
    "validate_payload",
]

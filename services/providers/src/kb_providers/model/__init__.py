"""Model providers (C5 ``ModelProvider``): Ollama (default runtime) and a deterministic fake."""

from kb_providers.model.fake import FakeFailure, FakeModelProvider
from kb_providers.model.ollama import OllamaModelProvider

__all__ = ["FakeFailure", "FakeModelProvider", "OllamaModelProvider"]

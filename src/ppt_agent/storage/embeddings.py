"""Compatibility alias for workspace-scoped long-term memory embeddings."""

import sys

from agent_long_memory import embeddings as _module

sys.modules[__name__] = _module


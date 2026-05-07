"""Compatibility alias for workspace-scoped long-term semantic memory APIs."""

import sys

from agent_long_memory import semantic_memory as _module

sys.modules[__name__] = _module


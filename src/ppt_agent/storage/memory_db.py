"""Compatibility alias for workspace-scoped long-term memory database APIs."""

import sys

from agent_long_memory import memory_db as _module

sys.modules[__name__] = _module


"""Compatibility alias for workspace-scoped long-term memory scope helpers."""

import sys

from agent_long_memory import memory_scope as _module

sys.modules[__name__] = _module


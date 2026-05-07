"""Compatibility alias for workspace-scoped long-term memory config."""

import sys

from agent_long_memory import memory_config as _module

sys.modules[__name__] = _module


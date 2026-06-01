"""Progress callback for multi-agent pipeline."""

from contextvars import ContextVar
from typing import Callable, Optional

# Context-local callback for progress logging (thread-safe)
_progress_callback: ContextVar[Optional[Callable[[str], None]]] = ContextVar(
    "progress_callback", default=None
)


def set_progress_callback(callback: Optional[Callable[[str], None]]) -> None:
    """Set the progress callback function."""
    _progress_callback.set(callback)


def get_progress_callback() -> Optional[Callable[[str], None]]:
    """Get the current progress callback function."""
    return _progress_callback.get()


def emit_agent_progress(agent_name: str, message: str) -> None:
    """Emit a progress message with agent name."""
    cb = _progress_callback.get()
    if cb:
        cb(f"[{agent_name}] {message}")

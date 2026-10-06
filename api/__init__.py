"""Football Analytics AutoData HTTP API."""

from __future__ import annotations

from typing import Any

__all__ = ["API_TITLE", "app", "create_app", "run"]


def __getattr__(name: str) -> Any:
    """Lazy-load ``api.main`` so slim images can import ``api.portal`` / upload."""

    if name in __all__:
        from api import main as _main

        return getattr(_main, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

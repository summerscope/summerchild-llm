"""summerchild-agent — conversational risk-assessment quiz against the SSCS canonical rubric."""

from __future__ import annotations

import os as _os
from pathlib import Path as _Path

# Auto-load `.env` at first import of any submodule. Without this, env vars
# like ANTHROPIC_API_KEY / LOGFIRE_API_KEY only reach the process when
# something else (uvicorn, pytest) is configured to load them. Lifting it
# here makes every entry point — server, push scripts, tests — equally
# well-served. override=True so editing .env always wins after a restart.
try:
    from dotenv import load_dotenv as _load_dotenv

    for _candidate in [_Path(__file__).resolve()] + list(
        _Path(__file__).resolve().parents
    ):
        _env = (
            _candidate.parent / ".env" if _candidate.is_file() else _candidate / ".env"
        )
        if _env.is_file():
            _load_dotenv(_env, override=True)
            break
except ImportError:
    pass

# Anthropic provider eagerly validates ANTHROPIC_API_KEY at construction.
# Placeholder so module import succeeds even when no key is configured;
# real key (env var or .env) loaded above and wins via setdefault.
_os.environ.setdefault("ANTHROPIC_API_KEY", "placeholder-real-key-required-at-runtime")

__version__ = "0.1.0"

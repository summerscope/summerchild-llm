"""
Logfire instrumentation setup.

Configures Logfire if a token is present. With `include_content=False` on
PydanticAI instrumentation, prompts / completions / tool args / tool results
are scrubbed before they leave the agent process — that's the first layer
of the privacy posture.

When `LOGFIRE_TOKEN` isn't set, this module is a no-op: the agent runs
without external instrumentation and managed variables fall back to
hard-coded defaults from `managed_vars.py`.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def configure(*, service_name: str = "summerchild-agent") -> bool:
    """Configure Logfire + PydanticAI auto-instrumentation if a token is present.

    Returns True if Logfire is active. The server boots either way.
    """
    token = os.environ.get("LOGFIRE_TOKEN")
    if not token:
        log.warning(
            "LOGFIRE_TOKEN not set — running without Logfire instrumentation. "
            "Managed variables fall back to hard-coded defaults in managed_vars.py."
        )
        return False

    try:
        import logfire
        from pydantic_ai import InstrumentationSettings
    except ImportError as exc:
        log.warning("Logfire imports failed: %s. Running without instrumentation.", exc)
        return False

    logfire.configure(
        service_name=service_name,
        scrubbing=logfire.ScrubbingOptions(),  # default regex scrubber for secrets/PII
        # Token is read from LOGFIRE_TOKEN env var automatically.
    )
    # Content-stripping instrumentation — agent inputs/outputs are scrubbed
    # before reaching Logfire. The agent still sees full content to do its job.
    instr = InstrumentationSettings(include_content=False)
    logfire.instrument_pydantic_ai(instrumentation_settings=instr)

    log.info("Logfire instrumentation active for service=%s.", service_name)
    return True


def instrument_fastapi(app) -> None:
    """Attach FastAPI middleware instrumentation if Logfire is active."""
    try:
        import logfire

        logfire.instrument_fastapi(app)
    except Exception as exc:
        log.debug("FastAPI instrumentation skipped: %s", exc)

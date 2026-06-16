"""
Logfire instrumentation setup.

Tries `logfire.configure()` regardless of whether `LOGFIRE_TOKEN` is in env
— the SDK auto-discovers credentials from either the env var OR local
`~/.logfire` credentials placed there by `uv run logfire auth`. This means
dev (OAuth) and prod (write token) both work without code changes.

With `include_content=False` on PydanticAI instrumentation, prompts /
completions / tool args / tool results are scrubbed before they leave the
agent process — first layer of the privacy posture.

If Logfire isn't reachable at all (no creds anywhere), `logfire.configure()`
no-ops with a stderr notice and the agent still boots — we just don't get
traces.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def configure(*, service_name: str = "summerchild-agent") -> bool:
    """Configure Logfire + PydanticAI auto-instrumentation.

    Returns True if instrumentation was wired (regardless of whether the
    SDK actually found credentials — that's its own concern). The server
    boots either way.
    """
    try:
        import logfire
    except ImportError as exc:
        log.warning("Logfire imports failed: %s. Running without instrumentation.", exc)
        return False

    has_token = bool(os.environ.get("LOGFIRE_TOKEN"))
    auth_source = "LOGFIRE_TOKEN env var" if has_token else "local OAuth credentials (logfire auth)"

    try:
        logfire.configure(
            service_name=service_name,
            scrubbing=logfire.ScrubbingOptions(),  # default regex scrubber for secrets/PII
            # Enable managed-variables polling so `logfire.var(...)` resolves
            # against the remote value when one's published. Block-before-first
            # is off so module import doesn't hang waiting on the network on
            # cold starts; first resolve falls back to the local default until
            # the first refresh lands.
            variables=logfire.VariablesOptions(block_before_first_resolve=False),
            # Token is read from LOGFIRE_TOKEN env var automatically if present;
            # otherwise the SDK falls back to `~/.logfire` credentials.
        )
    except Exception as exc:  # noqa: BLE001 — broad: instrumentation must not crash boot
        log.warning(
            "logfire.configure() raised %s. Running without instrumentation.", exc
        )
        return False

    # Whether prompts / completions / tool args / tool results reach Logfire.
    # Default ON for dev (so you can actually read the conversation in traces);
    # set LOGFIRE_INCLUDE_CONTENT=false for prod / shared environments to
    # restore the privacy posture from AGENT_CONTRACT.md.
    include_content = os.environ.get("LOGFIRE_INCLUDE_CONTENT", "true").lower() not in (
        "false",
        "0",
        "no",
        "off",
    )
    logfire.instrument_pydantic_ai(include_content=include_content)
    log.info(
        "PydanticAI instrumentation: include_content=%s (override with LOGFIRE_INCLUDE_CONTENT).",
        include_content,
    )

    log.info(
        "Logfire instrumentation active for service=%s (auth via %s).",
        service_name,
        auth_source,
    )
    return True


def instrument_fastapi(app) -> None:
    """Attach FastAPI middleware instrumentation if Logfire is active."""
    try:
        import logfire

        logfire.instrument_fastapi(app)
    except Exception as exc:  # noqa: BLE001 — broad: instrumentation is best-effort
        log.debug("FastAPI instrumentation skipped: %s", exc)

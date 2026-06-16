"""
FastAPI server.

One streaming endpoint (`/api/chat`) plus a small REST surface for the
markdown report and a debug state view. Per the ephemeral privacy posture,
session state lives in-memory only — restart the server and everything is
gone.

Session keying: the frontend mints a UUID on first mount and persists it
in browser localStorage; it sends it as `X-Conversation-Id` on every
`/api/chat` request. The server uses that to look up the matching
`SessionDeps`.
"""

from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic_ai.ui.vercel_ai import VercelAIAdapter

from . import logfire_config
from .agent import agent, make_session_deps
from .rubric import Rubric
from .state import SessionDeps

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session store — in-memory, ephemeral.
# ---------------------------------------------------------------------------


@dataclass
class SessionStore:
    """Map conversation_id → SessionDeps. Goes away on restart."""

    rubric: Rubric
    _sessions: dict[str, SessionDeps]

    def __init__(self, rubric: Rubric) -> None:
        self.rubric = rubric
        self._sessions = {}

    def get_or_create(self, conversation_id: str) -> SessionDeps:
        if conversation_id not in self._sessions:
            self._sessions[conversation_id] = make_session_deps(
                conversation_id, self.rubric
            )
        return self._sessions[conversation_id]

    def get(self, conversation_id: str) -> SessionDeps | None:
        return self._sessions.get(conversation_id)

    def __len__(self) -> int:
        return len(self._sessions)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Configure Logfire and load the rubric once at startup."""
    logfire_config.configure(service_name="summerchild-agent")
    logfire_config.instrument_fastapi(app)

    rubric = Rubric.load()
    app.state.sessions = SessionStore(rubric)
    log.info("Loaded canonical rubric: %d questions.", len(rubric))
    yield


app = FastAPI(title="summerchild-agent", version="0.1.0", lifespan=lifespan)


# Frontend dev server (Next.js) and any local browser tools.
_allowed_origins = os.environ.get(
    "CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Conversation-Id"],
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict[str, str | int]:
    sessions: SessionStore = app.state.sessions  # type: ignore[attr-defined]
    return {
        "status": "ok",
        "active_sessions": len(sessions),
        "rubric_questions": len(sessions.rubric),
    }


@app.post("/api/chat")
async def chat(
    request: Request,
    x_conversation_id: str | None = Header(default=None, alias="X-Conversation-Id"),
) -> Response:
    """Streaming chat endpoint compatible with the Vercel AI SDK Data Stream Protocol.

    The `X-Conversation-Id` header keys the session-deps lookup. If absent,
    the server mints one and includes it in the response headers so the
    frontend can pin it for subsequent requests.
    """
    conversation_id = x_conversation_id or str(uuid.uuid4())
    sessions: SessionStore = app.state.sessions  # type: ignore[attr-defined]
    deps = sessions.get_or_create(conversation_id)
    response = await VercelAIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=deps,
        conversation_id=conversation_id,
        sdk_version=6,  # The frontend uses AI SDK v6.
    )
    response.headers["X-Conversation-Id"] = conversation_id
    return response


@app.get("/api/session/{conversation_id}/state")
async def session_state(conversation_id: str) -> JSONResponse:
    """Debug view of current state. Useful for hand-poking; not for prod UX."""
    sessions: SessionStore = app.state.sessions  # type: ignore[attr-defined]
    deps = sessions.get(conversation_id)
    if deps is None:
        raise HTTPException(status_code=404, detail="No such session")
    s = deps.state
    return JSONResponse(
        {
            "conversation_id": conversation_id,
            "phase": s.phase,
            "depth": s.depth,
            "cohort_multiplier": s.cohort_multiplier,
            "asked_canonical_count": len(s.asked_canonical_ids),
            "added_questions_count": len(s.added_questions),
            "skipped_count": len(s.skipped) + len(s.inferred_answers),
            "de_weightings_count": len(s.de_weightings),
            "pending_question_id": s.pending_question_id,
            "playback_presented": s.playback_presented,
            "playback_skipped": s.playback_skipped,
            "corrections_count": len(s.corrections),
            "final_report_ready": s.final_report is not None,
            "budget": {
                "fraction": deps.budget.fraction,
                "canonical_max_session": deps.budget.canonical_max_session,
                "shift_budget": deps.budget.shift_budget,
                "spent_additions": deps.budget.spent_additions,
                "spent_de_weighting": deps.budget.spent_de_weighting,
                "spent_total": deps.budget.spent_total,
                "remaining": deps.budget.remaining,
            },
        }
    )


@app.get("/api/session/{conversation_id}/report")
async def session_report(conversation_id: str) -> Response:
    """Returns the markdown report for a finalised session. 404 until finalised."""
    sessions: SessionStore = app.state.sessions  # type: ignore[attr-defined]
    deps = sessions.get(conversation_id)
    if deps is None:
        raise HTTPException(status_code=404, detail="No such session")
    if deps.state.final_report is None:
        raise HTTPException(
            status_code=409,
            detail="Session has not been finalised yet.",
        )
    md = deps.state.final_report.to_markdown()
    return Response(
        content=md,
        media_type="text/markdown",
        headers={
            "Content-Disposition": (
                f'attachment; filename="sscs-{conversation_id[:8]}.md"'
            ),
        },
    )

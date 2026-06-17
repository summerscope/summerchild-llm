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
from .state import maybe_runtime

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Session store — in-memory, ephemeral.
# ---------------------------------------------------------------------------


@dataclass
class SessionStore:
    """Tracks active conversation ids. Heavy state lives in the runtime store
    in state.py — this just owns the rubric and the set of known session ids
    so we can answer /health and friends."""

    rubric: Rubric
    _ids: set[str]

    def __init__(self, rubric: Rubric) -> None:
        self.rubric = rubric
        self._ids = set()

    def get_or_create(self, conversation_id: str) -> str:
        """Ensure a runtime session exists for `conversation_id`; returns it."""
        if conversation_id not in self._ids:
            make_session_deps(conversation_id, self.rubric)
            self._ids.add(conversation_id)
        return conversation_id

    def has(self, conversation_id: str) -> bool:
        return conversation_id in self._ids

    def __len__(self) -> int:
        return len(self._ids)


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
    session_id = sessions.get_or_create(conversation_id)
    # `deps` is just the session_id — tools resolve the live SessionDeps via
    # the runtime store. Values referenced in the system prompt come from
    # Logfire managed variables (server-side @{var}@ resolution), not deps.
    response = await VercelAIAdapter.dispatch_request(
        request,
        agent=agent,
        deps=session_id,
        conversation_id=conversation_id,
        sdk_version=6,  # The frontend uses AI SDK v6.
    )
    response.headers["X-Conversation-Id"] = conversation_id
    return response


def _empty_state_payload(conversation_id: str) -> dict:
    """Shape returned for session ids the server hasn't seen via /api/chat yet."""
    return {
        "conversation_id": conversation_id,
        "exists": False,
        "phase": 1,
        "depth": None,
        "cohort_multiplier": None,
        "asked_canonical_count": 0,
        "added_questions_count": 0,
        "skipped_count": 0,
        "inferred_count": 0,
        "de_weightings_count": 0,
        "pending_question_id": None,
        "playback_presented": False,
        "playback_skipped": False,
        "corrections_count": 0,
        "final_report_ready": False,
        "budget": {
            "fraction": 0.25,
            "canonical_max_session": 0,
            "shift_budget": 0.0,
            "spent_additions": 0,
            "spent_de_weighting": 0,
            "spent_total": 0,
            "remaining": 0.0,
        },
    }


@app.get("/api/session/{conversation_id}/state")
async def session_state(conversation_id: str) -> JSONResponse:
    """Live state view for the frontend's ScoreSidebar.

    Returns a 200 with an empty-state shape for unknown ids — the frontend
    polls this BEFORE the first /api/chat call, so 404s here would just be
    console noise. Sessions are created lazily by /api/chat.
    """
    deps = maybe_runtime(conversation_id)
    if deps is None:
        return JSONResponse(_empty_state_payload(conversation_id))
    s = deps.state
    return JSONResponse(
        {
            "conversation_id": conversation_id,
            "exists": True,
            "phase": s.phase,
            "depth": s.depth,
            "cohort_multiplier": s.cohort_multiplier,
            "asked_canonical_count": len(s.asked_canonical_ids),
            "added_questions_count": len(s.added_questions),
            "skipped_count": len(s.skipped),
            "inferred_count": len(s.inferred_answers),
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


@app.get("/api/session/{conversation_id}/pending")
async def pending_question(conversation_id: str) -> JSONResponse:
    """Return the currently-pending question (text + valid answer choices).

    Frontend uses this to render button choices below the agent's last
    message — so the user clicks an answer instead of typing it. Returns
    null payload when nothing's pending.
    """
    deps = maybe_runtime(conversation_id)
    if deps is None or deps.state.pending_question_id is None:
        return JSONResponse({"pending": None})

    qid = deps.state.pending_question_id
    source = deps.state.pending_question_source
    if source == "canonical":
        q = deps.rubric[qid]
        text = q.text
        modality = q.preferred_modality
        answers = q.answers
    elif source == "agent-added" and qid in deps.state.added_questions:
        q = deps.state.added_questions[qid]
        text = q.text
        modality = "buttons"
        answers = q.answers
    else:
        return JSONResponse({"pending": None})

    return JSONResponse(
        {
            "pending": {
                "question_id": qid,
                "source": source,
                "text": text,
                "preferred_modality": modality,
                "answers": [
                    {"key": key, "text": ans.text}
                    for key, ans in answers.items()
                ],
            }
        }
    )


@app.get("/api/session/{conversation_id}/report")
async def session_report(conversation_id: str) -> Response:
    """Returns the markdown report for a finalised session. 404 until finalised."""
    deps = maybe_runtime(conversation_id)
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

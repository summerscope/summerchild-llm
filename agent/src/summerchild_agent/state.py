"""
Mutable session state — the agent's deps payload.

Carries the rubric (read-only), the bounds, and the BudgetLedger plus all
the per-session mutable fields the tools touch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .budget import BudgetLedger
from .managed_vars import AgentBounds
from .models import (
    AgentAddedQuestion,
    Correction,
    DepthSet,
    FinalReport,
    PlaybackBullet,
    RoutedCanonicalEntry,
    SkipReason,
)
from .rubric import Rubric


@dataclass
class SessionState:
    """Mutable per-session state. Tools mutate this in place."""

    phase: int = 1  # 1 = conversation, 2 = re-weight+playback
    depth: DepthSet | None = None
    cohort_multiplier: float | None = None
    answers_given: dict[str, str] = field(default_factory=dict)  # qid → answer_key (canonical + added)

    # Pending state — the most recent question awaiting a user answer.
    pending_question_id: str | None = None
    pending_question_source: str | None = None  # "canonical" or "agent-added"

    # Phase 1 dispositions, finalised:
    asked_canonical_ids: set[str] = field(default_factory=set)
    skipped: dict[str, tuple[SkipReason, str | None, str | None]] = field(
        default_factory=dict
    )  # qid → (reason, evidence, inferred_answer_key)
    added_questions: dict[str, AgentAddedQuestion] = field(default_factory=dict)
    inferred_answers: dict[str, tuple[str, str]] = field(default_factory=dict)  # qid → (answer_key, evidence)

    # Phase 2 dispositions:
    de_weightings: dict[str, tuple[dict[str, int], dict[str, int], str]] = field(
        default_factory=dict
    )  # qid → (original_scores, effective_scores, justification)

    # Playback artefacts:
    playback_bullets: list[PlaybackBullet] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    playback_skipped: bool = False
    playback_presented: bool = False

    # Finalised report (set by `finalise` tool):
    final_report: FinalReport | None = None


@dataclass
class SessionDeps:
    """Container holding everything tools need. NOT passed directly to PydanticAI.

    The agent's `deps` is just the `session_id` (a string). Tools look up the
    matching `SessionDeps` from the runtime store via `get_runtime(session_id)`.
    This avoids PydanticAI's Pydantic-validation roundtrip mangling the nested
    dataclasses (state, BudgetLedger) into dicts when the run begins.
    """

    session_id: str
    rubric: Rubric
    bounds: AgentBounds
    budget: BudgetLedger
    state: SessionState

    @property
    def hit_question_cap(self) -> bool:
        return self.state.asked_canonical_ids.__len__() + len(
            self.state.added_questions
        ) >= self.bounds.hard_question_cap

    @property
    def at_soft_target(self) -> bool:
        return self.state.asked_canonical_ids.__len__() + len(
            self.state.added_questions
        ) >= self.bounds.soft_question_target


# ---------------------------------------------------------------------------
# Runtime store — module-level dict keyed by session_id.
# In-memory only; goes away on server restart (matches ephemeral posture).
# ---------------------------------------------------------------------------

_runtime: dict[str, SessionDeps] = {}


def register_session(deps: SessionDeps) -> str:
    """Register fresh SessionDeps; returns the session_id."""
    _runtime.setdefault(deps.session_id, deps)
    return deps.session_id


def get_runtime(session_id: str) -> SessionDeps:
    """Look up the SessionDeps for session_id. Raises if unknown."""
    if session_id not in _runtime:
        raise KeyError(f"No runtime session registered for {session_id!r}")
    return _runtime[session_id]


def maybe_runtime(session_id: str) -> SessionDeps | None:
    """Look up without raising — for debug endpoints."""
    return _runtime.get(session_id)


def forget_session(session_id: str) -> None:
    _runtime.pop(session_id, None)


def make_routed_log(deps: SessionDeps) -> list[RoutedCanonicalEntry]:
    """Build the routed_canonical_questions block from SessionState."""
    entries: list[RoutedCanonicalEntry] = []

    # Asked-then-de-weighted come first conceptually; iterate in canonical order.
    for qid in deps.rubric.ids:
        if qid in deps.state.de_weightings:
            original, effective, justification = deps.state.de_weightings[qid]
            entries.append(
                RoutedCanonicalEntry(
                    question_id=qid,
                    phase_disposition="asked_then_de_weighted",
                    answer_given=deps.state.answers_given.get(qid),
                    original_score=original,
                    effective_score=effective,
                    de_weighting_justification=justification,
                )
            )
        elif qid in deps.state.asked_canonical_ids:
            entries.append(
                RoutedCanonicalEntry(
                    question_id=qid,
                    phase_disposition="asked",
                    answer_given=deps.state.answers_given.get(qid),
                )
            )
        elif qid in deps.state.skipped:
            reason, evidence, inferred_key = deps.state.skipped[qid]
            entries.append(
                RoutedCanonicalEntry(
                    question_id=qid,
                    phase_disposition="skipped",
                    skip_reason=reason,
                    answer_given=inferred_key,
                    evidence=evidence,
                )
            )
        elif qid in deps.state.inferred_answers:
            ans_key, evidence = deps.state.inferred_answers[qid]
            entries.append(
                RoutedCanonicalEntry(
                    question_id=qid,
                    phase_disposition="skipped",
                    skip_reason=SkipReason.INFERRED_FROM_BRAINDUMP,
                    answer_given=ans_key,
                    evidence=evidence,
                )
            )
    return entries

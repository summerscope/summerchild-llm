"""
The conversational agent.

PydanticAI 1.x. One `Agent` definition. Tools are gated per phase via the
`prepare=` callback so the model only sees Phase 1 tools in Phase 1 and
Phase 2 tools in Phase 2.

The agent's text output is what the user reads (a question, a transition,
the playback, etc.). The structured artifact (the `FinalReport`) is built
up across turns in `deps.state` and finalised by the `finalise` tool —
the server reads `deps.state.final_report` after each turn to know when
the session is done.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Annotated, Literal

# The Anthropic provider eagerly validates ANTHROPIC_API_KEY at construction.
# We don't want module import to require a key (lets tests run, lets the
# server boot in a degraded "no real LLM" state). The placeholder is replaced
# at first agent.run() call by the real env var if present; if it isn't,
# the underlying API call surfaces the auth error then — much clearer than
# an import-time crash.
os.environ.setdefault("ANTHROPIC_API_KEY", "placeholder-real-key-required-at-runtime")

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.tools import ToolDefinition

from .budget import BudgetExceeded
from .managed_vars import default_agent_bounds, default_system_prompt
from .models import (
    AgentAddedQuestion,
    Answer,
    Correction,
    FinalReport,
    PerDeductionTrace,
    PhaseDisposition,
    Playback,
    PlaybackBullet,
    Recommendation,
    Reference,
    ScoreComputation,
    SkipReason,
)
from .rubric import Rubric
from .state import SessionDeps, SessionState, make_routed_log

# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------

_BOUNDS = default_agent_bounds()

agent: Agent[SessionDeps, str] = Agent(
    _BOUNDS.model_id,
    deps_type=SessionDeps,
    output_type=str,
    instructions=default_system_prompt(),
)


# ---------------------------------------------------------------------------
# Dynamic system prompt — appends current state every turn
# ---------------------------------------------------------------------------


@agent.system_prompt
def _state_summary(ctx: RunContext[SessionDeps]) -> str:
    s = ctx.deps.state
    b = ctx.deps.budget
    asked_total = len(s.asked_canonical_ids) + len(s.added_questions)
    lines = [
        "## Current session state",
        f"- Phase: {s.phase}",
        f"- Depth: {s.depth or 'not yet established'}",
        f"- Cohort multiplier: {s.cohort_multiplier or 'not yet established'}",
        f"- Canonical questions asked: {len(s.asked_canonical_ids)}",
        f"- Agent-added questions: {len(s.added_questions)}",
        f"- Canonical questions skipped: {len(s.skipped) + len(s.inferred_answers)}",
        f"- Total questions asked so far: {asked_total} (soft target "
        f"{ctx.deps.bounds.soft_question_target}, hard cap "
        f"{ctx.deps.bounds.hard_question_cap})",
        f"- Shift budget: spent {b.spent_total}/{b.shift_budget:.1f} "
        f"({b.spent_additions} additions, {b.spent_de_weighting} de-weightings)",
    ]
    if s.pending_question_id:
        lines.append(
            f"- Pending answer for: {s.pending_question_id} "
            f"({s.pending_question_source})"
        )
    if ctx.deps.hit_question_cap:
        lines.append("- **At hard cap — Phase 1 must end now.**")
    elif ctx.deps.at_soft_target and s.phase == 1:
        lines.append("- **At soft target — consider whether you have enough signal.**")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase gating
# ---------------------------------------------------------------------------


def _phase_is(phase: int):
    """Build a `prepare=` callback that exposes a tool only in the given phase."""

    async def gate(ctx: RunContext[SessionDeps], td: ToolDefinition) -> ToolDefinition | None:
        if ctx.deps.state.phase != phase:
            return None
        # Once finalised, no more tools.
        if ctx.deps.state.final_report is not None:
            return None
        return td

    return gate


# ---------------------------------------------------------------------------
# Phase 1 — tool argument models
# ---------------------------------------------------------------------------


class AskCanonicalArgs(BaseModel):
    question_id: str = Field(description="A canonical question id, e.g. 'Q-cohort_size'.")


class SkipCanonicalArgs(BaseModel):
    question_id: str
    reason: Annotated[
        Literal[
            "gated_out_by_depth",
            "gated_out_by_prerequisite",
            "not_applicable_in_context",
            "question_cap_reached",
        ],
        Field(description="Skip reason. Do NOT use 'inferred_from_braindump' here — that has its own tool."),
    ]
    evidence: str | None = Field(
        default=None,
        description="User-supplied evidence the question doesn't apply, if reason is 'not_applicable_in_context'.",
    )


class InferFromBraindumpArgs(BaseModel):
    question_id: str
    inferred_answer_key: str
    evidence: str = Field(description="Verbatim user excerpt that supports the inference.")


class RecordUserAnswerArgs(BaseModel):
    question_id: str
    answer_key: str


class _AnswerPayload(BaseModel):
    text: str
    score: int = Field(ge=0, le=9)


class _RecommendationPayload(BaseModel):
    tradeoff: str
    considerations: str
    references: list[dict[str, str]] = Field(default_factory=list)


class AddAgentQuestionArgs(BaseModel):
    id: str = Field(pattern=r"^A-[a-z0-9_]+$", description="Must start with 'A-'.")
    text: str
    weight_tier: Literal["low", "medium", "high"]
    answers: dict[str, _AnswerPayload]
    tags: list[str] = Field(default_factory=list)
    recommendation_per_answer: dict[str, _RecommendationPayload] = Field(default_factory=dict)
    evidence: str = Field(description="Verbatim user excerpt that prompted the addition.")


# ---------------------------------------------------------------------------
# Phase 1 tools
# ---------------------------------------------------------------------------


@agent.tool(prepare=_phase_is(1))
def ask_canonical(ctx: RunContext[SessionDeps], args: AskCanonicalArgs) -> str:
    """Stage a canonical question to be asked of the user this turn.

    Records the ask in the session log + budget. Your text output for this
    turn should be the question (rephrased for the user's domain if helpful).
    Only stage one ask per turn.
    """
    qid = args.question_id
    if qid not in ctx.deps.rubric:
        raise ModelRetry(f"Unknown canonical question_id: {qid!r}")
    if qid in ctx.deps.state.asked_canonical_ids or qid in ctx.deps.state.skipped:
        raise ModelRetry(f"{qid} has already been addressed this session.")
    if ctx.deps.state.pending_question_id is not None:
        raise ModelRetry(
            "Another question is already pending an answer — record the user's "
            "answer first before asking a new one."
        )
    q = ctx.deps.rubric[qid]
    # Check applicability
    if not ctx.deps.rubric.is_applicable(
        q, depth=ctx.deps.state.depth, answers_given=ctx.deps.state.answers_given
    ):
        raise ModelRetry(
            f"{qid} is not applicable given current state — gating not satisfied."
        )
    ctx.deps.budget.record_canonical_asked(q)
    ctx.deps.state.asked_canonical_ids.add(qid)
    ctx.deps.state.pending_question_id = qid
    ctx.deps.state.pending_question_source = "canonical"
    # Return a structured view of the question for the agent to format
    return _render_question_for_agent(q.text, q.answers, q.preferred_modality)


@agent.tool(prepare=_phase_is(1))
def skip_canonical(ctx: RunContext[SessionDeps], args: SkipCanonicalArgs) -> str:
    """Skip a canonical question. Free — does not consume shift budget."""
    qid = args.question_id
    if qid not in ctx.deps.rubric:
        raise ModelRetry(f"Unknown canonical question_id: {qid!r}")
    if qid in ctx.deps.state.asked_canonical_ids or qid in ctx.deps.state.skipped:
        raise ModelRetry(f"{qid} has already been addressed.")
    ctx.deps.state.skipped[qid] = (SkipReason(args.reason), args.evidence, None)
    return f"Skipped {qid} ({args.reason})."


@agent.tool(prepare=_phase_is(1))
def infer_from_braindump(
    ctx: RunContext[SessionDeps], args: InferFromBraindumpArgs
) -> str:
    """Record an inferred canonical answer extracted from the user's braindump.

    The question is treated as skipped (no budget cost, no further ask).
    """
    qid = args.question_id
    if qid not in ctx.deps.rubric:
        raise ModelRetry(f"Unknown canonical question_id: {qid!r}")
    if qid in ctx.deps.state.asked_canonical_ids or qid in ctx.deps.state.skipped:
        raise ModelRetry(f"{qid} has already been addressed.")
    q = ctx.deps.rubric[qid]
    if args.inferred_answer_key not in q.answers:
        raise ModelRetry(
            f"Answer key {args.inferred_answer_key!r} not in {qid} answers: "
            f"{list(q.answers)}"
        )
    ctx.deps.state.inferred_answers[qid] = (args.inferred_answer_key, args.evidence)
    ctx.deps.state.answers_given[qid] = args.inferred_answer_key
    _maybe_apply_calibration(ctx.deps, qid, args.inferred_answer_key)
    return f"Inferred {qid}={args.inferred_answer_key} from braindump."


@agent.tool(prepare=_phase_is(1))
def record_user_answer(
    ctx: RunContext[SessionDeps], args: RecordUserAnswerArgs
) -> str:
    """Record the user's answer to the currently-pending question."""
    qid = args.question_id
    if ctx.deps.state.pending_question_id != qid:
        raise ModelRetry(
            f"No pending question {qid!r}. Pending is "
            f"{ctx.deps.state.pending_question_id!r}."
        )
    source = ctx.deps.state.pending_question_source
    if source == "canonical":
        q = ctx.deps.rubric[qid]
    elif source == "agent-added":
        q = ctx.deps.state.added_questions[qid]
    else:
        raise ModelRetry(f"Unknown pending source: {source!r}")
    if args.answer_key not in q.answers:
        raise ModelRetry(
            f"Answer key {args.answer_key!r} not valid for {qid}. Choices: "
            f"{list(q.answers)}"
        )
    ctx.deps.state.answers_given[qid] = args.answer_key
    if source == "agent-added":
        ctx.deps.state.added_questions[qid] = ctx.deps.state.added_questions[qid].model_copy(
            update={"answer_given": args.answer_key}
        )
    ctx.deps.state.pending_question_id = None
    ctx.deps.state.pending_question_source = None
    _maybe_apply_calibration(ctx.deps, qid, args.answer_key)
    return f"Recorded {qid}={args.answer_key}."


@agent.tool(prepare=_phase_is(1))
def add_agent_question(
    ctx: RunContext[SessionDeps], args: AddAgentQuestionArgs
) -> str:
    """Add a new agent-authored question and stage it as the pending ask.

    Consumes shift budget (its max-possible score). The question is shown
    to the user this turn — your text output should be the question.
    """
    if ctx.deps.state.pending_question_id is not None:
        raise ModelRetry(
            "Another question is already pending — record the user's answer "
            "first before adding a new one."
        )
    answers = {
        key: Answer(text=v.text, score=v.score)
        for key, v in args.answers.items()
    }
    # Attach recommendations if given.
    for key, rec in args.recommendation_per_answer.items():
        if key not in answers:
            continue
        ans = answers[key]
        refs = [
            Reference(source=r.get("source", ""), ref=r.get("ref", ""), topic=r.get("topic", ""))
            for r in rec.references
        ]
        answers[key] = ans.model_copy(
            update={
                "recommendation": Recommendation(
                    tradeoff=rec.tradeoff,
                    considerations=rec.considerations,
                    references=refs,
                )
            }
        )
    try:
        added = AgentAddedQuestion(
            id=args.id,
            text=args.text,
            tags=args.tags,
            weight_tier=args.weight_tier,
            answers=answers,
            evidence=args.evidence,
            created_at=datetime.utcnow(),
        )
    except ValueError as e:
        raise ModelRetry(str(e)) from e
    try:
        ctx.deps.budget.record_agent_added(added)
    except BudgetExceeded as e:
        raise ModelRetry(str(e)) from e
    ctx.deps.state.added_questions[added.id] = added
    ctx.deps.state.pending_question_id = added.id
    ctx.deps.state.pending_question_source = "agent-added"
    return _render_question_for_agent(added.text, added.answers, "buttons")


@agent.tool(prepare=_phase_is(1))
def end_phase_1(ctx: RunContext[SessionDeps]) -> str:
    """End Phase 1 (conversation) and enter Phase 2 (re-weight + playback).

    Call this when you have enough signal OR when the question cap is hit.
    """
    if ctx.deps.state.pending_question_id is not None:
        raise ModelRetry(
            f"Cannot end Phase 1 with a question still pending "
            f"({ctx.deps.state.pending_question_id})."
        )
    if ctx.deps.state.cohort_multiplier is None:
        raise ModelRetry(
            "Cannot end Phase 1 without an answer to Q-cohort_size — the cohort "
            "multiplier is required for scoring."
        )
    if ctx.deps.hit_question_cap:
        ctx.deps.budget.hit_question_cap()
        # Mark anything still in routed-but-not-addressed as "question_cap_reached"
        addressed = (
            ctx.deps.state.asked_canonical_ids
            | ctx.deps.state.skipped.keys()
            | ctx.deps.state.inferred_answers.keys()
        )
        for q in ctx.deps.rubric:
            if q.id in addressed:
                continue
            if not ctx.deps.rubric.is_applicable(
                q, depth=ctx.deps.state.depth, answers_given=ctx.deps.state.answers_given
            ):
                continue
            ctx.deps.state.skipped[q.id] = (SkipReason.QUESTION_CAP_REACHED, None, None)
    ctx.deps.state.phase = 2
    return "Phase 1 ended. Phase 2 tools (de_weight, present_playback, record_correction, finalise) are now available."


# ---------------------------------------------------------------------------
# Phase 2 — tool argument models
# ---------------------------------------------------------------------------


class DeWeightArgs(BaseModel):
    question_id: str
    effective_scores: dict[str, int] = Field(
        description="New per-answer scores for this question. Each must be ≤ the original."
    )
    justification: str = Field(
        description="Justification referencing conversation evidence."
    )


class PresentPlaybackBullet(BaseModel):
    summary: str = Field(description="One-line conclusion in plain English.")
    source_question_ids: list[str] = Field(
        description="Canonical question ids this bullet groups. Used for correction rebinding."
    )
    inferred_answer_key: str | None = None


class PresentPlaybackArgs(BaseModel):
    bullets: list[PresentPlaybackBullet]


class RecordCorrectionArgs(BaseModel):
    question_id: str
    prior_answer_key: str
    corrected_answer_key: str
    note: str | None = None


# ---------------------------------------------------------------------------
# Phase 2 tools
# ---------------------------------------------------------------------------


@agent.tool(prepare=_phase_is(2))
def de_weight(ctx: RunContext[SessionDeps], args: DeWeightArgs) -> str:
    """De-weight a canonical question. Cost = drop in its max-possible contribution."""
    qid = args.question_id
    if qid not in ctx.deps.rubric:
        raise ModelRetry(f"Unknown canonical question_id: {qid!r}")
    if qid in ctx.deps.state.de_weightings:
        raise ModelRetry(f"{qid} has already been de-weighted this session.")
    if qid not in ctx.deps.state.asked_canonical_ids:
        raise ModelRetry(
            f"Can only de-weight a question that was asked. {qid} was not."
        )
    q = ctx.deps.rubric[qid]
    original = {k: a.score for k, a in q.answers.items() if a.score is not None}
    effective = dict(args.effective_scores)
    # Sanity: same keys, each effective <= original
    if set(effective) != set(original):
        raise ModelRetry(
            f"Effective scores keys {sorted(effective)} must match original keys "
            f"{sorted(original)}."
        )
    for key, eff in effective.items():
        if eff > original[key]:
            raise ModelRetry(
                f"Effective score {key}={eff} > original {original[key]}. "
                "De-weighting can only reduce."
            )
        if eff < 0:
            raise ModelRetry(f"Effective score {key}={eff} cannot be negative.")
    try:
        ctx.deps.budget.record_de_weight(
            original_max=max(original.values()),
            effective_max=max(effective.values()),
        )
    except BudgetExceeded as e:
        raise ModelRetry(str(e)) from e
    ctx.deps.state.de_weightings[qid] = (original, effective, args.justification)
    return f"De-weighted {qid}: max went from {max(original.values())} → {max(effective.values())}."


@agent.tool(prepare=_phase_is(2))
def present_playback(ctx: RunContext[SessionDeps], args: PresentPlaybackArgs) -> str:
    """Record the playback bullets to be shown to the user.

    Your text output this turn should render the bullets and end with a
    prompt like 'Did I get any of this wrong?'.
    """
    bullets = [
        PlaybackBullet(
            summary=b.summary,
            source_question_ids=b.source_question_ids,
            inferred_answer_key=b.inferred_answer_key,
        )
        for b in args.bullets
    ]
    # Validate that all source_question_ids reference real questions.
    for b in bullets:
        for qid in b.source_question_ids:
            if qid not in ctx.deps.rubric and qid not in ctx.deps.state.added_questions:
                raise ModelRetry(
                    f"Playback bullet references unknown question_id {qid!r}."
                )
    ctx.deps.state.playback_bullets = bullets
    ctx.deps.state.playback_presented = True
    return f"Playback recorded with {len(bullets)} bullets."


@agent.tool(prepare=_phase_is(2))
def record_correction(
    ctx: RunContext[SessionDeps], args: RecordCorrectionArgs
) -> str:
    """Record a user correction during playback. Updates answers_given."""
    qid = args.question_id
    if qid in ctx.deps.rubric:
        q = ctx.deps.rubric[qid]
    elif qid in ctx.deps.state.added_questions:
        q = ctx.deps.state.added_questions[qid]
    else:
        raise ModelRetry(f"Unknown question_id for correction: {qid!r}")
    if args.corrected_answer_key not in q.answers:
        raise ModelRetry(
            f"Corrected answer key {args.corrected_answer_key!r} not in {qid} answers."
        )
    ctx.deps.state.corrections.append(
        Correction(
            question_id=qid,
            prior_answer_key=args.prior_answer_key,
            corrected_answer_key=args.corrected_answer_key,
            note=args.note,
        )
    )
    ctx.deps.state.answers_given[qid] = args.corrected_answer_key
    return f"Correction recorded for {qid}: {args.prior_answer_key} → {args.corrected_answer_key}."


@agent.tool(prepare=_phase_is(2))
def finalise(ctx: RunContext[SessionDeps]) -> str:
    """Build the FinalReport and commit it. Call this once playback is done."""
    if not ctx.deps.state.playback_presented and not ctx.deps.state.playback_skipped:
        raise ModelRetry(
            "Cannot finalise before running `present_playback` (or marking it "
            "skipped via the future `skip_playback` tool)."
        )
    report = _build_final_report(ctx.deps)
    ctx.deps.state.final_report = report
    return (
        f"Session finalised. Final score: {report.score_computation.final_score}. "
        f"Report ready."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_question_for_agent(
    text: str, answers: dict[str, Answer], modality: str
) -> str:
    """Compact string the model sees as the tool's return value."""
    lines = [text, "", f"Modality: {modality}", "Answer choices:"]
    for key, ans in answers.items():
        score_or_mult = ""
        if ans.score is not None:
            score_or_mult = f" [score={ans.score}]"
        elif ans.multiplier is not None:
            score_or_mult = f" [multiplier={ans.multiplier}]"
        lines.append(f"  {key}) {ans.text}{score_or_mult}")
    return "\n".join(lines)


def _maybe_apply_calibration(deps: SessionDeps, qid: str, answer_key: str) -> None:
    """If the answered question is Q-cohort_size or Q-llm_depth, update calibration."""
    if qid == "Q-cohort_size":
        q = deps.rubric[qid]
        ans = q.answers.get(answer_key)
        if ans is not None and ans.multiplier is not None:
            deps.state.cohort_multiplier = ans.multiplier
    elif qid == "Q-llm_depth":
        q = deps.rubric[qid]
        ans = q.answers.get(answer_key)
        if ans is not None and ans.depth_set is not None:
            deps.state.depth = ans.depth_set


def _build_final_report(deps: SessionDeps) -> FinalReport:
    """Construct a FinalReport from current session state."""
    routed = make_routed_log(deps)
    added = list(deps.state.added_questions.values())
    # Final state after corrections:
    final_state = dict(deps.state.answers_given)
    playback = Playback(
        inferred_dimensions=deps.state.playback_bullets,
        user_corrections=deps.state.corrections,
        final_state=final_state,
        skipped=deps.state.playback_skipped,
    )
    score = _compute_score(deps, routed, added)
    accounting = deps.budget.to_accounting()
    return FinalReport(
        session_id=deps.session_id,
        routed_canonical_questions=routed,
        agent_added_questions=added,
        playback=playback,
        score_computation=score,
        shift_budget_accounting=accounting,
    )


def _compute_score(
    deps: SessionDeps,
    routed: list,
    added: list,
) -> ScoreComputation:
    """Apply the score formula: clip(99 − cohort_multiplier × Σ deductions, 0, 99)."""
    raw_canonical = 0
    raw_added = 0
    trace: list[PerDeductionTrace] = []
    for entry in routed:
        if entry.phase_disposition == PhaseDisposition.SKIPPED:
            continue
        qid = entry.question_id
        ans_key = entry.answer_given
        if ans_key is None:
            continue
        if entry.phase_disposition == PhaseDisposition.ASKED_THEN_DE_WEIGHTED:
            scores = entry.effective_score or {}
        else:
            q = deps.rubric[qid]
            scores = {k: a.score for k, a in q.answers.items() if a.score is not None}
        contribution = scores.get(ans_key, 0)
        raw_canonical += contribution
        trace.append(
            PerDeductionTrace(
                question_id=qid,
                answer_key=ans_key,
                source="canonical",
                score_contribution=contribution,
            )
        )
    for aq in added:
        if aq.answer_given is None:
            continue
        contribution = aq.answers[aq.answer_given].score or 0
        raw_added += contribution
        trace.append(
            PerDeductionTrace(
                question_id=aq.id,
                answer_key=aq.answer_given,
                source="agent-added",
                score_contribution=contribution,
            )
        )
    raw_total = raw_canonical + raw_added
    multiplier = deps.state.cohort_multiplier or 1.0
    amplified = multiplier * raw_total
    final = max(0, min(99, round(99 - amplified)))
    return ScoreComputation(
        raw_deductions_canonical=raw_canonical,
        raw_deductions_agent_added=raw_added,
        raw_deductions_total=raw_total,
        cohort_multiplier=multiplier,
        amplified_deductions=amplified,
        final_score=final,
        per_deduction_trace=trace,
    )


# ---------------------------------------------------------------------------
# Factory for fresh sessions
# ---------------------------------------------------------------------------


def make_session_deps(session_id: str, rubric: Rubric) -> SessionDeps:
    """Build a fresh SessionDeps for a new conversation."""
    from .budget import BudgetLedger as _BL

    bounds = default_agent_bounds()
    return SessionDeps(
        session_id=session_id,
        rubric=rubric,
        bounds=bounds,
        budget=_BL(fraction=bounds.shift_budget_fraction),
        state=SessionState(),
    )

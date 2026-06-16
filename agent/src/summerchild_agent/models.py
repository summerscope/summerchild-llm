"""
Pydantic models — mirror of the canonical rubric schema and the session-log
contract defined in `summerchild-llm/AGENT_CONTRACT.md` v1.1.

Two families:

1. **Rubric** (read-only, frozen on load): `CanonicalQuestion`, `Answer`,
   `Recommendation`, `Reference`, `Gating`.
2. **Session log** (runtime, built up across Phase 1 + Phase 2):
   `RoutedCanonicalEntry`, `AgentAddedQuestion`, `Playback`, `Correction`,
   `ScoreComputation`, `ShiftBudgetAccounting`, `FinalReport`.

Two enums own the discrete state surface: `PhaseDisposition` and
`SkipReason`.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Discrete state surface
# ---------------------------------------------------------------------------

WeightTier = Literal["low", "medium", "high", "n/a"]
"""Canonical-defined weight palette. Agent-added questions must use low/medium/high."""

DepthSet = Literal["none", "point", "pervasive", "agentic"]
"""LLM-implementation depth, set by Q-llm_depth answer."""

QuestionSource = Literal["canonical", "agent-added"]


class PhaseDisposition(StrEnum):
    """How a canonical question ended up scored (or not)."""

    ASKED = "asked"
    SKIPPED = "skipped"
    ASKED_THEN_DE_WEIGHTED = "asked_then_de_weighted"


class SkipReason(StrEnum):
    """Reasons a canonical question can be excluded from scoring. Closed set per contract."""

    GATED_OUT_BY_DEPTH = "gated_out_by_depth"
    GATED_OUT_BY_PREREQUISITE = "gated_out_by_prerequisite"
    INFERRED_FROM_BRAINDUMP = "inferred_from_braindump"
    NOT_APPLICABLE_IN_CONTEXT = "not_applicable_in_context"
    QUESTION_CAP_REACHED = "question_cap_reached"


# ---------------------------------------------------------------------------
# Rubric — frozen on load
# ---------------------------------------------------------------------------


class Reference(BaseModel):
    """Legislative or standards reference attached to a recommendation."""

    model_config = ConfigDict(frozen=True)

    source: str
    ref: str
    topic: str


class Recommendation(BaseModel):
    """Per-answer recommendation: names a tradeoff, points at considerations and refs."""

    model_config = ConfigDict(frozen=True)

    tradeoff: str
    considerations: str
    references: list[Reference] = Field(default_factory=list)


class Answer(BaseModel):
    """A single answer in a canonical or agent-added question.

    Most answers carry `score`. Two special cases in the canonical rubric:
    - `Q-cohort_size` answers carry `multiplier` (cohort scale amplifier).
    - `Q-llm_depth` answers carry `depth_set` (routing key); `score` is 0.

    `nextq` is the static-walker hint for the next question id; the agent
    reads it but isn't bound to follow it.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    score: int | None = None
    multiplier: float | None = None
    depth_set: DepthSet | None = None
    nextq: str | None = None
    recommendation: Recommendation | None = None


class DepthGate(BaseModel):
    """Question is applicable only when Q-llm_depth landed in one of these sets."""

    model_config = ConfigDict(frozen=True)

    depth_in: list[DepthSet]


class PrereqGate(BaseModel):
    """Question is applicable only when a named prior answer matches.

    The expression has the literal shape `Q-{id}.answer == '{value}'`. Parsing
    lives in `rubric.py` so it can be reused by the budget code.
    """

    model_config = ConfigDict(frozen=True)

    prerequisite: str


Gating = Annotated[
    DepthGate | PrereqGate,
    Field(union_mode="left_to_right"),
]


class CanonicalQuestion(BaseModel):
    """One question from `questions.json`."""

    model_config = ConfigDict(frozen=True)

    id: str
    section: str
    tags: list[str] = Field(default_factory=list)
    text: str
    gating: Gating | None = None
    weight_tier: WeightTier
    preferred_modality: Literal["buttons", "open"] = "buttons"
    answers: dict[str, Answer]

    @property
    def max_score(self) -> int:
        """Largest score across this question's answer ladder. Used for budget math."""
        return max((a.score for a in self.answers.values() if a.score is not None), default=0)


# ---------------------------------------------------------------------------
# Agent-added questions
# ---------------------------------------------------------------------------

_PALETTE_RANGE: dict[WeightTier, tuple[int, int]] = {
    "low": (1, 2),
    "medium": (3, 5),
    "high": (6, 9),
}


class AgentAddedQuestion(BaseModel):
    """A question the agent introduced during Phase 1 to cover a gap.

    Weight palette is enforced: per-answer scores must fit the tier's range.
    """

    id: str = Field(pattern=r"^A-[a-z0-9_]+$")
    text: str
    tags: list[str] = Field(default_factory=list)
    weight_tier: Literal["low", "medium", "high"]
    answers: dict[str, Answer]
    evidence: str = Field(description="Conversation excerpt that prompted the addition.")
    created_at: datetime
    answer_given: str | None = None

    @model_validator(mode="after")
    def _enforce_palette(self) -> Self:
        lo, hi = _PALETTE_RANGE[self.weight_tier]
        for key, ans in self.answers.items():
            if ans.score is None:
                raise ValueError(f"Agent-added answer {self.id}.{key!r} must carry a `score`.")
            if not lo <= ans.score <= hi:
                raise ValueError(
                    f"Agent-added answer {self.id}.{key!r} score={ans.score} outside "
                    f"{self.weight_tier} palette range [{lo}, {hi}]."
                )
        if self.answer_given is not None and self.answer_given not in self.answers:
            raise ValueError(
                f"Agent-added question {self.id} answer_given={self.answer_given!r} "
                f"isn't a key in answers."
            )
        return self

    @property
    def max_score(self) -> int:
        return max((a.score for a in self.answers.values() if a.score is not None), default=0)


# ---------------------------------------------------------------------------
# Session log
# ---------------------------------------------------------------------------


class RoutedCanonicalEntry(BaseModel):
    """One canonical question's disposition in this session.

    Mirrors the `routed_canonical_questions` schema in AGENT_CONTRACT.md.
    """

    question_id: str
    phase_disposition: PhaseDisposition
    answer_given: str | None = None
    skip_reason: SkipReason | None = None
    evidence: str | None = None
    original_score: dict[str, int] | None = None
    effective_score: dict[str, int] | None = None
    de_weighting_justification: str | None = None

    @model_validator(mode="after")
    def _disposition_invariants(self) -> Self:
        if self.phase_disposition == PhaseDisposition.SKIPPED:
            if self.skip_reason is None:
                raise ValueError("Skipped questions must carry a skip_reason.")
            if self.skip_reason == SkipReason.INFERRED_FROM_BRAINDUMP:
                if self.answer_given is None:
                    raise ValueError(
                        "Inferred skip must record the inferred answer in `answer_given`."
                    )
                if self.evidence is None:
                    raise ValueError("Inferred skip must record `evidence`.")
        elif self.phase_disposition == PhaseDisposition.ASKED:
            if self.answer_given is None:
                raise ValueError("Asked questions must carry `answer_given`.")
        elif self.phase_disposition == PhaseDisposition.ASKED_THEN_DE_WEIGHTED:
            if self.answer_given is None:
                raise ValueError("De-weighted questions must still carry `answer_given`.")
            if self.effective_score is None or self.original_score is None:
                raise ValueError("De-weighted questions must record original + effective scores.")
            if self.de_weighting_justification is None:
                raise ValueError("De-weighted questions must carry a justification.")
        return self


# ---------------------------------------------------------------------------
# Playback
# ---------------------------------------------------------------------------


class PlaybackBullet(BaseModel):
    """One grouped thematic summary line shown to the user during playback.

    `source_question_ids` is the back-mapping decided in spec discussion:
    each bullet groups multiple underlying questions, and the IDs let
    user corrections rebind to the right canonicals.
    """

    summary: str
    source_question_ids: list[str]
    inferred_answer_key: str | None = None


class Correction(BaseModel):
    """A user correction during playback. Binds to one canonical question."""

    question_id: str
    prior_answer_key: str
    corrected_answer_key: str
    note: str | None = None


class Playback(BaseModel):
    """The playback step's record. Per the contract."""

    inferred_dimensions: list[PlaybackBullet] = Field(default_factory=list)
    user_corrections: list[Correction] = Field(default_factory=list)
    final_state: dict[str, str] = Field(
        default_factory=dict,
        description="Final answer key per question_id after corrections.",
    )
    skipped: bool = False


# ---------------------------------------------------------------------------
# Score + budget
# ---------------------------------------------------------------------------


class PerDeductionTrace(BaseModel):
    """One row in the score-reconstruction trace."""

    question_id: str
    answer_key: str
    source: QuestionSource
    score_contribution: int


class ScoreComputation(BaseModel):
    """The score math, laid out for audit."""

    raw_deductions_canonical: int
    raw_deductions_agent_added: int
    raw_deductions_total: int
    cohort_multiplier: float
    amplified_deductions: float
    final_score: int = Field(ge=0, le=99)
    per_deduction_trace: list[PerDeductionTrace] = Field(default_factory=list)


class ShiftBudgetAccounting(BaseModel):
    """Final budget statement at session-end. See `BudgetLedger` for the live version."""

    canonical_max_session: int
    shift_budget: float
    phase_1_questions_asked: int
    phase_1_hit_question_cap: bool
    budget_spent_additions: int
    budget_spent_de_weighting: int
    budget_spent_total: int
    budget_remaining: float

    @model_validator(mode="after")
    def _budget_invariant(self) -> Self:
        if self.budget_spent_total > self.shift_budget:
            raise ValueError(
                f"Session invalid: budget_spent_total={self.budget_spent_total} > "
                f"shift_budget={self.shift_budget}."
            )
        if (
            self.budget_spent_total
            != self.budget_spent_additions + self.budget_spent_de_weighting
        ):
            raise ValueError(
                "budget_spent_total must equal additions + de_weighting; got "
                f"total={self.budget_spent_total}, additions={self.budget_spent_additions}, "
                f"de_weighting={self.budget_spent_de_weighting}."
            )
        return self


# ---------------------------------------------------------------------------
# Final report — the structured output the agent returns
# ---------------------------------------------------------------------------


class FinalReport(BaseModel):
    """Top-level session log. The agent's structured output at finalisation."""

    session_id: str
    routed_canonical_questions: list[RoutedCanonicalEntry] = Field(default_factory=list)
    agent_added_questions: list[AgentAddedQuestion] = Field(default_factory=list)
    playback: Playback
    score_computation: ScoreComputation
    shift_budget_accounting: ShiftBudgetAccounting

    def to_markdown(self) -> str:
        """Render the user-facing report. Bare-bones; iterate later."""
        lines: list[str] = []
        lines.append("# Sweet Summer Child Score — assessment report")
        lines.append("")
        lines.append(f"**Final score: {self.score_computation.final_score} / 99**")
        lines.append("")
        if self.playback.skipped:
            lines.append(
                "_Note: playback was skipped, so this score is provisional._"
            )
            lines.append("")

        lines.append("## What the agent concluded about your system")
        lines.append("")
        for bullet in self.playback.inferred_dimensions:
            lines.append(f"- {bullet.summary}")
        if self.playback.user_corrections:
            lines.append("")
            lines.append("### Your corrections during playback")
            for c in self.playback.user_corrections:
                lines.append(
                    f"- `{c.question_id}`: {c.prior_answer_key} → "
                    f"**{c.corrected_answer_key}**"
                    + (f" ({c.note})" if c.note else "")
                )

        lines.append("")
        lines.append("## Recommendations")
        lines.append("")
        lines.append("_See per-deduction trace for the questions that drove the score._")

        lines.append("")
        lines.append("## Score math")
        sc = self.score_computation
        lines.append(
            f"- Raw canonical deductions: {sc.raw_deductions_canonical}\n"
            f"- Raw agent-added deductions: {sc.raw_deductions_agent_added}\n"
            f"- Cohort multiplier (Q-cohort_size): ×{sc.cohort_multiplier}\n"
            f"- Amplified deductions: {sc.amplified_deductions:.1f}\n"
            f"- **Final score: 99 − {sc.amplified_deductions:.1f} = {sc.final_score}**"
        )

        lines.append("")
        lines.append("## Shift budget accounting")
        sb = self.shift_budget_accounting
        lines.append(
            f"- Canonical max for this session: {sb.canonical_max_session}\n"
            f"- Shift budget (25%): {sb.shift_budget:.1f}\n"
            f"- Spent on agent additions: {sb.budget_spent_additions}\n"
            f"- Spent on Phase 2 de-weightings: {sb.budget_spent_de_weighting}\n"
            f"- Remaining: {sb.budget_remaining:.1f}\n"
            f"- Phase 1 questions asked: {sb.phase_1_questions_asked}"
            + ("  _(hit hard cap)_" if sb.phase_1_hit_question_cap else "")
        )

        return "\n".join(lines)

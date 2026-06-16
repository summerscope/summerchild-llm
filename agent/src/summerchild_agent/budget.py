"""
Shift-budget accounting.

Two-phase model per AGENT_CONTRACT.md v1.1:

- Phase 1: adding an agent-authored question consumes its max-possible score.
- Phase 2: de-weighting a canonical question consumes the reduction in that
  question's *max-possible* contribution (i.e. `max(original_scores) -
  max(effective_scores)`).

`BudgetLedger` is the live ledger the agent's tools mutate. At finalisation
it produces a `ShiftBudgetAccounting` for the session log.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import AgentAddedQuestion, CanonicalQuestion, ShiftBudgetAccounting


@dataclass(slots=True)
class BudgetLedger:
    """Live shift-budget tracker.

    Fields are mutated by the agent's tools and validated against the
    fraction-of-canonical cap on every adjustment.
    """

    fraction: float = 0.25
    canonical_max_session: int = 0
    spent_additions: int = 0
    spent_de_weighting: int = 0
    phase_1_questions_asked: int = 0
    phase_1_hit_question_cap: bool = False
    _asked_question_ids: set[str] = field(default_factory=set)

    @property
    def shift_budget(self) -> float:
        return self.fraction * self.canonical_max_session

    @property
    def spent_total(self) -> int:
        return self.spent_additions + self.spent_de_weighting

    @property
    def remaining(self) -> float:
        return self.shift_budget - self.spent_total

    # ---- Phase 1 mutations -------------------------------------------------

    def record_canonical_asked(self, question: CanonicalQuestion) -> None:
        """Phase 1: a canonical question was asked. Adds to CANONICAL_MAX_SESSION."""
        if question.id in self._asked_question_ids:
            return
        self._asked_question_ids.add(question.id)
        self.canonical_max_session += question.max_score
        self.phase_1_questions_asked += 1

    def record_agent_added(self, question: AgentAddedQuestion) -> None:
        """Phase 1: an agent-authored question was asked. Consumes budget."""
        if question.id in self._asked_question_ids:
            return
        self.check_addition_fits(question.max_score)
        self._asked_question_ids.add(question.id)
        self.spent_additions += question.max_score
        self.phase_1_questions_asked += 1

    def hit_question_cap(self) -> None:
        """Marks that Phase 1 ended because of the hard question cap."""
        self.phase_1_hit_question_cap = True

    # ---- Phase 2 mutations -------------------------------------------------

    def record_de_weight(self, original_max: int, effective_max: int) -> None:
        """Phase 2: a canonical question was de-weighted; cost = drop in max contribution."""
        if effective_max > original_max:
            raise ValueError(
                f"De-weighting can only reduce: effective_max={effective_max} > "
                f"original_max={original_max}."
            )
        cost = original_max - effective_max
        if cost == 0:
            return
        self.check_de_weight_fits(cost)
        self.spent_de_weighting += cost

    # ---- Pre-flight checks (so tools can `ModelRetry` cleanly) -------------

    def check_addition_fits(self, max_score: int) -> None:
        if self.spent_total + max_score > self.shift_budget:
            raise BudgetExceeded(
                f"Adding a question with max_score={max_score} would push budget "
                f"spent to {self.spent_total + max_score}, exceeding shift_budget="
                f"{self.shift_budget:.2f}."
            )

    def check_de_weight_fits(self, cost: int) -> None:
        if self.spent_total + cost > self.shift_budget:
            raise BudgetExceeded(
                f"De-weight reduction of {cost} would push budget spent to "
                f"{self.spent_total + cost}, exceeding shift_budget="
                f"{self.shift_budget:.2f}."
            )

    # ---- Finalisation ------------------------------------------------------

    def to_accounting(self) -> ShiftBudgetAccounting:
        """Produce the session-log block. Raises on invariant violation."""
        return ShiftBudgetAccounting(
            canonical_max_session=self.canonical_max_session,
            shift_budget=self.shift_budget,
            phase_1_questions_asked=self.phase_1_questions_asked,
            phase_1_hit_question_cap=self.phase_1_hit_question_cap,
            budget_spent_additions=self.spent_additions,
            budget_spent_de_weighting=self.spent_de_weighting,
            budget_spent_total=self.spent_total,
            budget_remaining=self.remaining,
        )


class BudgetExceeded(ValueError):
    """Raised when an adjustment would overrun the shift budget."""

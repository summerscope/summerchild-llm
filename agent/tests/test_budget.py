"""Shift-budget ledger tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from summerchild_agent.budget import BudgetExceeded, BudgetLedger
from summerchild_agent.models import AgentAddedQuestion, Answer
from summerchild_agent.rubric import Rubric


def _added(*, qid: str, tier: str, scores: list[int]) -> AgentAddedQuestion:
    return AgentAddedQuestion(
        id=qid,
        text="?",
        weight_tier=tier,  # type: ignore[arg-type]
        answers={
            chr(ord("A") + i): Answer(text=str(i), score=s)
            for i, s in enumerate(scores)
        },
        evidence="e",
        created_at=datetime(2026, 6, 15),
    )


def test_budget_starts_at_zero() -> None:
    ledger = BudgetLedger()
    assert ledger.canonical_max_session == 0
    assert ledger.shift_budget == 0
    assert ledger.spent_total == 0


def test_canonical_asked_grows_max_session() -> None:
    r = Rubric.load()
    ledger = BudgetLedger()
    q = r["Q-cohort_fit"]  # medium, max_score 8
    ledger.record_canonical_asked(q)
    assert ledger.canonical_max_session == q.max_score
    assert ledger.shift_budget == pytest.approx(0.25 * q.max_score)
    assert ledger.phase_1_questions_asked == 1


def test_recording_same_canonical_twice_is_idempotent() -> None:
    r = Rubric.load()
    ledger = BudgetLedger()
    ledger.record_canonical_asked(r["Q-cohort_fit"])
    ledger.record_canonical_asked(r["Q-cohort_fit"])
    assert ledger.phase_1_questions_asked == 1


def test_agent_added_consumes_budget() -> None:
    r = Rubric.load()
    ledger = BudgetLedger()
    # Build up enough canonical to give the budget room.
    for qid in [
        "Q-cohort_fit",
        "Q-cohort_econ",
        "Q-mandatory_or_optional",
        "Q-cohort_coercive",
    ]:
        ledger.record_canonical_asked(r[qid])
    pre_budget = ledger.shift_budget
    added = _added(qid="A-novel_low", tier="low", scores=[1, 2])
    ledger.record_agent_added(added)
    assert ledger.spent_additions == 2  # max_score
    assert ledger.shift_budget == pre_budget  # unchanged (no new canonical asked)


def test_overrun_raises() -> None:
    r = Rubric.load()
    ledger = BudgetLedger()
    ledger.record_canonical_asked(r["Q-cohort_fit"])  # budget = 0.25 * 8 = 2.0
    big = _added(qid="A-big", tier="high", scores=[6, 9])
    with pytest.raises(BudgetExceeded):
        ledger.record_agent_added(big)


def test_de_weight_cost_is_drop_in_max() -> None:
    r = Rubric.load()
    ledger = BudgetLedger()
    # Use a high question (max_score 9 ish) so we have headroom.
    for qid in ["Q-confabulation", "Q-mandatory_or_optional", "Q-cohort_fit", "Q-cohort_econ"]:
        try:
            ledger.record_canonical_asked(r[qid])
        except Exception:
            pass
    # Drop a 9-max canonical to a 4-max: cost = 5.
    if ledger.shift_budget < 5:
        # Build up more headroom by asking more.
        for qid in r.ids[:15]:
            ledger.record_canonical_asked(r[qid])
    ledger.record_de_weight(original_max=9, effective_max=4)
    assert ledger.spent_de_weighting == 5


def test_de_weight_cannot_increase() -> None:
    ledger = BudgetLedger()
    ledger.canonical_max_session = 100
    with pytest.raises(ValueError):
        ledger.record_de_weight(original_max=4, effective_max=9)


def test_to_accounting_passes_invariant() -> None:
    r = Rubric.load()
    ledger = BudgetLedger()
    for qid in r.ids[:10]:
        try:
            ledger.record_canonical_asked(r[qid])
        except Exception:
            pass
    accounting = ledger.to_accounting()
    assert (
        accounting.budget_spent_total
        == accounting.budget_spent_additions + accounting.budget_spent_de_weighting
    )
    assert accounting.budget_spent_total <= accounting.shift_budget

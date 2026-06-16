"""Pydantic model tests — invariants on session-log shapes."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from summerchild_agent.models import (
    AgentAddedQuestion,
    Answer,
    Correction,
    PhaseDisposition,
    Playback,
    PlaybackBullet,
    Recommendation,
    Reference,
    RoutedCanonicalEntry,
    ScoreComputation,
    ShiftBudgetAccounting,
    SkipReason,
)


def test_skipped_must_carry_skip_reason() -> None:
    with pytest.raises(ValidationError):
        RoutedCanonicalEntry(
            question_id="Q-x", phase_disposition=PhaseDisposition.SKIPPED
        )


def test_inferred_skip_must_have_evidence_and_answer() -> None:
    with pytest.raises(ValidationError):
        RoutedCanonicalEntry(
            question_id="Q-x",
            phase_disposition=PhaseDisposition.SKIPPED,
            skip_reason=SkipReason.INFERRED_FROM_BRAINDUMP,
        )


def test_asked_requires_answer() -> None:
    with pytest.raises(ValidationError):
        RoutedCanonicalEntry(
            question_id="Q-x", phase_disposition=PhaseDisposition.ASKED
        )


def test_de_weighted_requires_scores_and_justification() -> None:
    with pytest.raises(ValidationError):
        RoutedCanonicalEntry(
            question_id="Q-x",
            phase_disposition=PhaseDisposition.ASKED_THEN_DE_WEIGHTED,
            answer_given="A",
        )


def test_agent_added_palette_enforced() -> None:
    # high tier requires scores in 6-9
    with pytest.raises(ValidationError):
        AgentAddedQuestion(
            id="A-bad",
            text="?",
            weight_tier="high",
            answers={"A": Answer(text="x", score=3)},  # 3 not in 6-9
            evidence="e",
            created_at="2026-06-15T00:00:00",  # type: ignore[arg-type]
        )


def test_shift_budget_accounting_invariant() -> None:
    with pytest.raises(ValidationError):
        ShiftBudgetAccounting(
            canonical_max_session=10,
            shift_budget=2.5,
            phase_1_questions_asked=5,
            phase_1_hit_question_cap=False,
            budget_spent_additions=2,
            budget_spent_de_weighting=2,
            budget_spent_total=4,  # exceeds 2.5
            budget_remaining=-1.5,
        )


def test_score_computation_bounded_0_99() -> None:
    sc = ScoreComputation(
        raw_deductions_canonical=10,
        raw_deductions_agent_added=0,
        raw_deductions_total=10,
        cohort_multiplier=1.0,
        amplified_deductions=10.0,
        final_score=89,
    )
    assert 0 <= sc.final_score <= 99
    with pytest.raises(ValidationError):
        ScoreComputation(
            raw_deductions_canonical=10,
            raw_deductions_agent_added=0,
            raw_deductions_total=10,
            cohort_multiplier=1.0,
            amplified_deductions=10.0,
            final_score=100,
        )


def test_playback_bullets_carry_back_mapping() -> None:
    # This is the spec amendment we agreed: each bullet carries source_question_ids
    b = PlaybackBullet(
        summary="Cohort vulnerability: medium",
        source_question_ids=[
            "Q-cohort_econ",
            "Q-cohort_psych",
            "Q-cohort_coercive",
        ],
    )
    assert b.source_question_ids == [
        "Q-cohort_econ",
        "Q-cohort_psych",
        "Q-cohort_coercive",
    ]


def test_recommendation_round_trips() -> None:
    rec = Recommendation(
        tradeoff="X vs Y",
        considerations="Mind Z.",
        references=[Reference(source="EU AI Act", ref="Art 14", topic="Oversight")],
    )
    assert rec.references[0].source == "EU AI Act"


def test_correction_shape() -> None:
    c = Correction(
        question_id="Q-cohort_fit",
        prior_answer_key="C",
        corrected_answer_key="D",
        note="bigger cultural gap than I first said",
    )
    assert c.corrected_answer_key == "D"


def test_playback_empty_default_ok() -> None:
    p = Playback()
    assert p.skipped is False
    assert p.inferred_dimensions == []
    assert p.user_corrections == []

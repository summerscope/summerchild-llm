"""Rubric loader + gating evaluation tests."""

from __future__ import annotations

from summerchild_agent.rubric import PrereqExpr, Rubric


def test_rubric_loads_47_questions() -> None:
    r = Rubric.load()
    assert len(r) == 47


def test_calibration_questions_present() -> None:
    r = Rubric.load()
    for qid in ("Q-cohort_size", "Q-cohort_fit", "Q-llm_depth"):
        assert qid in r


def test_q_cohort_size_carries_multipliers() -> None:
    r = Rubric.load()
    q = r["Q-cohort_size"]
    for ans in q.answers.values():
        assert ans.multiplier is not None
        assert 0.5 <= ans.multiplier <= 1.5


def test_q_llm_depth_carries_depth_sets() -> None:
    r = Rubric.load()
    q = r["Q-llm_depth"]
    expected = {"none", "point", "pervasive", "agentic"}
    actual = {ans.depth_set for ans in q.answers.values()}
    assert actual == expected


def test_prereq_expression_parses() -> None:
    expr = PrereqExpr.parse("Q-foo.answer == 'X'")
    assert expr.qid == "Q-foo"
    assert expr.answer == "X"


def test_gating_depth_in() -> None:
    r = Rubric.load()
    q = r["Q-confabulation"]
    # LLM-subtree question is not applicable when depth is None or 'none'.
    assert not r.is_applicable(q, depth=None, answers_given={})
    assert not r.is_applicable(q, depth="none", answers_given={})
    assert r.is_applicable(q, depth="point", answers_given={})
    assert r.is_applicable(q, depth="agentic", answers_given={})


def test_gating_prerequisite() -> None:
    r = Rubric.load()
    q = r["Q-opt_default"]
    # Not applicable without the prereq satisfied.
    assert not r.is_applicable(q, depth="point", answers_given={})
    # Applicable when Q-mandatory_or_optional == 'Optional'.
    assert r.is_applicable(
        q,
        depth="point",
        answers_given={"Q-mandatory_or_optional": "Optional"},
    )


def test_next_applicable_skips_addressed() -> None:
    r = Rubric.load()
    nxt = r.next_applicable(
        depth=None,
        answers_given={},
        already_addressed={"Q-cohort_size"},
    )
    assert nxt is not None
    assert nxt.id != "Q-cohort_size"

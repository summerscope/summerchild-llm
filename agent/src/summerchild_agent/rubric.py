"""
Canonical rubric loader + gating evaluator.

Reads `questions.json` from the sibling `summerchild-llm` repo (or wherever
`SUMMERCHILD_RUBRIC_DIR` points) and provides:

- `Rubric` — an immutable container of canonical questions, indexed by id.
- Gating evaluation against a partial set of answers (which questions are
  applicable given what we know so far).
- Walker helper that mirrors the static-client behaviour for testing.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from pydantic import TypeAdapter

from .models import (
    CanonicalQuestion,
    DepthGate,
    DepthSet,
    PrereqGate,
)

DEFAULT_RUBRIC_DIR = Path(__file__).resolve().parents[3]
"""Default rubric location: the repo root (where questions.json + AGENT_CONTRACT.md live).
File layout: <repo>/agent/src/summerchild_agent/rubric.py → parents[3] = <repo>."""

# The legacy "Results" entry in the JSON is a sentinel for the static walker,
# not a question. Filter it out on load.
_LEGACY_SENTINEL_IDS = {"Results"}

# Expression form: Q-id.answer == 'value'
_PREREQ_RE = re.compile(
    r"^\s*(?P<qid>[A-Za-z0-9_\-]+)\.answer\s*==\s*'(?P<value>[^']*)'\s*$"
)


@dataclass(frozen=True)
class PrereqExpr:
    """Parsed prerequisite expression."""

    qid: str
    answer: str

    @classmethod
    def parse(cls, expr: str) -> Self:
        m = _PREREQ_RE.match(expr)
        if not m:
            raise ValueError(
                f"Could not parse prerequisite expression: {expr!r}. "
                f"Expected form: Q-id.answer == 'value'."
            )
        return cls(qid=m["qid"], answer=m["value"])


class Rubric:
    """Immutable view over the canonical questions, indexed by id."""

    __slots__ = ("_order", "_questions_by_id")

    def __init__(self, questions: list[CanonicalQuestion]) -> None:
        self._questions_by_id: dict[str, CanonicalQuestion] = {q.id: q for q in questions}
        self._order: tuple[str, ...] = tuple(q.id for q in questions)

    @classmethod
    def load(cls, rubric_dir: Path | str | None = None) -> Self:
        """Read `questions.json` from `rubric_dir` (or env, or default sibling repo)."""
        path = _resolve_rubric_path(rubric_dir)
        raw = json.loads(path.read_text())
        usable = [entry for entry in raw if entry.get("id") not in _LEGACY_SENTINEL_IDS]
        adapter = TypeAdapter(list[CanonicalQuestion])
        questions = adapter.validate_python(usable)
        return cls(questions)

    # ---- indexing -----------------------------------------------------

    def __len__(self) -> int:
        return len(self._questions_by_id)

    def __iter__(self):
        return (self._questions_by_id[qid] for qid in self._order)

    def __getitem__(self, qid: str) -> CanonicalQuestion:
        return self._questions_by_id[qid]

    def __contains__(self, qid: object) -> bool:
        return qid in self._questions_by_id

    @property
    def ids(self) -> tuple[str, ...]:
        return self._order

    # ---- gating -------------------------------------------------------

    def is_applicable(
        self,
        question: CanonicalQuestion,
        *,
        depth: DepthSet | None,
        answers_given: Mapping[str, str],
    ) -> bool:
        """True if `question` is applicable given the current session state."""
        if question.gating is None:
            return True
        if isinstance(question.gating, DepthGate):
            if depth is None:
                return False
            return depth in question.gating.depth_in
        if isinstance(question.gating, PrereqGate):
            try:
                expr = PrereqExpr.parse(question.gating.prerequisite)
            except ValueError:
                # Malformed gating; default to applicable so we surface, don't drop silently.
                return True
            given = answers_given.get(expr.qid)
            return given == expr.answer
        return True

    def applicable_questions(
        self,
        *,
        depth: DepthSet | None,
        answers_given: Mapping[str, str],
    ) -> list[CanonicalQuestion]:
        """All canonical questions currently applicable given partial state."""
        return [
            q
            for q in self
            if self.is_applicable(q, depth=depth, answers_given=answers_given)
        ]

    def next_applicable(
        self,
        *,
        depth: DepthSet | None,
        answers_given: Mapping[str, str],
        already_addressed: set[str],
    ) -> CanonicalQuestion | None:
        """Next applicable question that hasn't yet been asked or skipped.

        Iterates canonical order; the agent isn't bound to follow it but
        gets this as a hint.
        """
        for q in self:
            if q.id in already_addressed:
                continue
            if self.is_applicable(q, depth=depth, answers_given=answers_given):
                return q
        return None


def _resolve_rubric_path(rubric_dir: Path | str | None) -> Path:
    """Resolve the location of questions.json. Tries the arg, the env var, then the default."""
    candidates: list[Path] = []
    if rubric_dir is not None:
        candidates.append(Path(rubric_dir))
    env_val = os.environ.get("SUMMERCHILD_RUBRIC_DIR")
    if env_val:
        candidates.append(Path(env_val))
    candidates.append(DEFAULT_RUBRIC_DIR)

    for cand in candidates:
        p = cand / "questions.json"
        if p.is_file():
            return p
    raise FileNotFoundError(
        f"Could not find questions.json in any of: "
        f"{[str(c / 'questions.json') for c in candidates]}"
    )

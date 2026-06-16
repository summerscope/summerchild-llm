"""
Managed-variable resolution.

In production these come from Logfire (`template_var` / remote variables) so
they can be tuned from the Logfire UI without redeploys. For local dev we
short-circuit to env-var overrides and then to hard-coded defaults so the
agent runs without any cloud auth.

This module owns the *defaults* and the *env-var fallback paths*. The Logfire
wiring lives in `logfire_config.py` and consumes this module's defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_SYSTEM_PROMPT = """\
You are the Sweet Summer Child Score assessor — a sharp, warm, slightly arch
voice who has seen a lot of automated decision systems go badly and is here
to help the user not repeat that. Think: protective senior colleague.

You are conducting a structured risk assessment of a system the user is
building or has built. You consume the canonical rubric (47 questions across
calibration, shared-scan, LLM-subtree, and cross-cutting sections) and the
agent contract that governs how you may deviate from it.

## How you talk

- Warm but unflinching. Do not flatter. Do not over-soften high-risk findings.
- Plain language. Avoid jargon unless the user is using it.
- Short — one question per turn, or one observation. Do not dump.
- When the user braindumps, extract what you can and reflect it back.
- You may rephrase canonical question text for the user's domain. You may NOT
  change the semantic meaning of the question or the answer ladder.

## How you operate (rules from AGENT_CONTRACT.md v1.1)

The session has two phases.

**Phase 1 — Conversation.** Tools available: `ask_canonical`, `skip_canonical`,
`infer_from_braindump`, `add_agent_question`, `record_user_answer`,
`end_phase_1`. You may:
- Ask canonical questions in any order. Front-load `Q-cohort_size`,
  `Q-cohort_fit`, `Q-llm_depth` — they route everything else.
- Skip canonical questions when gating rules them out, when a previous
  answer makes them moot, or when the user makes clear they don't apply.
- Add new questions (with `add_agent_question`) when the conversation
  surfaces a thread canonical doesn't anticipate. Use the weight palette:
  low=1-2 / medium=3-5 / high=6-9 per answer.
- Infer canonical answers from braindump text and record the inferred
  answer with evidence.
- End Phase 1 (`end_phase_1`) when you have enough signal OR when you
  hit the question cap.

**Bias rule:** prefer asking a canonical question over adding a new one
when you're uncertain. Phase 1 additions eat the budget immediately;
Phase 2 de-weighting can recover from over-asking but not over-adding.

**Phase 2 — Re-weight + playback.** Tools available: `de_weight`,
`present_playback`, `record_correction`, `finalise`. No new questions in
this phase. You:
1. Review the full conversation.
2. De-weight any canonical questions where context makes them less
   load-bearing than their canonical weight assumes.
3. Run `present_playback` with grouped thematic bullets summarising what
   you concluded. Each bullet groups multiple underlying questions and
   carries a back-mapping so user corrections rebind correctly.
4. Apply corrections via `record_correction`.
5. Call `finalise` to commit the score and emit the report.

## Constraints (do not break)

- Shift budget = 25% × canonical_max_session. `Σ |adjustments| ≤ budget`.
- Soft target: 15 questions total. Hard cap: 30. Hitting the cap MUST end
  Phase 1.
- Skipping is free (no budget cost). De-weighting is Phase 2 only.
- Every adjustment carries a justification recorded by the tool.
- Refuse to call `finalise` if budget is exceeded — the session is invalid.

When in doubt, ask a clarifying question instead of guessing. Be honest
when you can't tell.
"""


@dataclass(frozen=True)
class AgentBounds:
    """Runtime bounds on the agent's authority. All tunable via managed vars."""

    shift_budget_fraction: float
    soft_question_target: int
    hard_question_cap: int
    model_id: str


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return float(val)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None or val.strip() == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _env_str(key: str, default: str) -> str:
    val = os.environ.get(key)
    return val if val and val.strip() else default


def default_system_prompt() -> str:
    """Local-dev fallback for the system prompt managed variable.

    When Logfire is reachable, `logfire_config.resolve_system_prompt` will
    fetch the remote `prompt__system` variable and use that instead. When
    it's not reachable, callers fall back to this.
    """
    return DEFAULT_SYSTEM_PROMPT


def default_agent_bounds() -> AgentBounds:
    """Local-dev fallback for the agent-bounds managed variables.

    Env vars override the hard-coded defaults so local iteration on bounds
    is possible without a Logfire login.
    """
    return AgentBounds(
        shift_budget_fraction=_env_float("SUMMERCHILD_SHIFT_BUDGET_FRACTION", 0.25),
        soft_question_target=_env_int("SUMMERCHILD_SOFT_QUESTION_TARGET", 15),
        hard_question_cap=_env_int("SUMMERCHILD_HARD_QUESTION_CAP", 30),
        model_id=_env_str("SUMMERCHILD_MODEL", "anthropic:claude-sonnet-4-6"),
    )

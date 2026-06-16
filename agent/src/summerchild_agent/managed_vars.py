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
- When the user braindumps, extract what you can and quietly record it. Do
  NOT then say it back to them — there's a review step at the end that
  surfaces everything you inferred. The only time you echo a previous
  answer is when echoing it directly leads into the next question.
- **No meta-narration.** Never say things like *"Now the cohort fit
  question:"* or *"Next up I want to ask about..."* The user shouldn't see
  the rubric's machinery. Just ask the next question in their domain's
  language, full stop.
- **No reflexive praise / framing** like *"that's a meaningful footprint"*,
  *"interesting wrinkle"*, *"good answer"*. These are noise.
- You may rephrase canonical question text for the user's domain. You may NOT
  change the semantic meaning of the question or the answer ladder.

## Question types — IMPORTANT

You drive the conversation using exactly TWO question shapes:

1. **Open-ended (braindump).** Ask one short, focused question. The user
   answers in free text; extract every signal you can and reflect it back.
   Use this when the rubric has no obvious answer ladder OR when you want
   rich context fast — a few well-aimed open questions can cover ten
   canonical questions' worth of signal at once.

2. **Multi-choice (buttons).** When a canonical question's
   `preferred_modality` is `"buttons"`, **present only the question text**.
   The UI automatically renders each answer choice as a clickable button
   below your message, AND adds an "I'm not sure" button. Do NOT list the
   choices yourself in markdown (no `**A** — 1-100` lists, no inline
   `A) Yes B) No`). Listing them duplicates the UI and gives the user two
   surfaces to pick from. Keep your message conversational and short —
   e.g. *"Pick the band that fits your realistic deployment target."*

If the user picks "I'm not sure" (or types something equivalent), do NOT
guess on their behalf. Either ask one short clarifying question to narrow
it down, OR skip the canonical question with
`skip_reason="not_applicable_in_context"` if it's genuinely unanswerable
in their context. Skipping beats donkey-voting.

## Question selection strategy (after the initial braindump)

The canonical rubric in `questions.json` is **already ordered** for
engagement: easy gating questions first (they prune subtrees and feel
like quick wins), then concrete factual context, then reflective
context, with the hardest projection questions (`Q-cohort_size`,
`Q-cohort_fit`) last so they land after the user already has momentum.

**Default behaviour: walk the rubric in canonical order.** That's the
right call ~90% of the time and matches the engagement design baked
into the file.

Deviate only when the user's braindump makes a different next question
obviously more salient — e.g., they describe a scoring system applied
to schoolchildren, so jumping to `Q-cohort_coercive` before its usual
position is the right move. When in doubt, follow canonical order.

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


# ---------------------------------------------------------------------------
# Logfire managed variable for the system prompt.
#
# Declared once at module load. `logfire.var(...)` returns immediately even if
# Logfire isn't configured — until then, `.get().value` falls back to the
# `default=` here. After `logfire.configure(...)` runs (server lifespan) and
# `logfire.variables_push()` is run (one-off, via `scripts/push_variables.py`),
# the value comes from the Logfire UI and refreshes every ~60s.
# ---------------------------------------------------------------------------

import logfire as _logfire  # local alias so we don't accidentally double-import

SYSTEM_PROMPT_VAR = _logfire.var(
    name="agent_system_prompt",
    type=str,
    default=DEFAULT_SYSTEM_PROMPT,
    description=(
        "Persona + behaviour rules for the SSCS conversational assessor. "
        "Edit here to tune voice, narration policy, question-selection "
        "strategy, etc. Picked up on the next polling cycle (~60s); takes "
        "effect on the agent's very next conversation turn."
    ),
)

MODEL_ID_VAR = _logfire.var(
    name="agent_model_id",
    type=str,
    default=_env_str("SUMMERCHILD_MODEL", "anthropic:claude-sonnet-4-6"),
    description=(
        "PydanticAI model identifier, e.g. 'anthropic:claude-sonnet-4-6' "
        "or 'openai:gpt-5.2'. NOTE: captured at backend startup — changing "
        "this in the UI requires a backend restart to take effect."
    ),
)

SHIFT_BUDGET_FRACTION_VAR = _logfire.var(
    name="agent_shift_budget_fraction",
    type=float,
    default=_env_float("SUMMERCHILD_SHIFT_BUDGET_FRACTION", 0.25),
    description=(
        "Fraction of CANONICAL_MAX_SESSION the agent can redistribute via "
        "additions + de-weightings. Per AGENT_CONTRACT.md default is 0.25. "
        "Resolved per-session — new sessions pick up changes immediately; "
        "in-flight sessions keep their original value."
    ),
)

SOFT_QUESTION_TARGET_VAR = _logfire.var(
    name="agent_soft_question_target",
    type=int,
    default=_env_int("SUMMERCHILD_SOFT_QUESTION_TARGET", 15),
    description=(
        "Soft target for total questions asked in Phase 1 (canonical + "
        "agent-added). Agent should land near this on average. Resolved "
        "per-session — new sessions pick up changes immediately."
    ),
)

HARD_QUESTION_CAP_VAR = _logfire.var(
    name="agent_hard_question_cap",
    type=int,
    default=_env_int("SUMMERCHILD_HARD_QUESTION_CAP", 30),
    description=(
        "Hard ceiling on Phase 1 questions. Hitting this MUST end Phase 1. "
        "Stops scope blowout. Resolved per-session — new sessions pick up "
        "changes immediately."
    ),
)


def resolve_system_prompt() -> str:
    """Current system prompt — Logfire managed value or `DEFAULT_SYSTEM_PROMPT`.

    Used as the persona `@agent.system_prompt` callback in `agent.py` so the
    prompt is re-resolved on every conversation turn.
    """
    return SYSTEM_PROMPT_VAR.get().value


def default_system_prompt() -> str:
    """Back-compat alias — same as `resolve_system_prompt`. Will be removed."""
    return resolve_system_prompt()


def default_agent_bounds() -> AgentBounds:
    """Resolve the four agent-bound managed variables from Logfire.

    Called once at module load to capture `model_id` into the Agent (frozen
    until backend restart) AND fresh on each new session via
    `make_session_deps`, so new sessions always pick up the latest cap +
    budget values.
    """
    return AgentBounds(
        shift_budget_fraction=SHIFT_BUDGET_FRACTION_VAR.get().value,
        soft_question_target=SOFT_QUESTION_TARGET_VAR.get().value,
        hard_question_cap=HARD_QUESTION_CAP_VAR.get().value,
        model_id=MODEL_ID_VAR.get().value,
    )

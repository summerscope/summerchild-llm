# Agent Contract

This file describes the contract between the **Sweet Summer Child Score canonical rubric** (this repo: `questions.json` and `summerchild.py`) and any **external conversational agent** that consumes the rubric to drive an adaptive assessment.

The agent itself is **not** built in this repo. This document is the spec the agent project consumes to know how to use the rubric responsibly.

---

## Vocabulary

- **Canonical rubric**: the set of questions, weights, gating, and recommendations defined in `questions.json` at a given commit. Stable, audit-traceable.
- **Static client**: a non-agent walker of the canonical rubric (e.g. `summerchild.py`). Asks every question whose gating is satisfied, in order, with no inference.
- **Conversational agent**: an LLM-driven assistant that consumes `questions.json` to drive a richer, adaptive assessment session — collapsing canonical questions into braindumps, asking follow-ups, adding new questions when canonical doesn't cover a thread the user raises, and producing a session log.
- **Session log**: a structured record of every decision the agent made during one assessment session. The audit artifact.
- **Shift budget**: the maximum amount of point-weighted signal the agent is allowed to redistribute (de-weight existing questions or add new ones) within a single session, as a fraction of the canonical signal available for that session.

---

## What the agent CAN do

### Drive the conversation
- Pick the order in which questions are asked.
- Decide when to dig deeper on a particular topic.
- Decide which canonical questions to skip when their answer can be reliably inferred from prior conversation. Inferred answers must still appear in the session log with `skip_reason: "inferred_from_braindump"` and a reference to the source utterance.

### Choose interaction modality per question
- Open text braindump (one question at a time, free-form answer).
- Single-choice buttons (canonical answer ladder).
- Multi-choice buttons (when a question has independent sub-dimensions).
- Hybrid (free-form answer with structured follow-up clarification).

The question schema may carry a `preferred_modality` hint (`"open"` or `"buttons"`). The agent decides per session — the hint is advisory.

### Rephrase canonical question text for context
- Specialise wording for the user's domain (healthcare vs. ed-tech vs. fintech).
- Translate to the user's language.
- Adjust tone.

The semantic meaning and the underlying answer ladder do NOT change. A canonical question and its agent-rephrased version map to the same `question_id` in the session log.

### Add agent-authored questions on the fly
When the canonical rubric doesn't cover a thread the conversation surfaces, the agent may add a new question. Agent-added questions must:

- Be tagged `"source": "agent-added"` in the session log. (Canonical questions are implicitly `"source": "canonical"`.)
- Use the weight palette: `weight_tier` in `{"low", "medium", "high"}` with per-answer `score` values in the appropriate range (`low → 1–2`, `medium → 3–5`, `high → 6–9`).
- Include the standard recommendation structure: `tradeoff`, `considerations`, and optionally `references` (which may be empty if no legislative coverage applies).
- Be logged with:
  - `session_id`
  - `created_at` timestamp
  - `evidence`: the conversation excerpt that prompted the addition

Agent-added questions do NOT get written back to canonical `questions.json` automatically. A separate curation process may promote popular agent-added questions into canonical in a future rubric release.

### De-weight canonical questions for the current session
When the user clarifies that a canonical question is less relevant in their specific context (e.g., "we have no external users at all"), the agent may reduce the `effective_score` per answer on that question for the current session only. Original canonical `score` values are unchanged and remain in `questions.json`. The de-weighting is recorded with:

- Original `score` per answer
- Effective `score` per answer for this session
- Justification text

---

## Shift budget

For each session, the agent operates within a bounded shift budget. The budget bounds how much the agent can deviate from canonical without explicit user input on canonical-defined questions.

```
CANONICAL_MAX_SESSION = sum of max possible scores across all canonical questions
                       routed for this session (after gating by depth + prerequisites)

SHIFT_BUDGET = 0.25 × CANONICAL_MAX_SESSION
```

The agent spends the budget by either:

- **De-weighting a canonical question**: the reduction amount counts against the budget.
- **Adding an agent-authored question**: its max possible score counts against the budget.

The agent CANNOT amplify canonical question weights above their authored maxima. To raise signal on a topic, the agent must add a new question instead.

**Constraint**: `Σ |all adjustments| ≤ SHIFT_BUDGET` at session finalisation.

Every adjustment must carry a justification in the session log. Examples:

- *"De-weighted Q-consent_revocable because user clarified the system has no external users and no PII collection — revocation isn't applicable."*
- *"Added Q-data_leakage_to_training because user described data crossing trust boundaries we hadn't anticipated."*

---

## Session log requirements

Everything the agent decides must be explicit and traceable in the session log. The log MUST include the following sections:

### `routed_canonical_questions`
For every canonical question that the gating tree would have routed for this session (given the user's `Q-cohort_size`, `Q-llm_depth`, etc.):

- `question_id`: the canonical ID.
- `asked`: boolean — did the agent actually ask this question?
- `answer_given`: the answer key (if asked). For inferred answers, this is the value the agent inferred.
- `skip_reason` (if not asked): one of:
  - `"gated_out_by_depth"` — gating ruled this out
  - `"gated_out_by_prerequisite"` — a prereq wasn't met
  - `"inferred_from_braindump"` — agent extracted the answer from earlier conversation (include `evidence` reference)
  - `"de_weighted_to_zero"` — agent declared this fully not applicable in this context
- `evidence` (for inferred answers): conversation excerpt that supports the inferred answer.
- `effective_score` (if de-weighted): the session-effective score values per answer.
- `de_weighting_justification` (if de-weighted): free-text justification.

### `agent_added_questions`
For every question the agent added during the session:

- Full question payload (text, weight_tier, answers with scores, recommendation structure).
- `evidence`: conversation excerpt that prompted the addition.
- `created_at`: timestamp.
- `answer_given`: the answer key the user provided.

### `playback`
The output of the playback step (see below):

- `inferred_dimensions`: structured list of "this is what I concluded about your system on each dimension."
- `user_corrections`: any corrections the user made during playback.
- `final_state`: the dimensions/answers committed to scoring after playback.

### `score_computation`
Showing the layered math:

- `raw_deductions_canonical`: sum of `effective_score` × (count of canonical questions answered, with values).
- `raw_deductions_agent_added`: sum from agent-added questions.
- `raw_deductions_total`: sum.
- `cohort_multiplier`: the value from `Q-cohort_size`.
- `amplified_deductions`: `cohort_multiplier × raw_deductions_total`.
- `final_score`: `clip(99 − amplified_deductions, 0, 99)`.
- `per_deduction_trace`: list of (question_id, answer_key, source, score_contribution) tuples so a reviewer can reconstruct the sum.

### `shift_budget_accounting`
- `canonical_max_session`
- `shift_budget`
- `budget_spent_de_weighting`: sum of |de-weighting amounts|
- `budget_spent_additions`: sum of agent-added max scores
- `budget_spent_total`
- `budget_remaining`

If `budget_spent_total > shift_budget` at session finalisation, the session is invalid and must be reconsidered before scoring is committed.

---

## Playback requirement

Before finalising the score, the agent MUST present a **playback summary** to the user. The format:

> "Based on our conversation, here's what I concluded about your system:
> - Cohort vulnerability: medium (you mentioned average earners, average tech literacy)
> - LLM confabulation handling: not measured (no confidence signal in your system)
> - Agent autonomy scope: limited to read-only actions
> - ...
>
> Did I get any of this wrong?"

The user may correct any inferred answer before the score is committed. Corrections become the authoritative answer for that dimension and are recorded in the session log under `playback.user_corrections`.

If the user wants to skip playback (e.g., they're just curious about the score and don't care about a careful assessment), the agent should:
- Honour that choice
- Record `playback.skipped: true` in the session log
- Note the resulting score is provisional in the rendered output

---

## What the agent CANNOT do

- **Modify the canonical `questions.json` file**. All session-scoped adjustments stay session-scoped.
- **Amplify canonical question weights above their authored maxima**. (Add a new question instead.)
- **Directly set the final score**. The score is computed from the formula; the agent influences inputs only.
- **Skip a canonical question without recording a `skip_reason`**.
- **Skip the playback step without recording `playback.skipped: true`**.
- **Exceed the shift budget** at session finalisation.
- **Combine multiple users' answers into one assessment** without an explicit multi-stakeholder mode (out of scope for v1).

---

## Implementation notes

### Reading `questions.json`
- Walk the array of question objects in order.
- For each, check `gating`:
  - `null` → always applicable
  - `{"depth_in": [...]}` → applicable if `Q-llm_depth.answer.depth_set` is in the list
  - `{"prerequisite": "Q-id.answer == 'X'"}` → applicable if the named question was answered with `X`
- The agent can deviate from order but must record every routed-but-skipped question.

### Cohort multiplier
- The `Q-cohort_size` answer carries a `multiplier` field (range 0.5–1.5). Apply at the score-computation step, not per-question.

### LLM-depth routing
- The `Q-llm_depth` answer carries a `depth_set` field (`"none" | "point" | "pervasive" | "agentic"`). This sets the session depth used by depth-gated questions.

### Recommendation structure
- Each non-trivial answer's `recommendation` is either `null` or `{tradeoff, considerations, references}`.
- `references` is an array of `{source, ref, topic}` objects, possibly empty.
- The legacy flat-string format is tolerated by the static client for backward compatibility but the agent should always emit structured.

---

## Versioning

Breaking changes to this contract require a major-version bump on the rubric (`questions.json`). The current version is **v1.0**. The agent project should pin to a known canonical commit and re-evaluate this contract on each rubric update.

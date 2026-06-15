# Agent Contract

This file describes the contract between the **Sweet Summer Child Score canonical rubric** (this repo: `questions.json` and `summerchild.py`) and any **external conversational agent** that consumes the rubric to drive an adaptive assessment.

The agent itself is **not** built in this repo. This document is the spec the agent project consumes to know how to use the rubric responsibly.

---

## Vocabulary

- **Canonical rubric**: the set of questions, weights, gating, and recommendations defined in `questions.json` at a given commit. Stable, audit-traceable.
- **Static client**: a non-agent walker of the canonical rubric (e.g. `summerchild.py`). Asks every question whose gating is satisfied, in order, with no inference.
- **Conversational agent**: an LLM-driven assistant that consumes `questions.json` to drive a richer, adaptive assessment session — collapsing canonical questions into braindumps, asking follow-ups, adding new questions when canonical doesn't cover a thread the user raises, and producing a session log.
- **Session log**: a structured record of every decision the agent made during one assessment session. The audit artifact.
- **Shift budget**: the maximum amount of point-weighted signal the agent is allowed to redistribute (de-weight canonical questions or add new ones) within a single session, as a fraction of the canonical signal available for that session.

---

## Phases of an assessment session

An assessment runs in **two distinct phases**. Adjustments to canonical weights happen in the second phase only — the agent cannot reliably judge what to re-weight until it has the full conversation context.

### Phase 1: Conversation

The agent collects evidence by asking, skipping, and adding questions.

- **Ask** (canonical or agent-authored): the user provides an answer. The answer's `score` is recorded.
- **Skip** (canonical only): the question is excluded from scoring entirely. Reasons for a skip:
  - `gated_out_by_depth` — LLM-depth routing rules this out
  - `gated_out_by_prerequisite` — a prior-answer prerequisite isn't satisfied
  - `inferred_from_braindump` — the agent extracted the answer from an earlier open-ended response (and records that answer in the log)
  - `not_applicable_in_context` — the user has made it clear this dimension structurally doesn't apply (e.g., "we have no external users")

  Skipping does NOT consume the shift budget. A skip is a binary "doesn't apply" — the question simply isn't counted toward `CANONICAL_MAX_SESSION` and contributes nothing to the score.

- **Add** (agent-authored only): the agent creates a new question because conversation surfaced something canonical doesn't anticipate. The added question's max possible score counts against the shift budget.

**De-weighting does NOT happen in this phase.** The agent does not adjust canonical question weights during the conversation.

### Phase 2: Re-weight + playback

When conversation ends — either because the agent has enough signal or because the hard question cap has been reached — the agent does a single holistic pass:

1. Review all evidence gathered during conversation.
2. **De-weight** canonical questions where the full context now makes them less load-bearing than their authored weight implied. (Example: across the conversation, it became clear the system is internal-only with strong opt-in — so `Q-opt_visibility` carries less risk than its canonical weight assumes.) De-weighting consumes the shift budget.
3. Lock in the final set of adjustments.
4. Run **playback** with the user — show what was concluded per dimension, let the user correct.
5. Compute the final score.

No new questions are added in Phase 2 (the user is no longer in the conversation by the time the agent does the re-weight pass — adding a question with no answer to score against would be incoherent). Threads the agent noticed in retrospect that warrant follow-up should be surfaced during playback, where the user can answer them.

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

### De-weight canonical questions during the re-weight phase
After the conversation ends (Phase 2), the agent may reduce the `effective_score` per answer on a canonical question for the current session. This happens once, holistically, with the full conversation in view — not piecemeal during conversation. Original canonical `score` values are unchanged and remain in `questions.json`. The de-weighting is recorded with:

- Original `score` per answer
- Effective `score` per answer for this session
- Justification text referencing the conversation evidence that supports the de-weighting

The total of all de-weightings + agent-added question scores must not exceed the shift budget (see below).

Note: if the user has *clearly* established during conversation that a canonical question doesn't apply at all, the right move during Phase 1 is to **skip** it (excluded entirely, no budget impact), not to plan a Phase 2 de-weight. De-weighting is for the more nuanced cases where the question still partially applies but at a lower weight than canonical assumes.

---

## Shift budget

The shift budget bounds how much the agent can deviate from canonical weighting within a single session. It applies across both phases of the assessment.

```
CANONICAL_MAX_SESSION = sum of max possible scores across all canonical questions
                       ASKED in this session (Phase 1).
                       Skipped questions are NOT counted toward this total.

SHIFT_BUDGET = 0.25 × CANONICAL_MAX_SESSION
```

The agent spends the budget two ways:

- **Adding an agent-authored question** (Phase 1): its max possible score counts against the budget.
- **De-weighting a canonical question** (Phase 2): the reduction amount per question (summed across its answer ladder) counts against the budget.

The agent CANNOT amplify canonical question weights above their authored maxima. To raise signal on a topic, the agent must add a new question in Phase 1.

**Constraint**: `Σ |all adjustments| ≤ SHIFT_BUDGET` at session finalisation.

Every adjustment must carry a justification in the session log. Examples:

- *Phase 1, added question:* "Added Q-data_leakage_to_training because user described data crossing trust boundaries we hadn't anticipated."
- *Phase 2, de-weight:* "De-weighted Q-consent_revocable from medium to low because conversation established the system is internal-only with strong opt-in — revocation matters less than canonical assumes."

A note on Phase 1 vs Phase 2 budget pressure: agent-added questions in Phase 1 consume the budget immediately, leaving less room for Phase 2 de-weighting. The agent should bias toward asking when in doubt during Phase 1 — over-asking is recoverable in Phase 2; over-adding is not.

---

## Maximum questions asked

Every session has both a target and a hard cap on questions asked (canonical asked + agent-authored asked combined):

- **Soft target: 15 questions** total. The agent should plan to land near this on average. Use inference and braindump compression to cover canonical dimensions without explicit ask.
- **Hard cap: 30 questions** total. If the agent reaches 30 questions during Phase 1, it MUST end the conversation phase and proceed to Phase 2 (re-weight + playback). No exceptions.

The cap exists so an assessment feels like a 10-minute exercise, not a 1-hour interrogation. If the agent reaches the cap without enough signal on some dimensions, those dimensions get skipped with `skip_reason: "question_cap_reached"` and the playback should call them out explicitly so the user can confirm or correct.

These values are configurable per deployment, but the existence of a hard cap is non-negotiable. An infinite-question session blows out scope and degrades user trust.

---

## Session log requirements

Everything the agent decides must be explicit and traceable in the session log. The log MUST include the following sections:

### `routed_canonical_questions`
For every canonical question that the gating tree would have routed for this session (given the user's `Q-cohort_size`, `Q-llm_depth`, etc.):

- `question_id`: the canonical ID.
- `phase_disposition`: one of `"asked"`, `"skipped"`, `"asked_then_de_weighted"`.
- `answer_given`: the answer key (if asked). For inferred answers, this is the value the agent inferred during Phase 1.
- `skip_reason` (if `phase_disposition == "skipped"`): one of:
  - `"gated_out_by_depth"` — gating ruled this out
  - `"gated_out_by_prerequisite"` — a prereq wasn't met
  - `"inferred_from_braindump"` — agent extracted the answer from earlier conversation (include `evidence` reference and the inferred answer)
  - `"not_applicable_in_context"` — user clarified during conversation that this dimension structurally doesn't apply
  - `"question_cap_reached"` — Phase 1 hit the hard cap before this question could be asked
- `evidence` (for inferred answers): conversation excerpt that supports the inferred answer.
- `original_score` (if de-weighted): canonical per-answer scores from `questions.json`.
- `effective_score` (if de-weighted): the Phase 2 effective score values per answer.
- `de_weighting_justification` (if de-weighted): free-text justification referencing conversation evidence.

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
- `canonical_max_session`: sum of max possible scores across canonical questions ASKED (not skipped).
- `shift_budget`: `0.25 × canonical_max_session`.
- `phase_1_questions_asked`: count of canonical + agent-added questions asked in Phase 1.
- `phase_1_hit_question_cap`: boolean — did Phase 1 end because of the 30-question hard cap?
- `budget_spent_additions`: sum of agent-added max scores (Phase 1).
- `budget_spent_de_weighting`: sum of |de-weighting amounts| (Phase 2).
- `budget_spent_total`: sum of both.
- `budget_remaining`.

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
- **De-weight canonical questions during the conversation phase**. De-weighting is a Phase 2 holistic operation, not piecemeal during conversation.
- **Add new questions during the re-weight phase**. Phase 2 is closed to new questions (no answers available). Threads that warrant follow-up should be raised during playback so the user can answer them.
- **Exceed the hard question cap (30) in Phase 1**. If the cap is hit, end conversation and proceed to Phase 2.
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

Breaking changes to this contract require a major-version bump on the rubric (`questions.json`). The current contract version is **v1.1** (added Phase 1 / Phase 2 split, hard question cap, clarified that skipping is free of budget cost). The agent project should pin to a known canonical commit and re-evaluate this contract on each rubric update.

# Sweet Summer Child Score — LLM-era rubric

_Risk assessment rubric for automated decision systems, redesigned for the foundation-model era._

This repo holds the **LLM-era canonical rubric** for the Sweet Summer Child Score: a 47-question scan covering classical-ML harms, point-LLM features, pervasive-LLM systems, and fully agentic LLM systems taking real-world actions.

## What's here

| File | Purpose |
|---|---|
| `questions.json` | The canonical rubric. Questions, weights, gating, structured recommendations with legislative references. |
| `summerchild.py` | Static terminal client. Walks the full rubric end-to-end without an agent. Useful as a reference implementation and for verification. |
| `AGENT_CONTRACT.md` | Spec for any external conversational agent that consumes the canonical rubric. Describes what the agent can/can't do, the shift budget, the session log format, and the playback requirement. |
| `LICENSE` | Copied from the original `summerchildpy`. |

## Relationship to `summerchildpy`

This is a **descendant** of [summerchildpy](https://github.com/summerscope/summerchildpy), the original 2020 Sweet Summer Child Score in Python. The original was designed for classical automated decision systems and assumed the operator controlled the model and training data — assumptions that no longer hold in the foundation-model era. This repo:

- Keeps what works: the cohort/posture/Maslow-harm framework, the headline `X / 99` score, the tier names.
- Adds LLM-era harm classes: confabulation, prompt injection, sycophancy, model-version drift, multilingual fairness, agent autonomy (financial / physical / social blast radius), parasocial harms, foundation-model concentration risk, environmental cost, cognitive deskilling.
- Reframes the cohort question to address cultural fit / non-WEIRD deployment risk in addition to scale.
- Reframes the "always outputs a decision" question to address **calibrated confidence and low-confidence escalation** — the LLM-era analogue.
- Adds a routing axis (`Q-llm_depth`) so classical systems aren't asked LLM questions and agentic systems get the full battery.
- Restructures recommendations to surface **tradeoffs and legislative references** (GDPR, EU AI Act, NIST AI RMF, OWASP, etc.) rather than prescribing fixed answers.

The original `summerchildpy` is unchanged and continues to live at its own URL. Use this repo if you're assessing an LLM-based system or want the modernised question set.

## Run the static client (Python)

**Requirements:** Python 3 installed.

```sh
git clone <this-repo-url>
cd summerchild-llm
python3 summerchild.py
```

The static client walks every applicable question in order. Gating is honoured — if you select `llm_depth = none`, the LLM-era questions are skipped automatically.

A typical session takes:
- ~5 minutes for a classical system with `Q-llm_depth = none`
- ~10 minutes for a pervasive LLM system
- ~15 minutes for a full agentic LLM system

## Use with a conversational agent

The static client is one way to run the rubric. A conversational agent (separate project, not in this repo) can consume the same `questions.json` to drive a richer, adaptive assessment: braindump questions that cover multiple canonical dimensions at once, button-click follow-ups for gaps, on-the-fly added questions when the user surfaces something the canonical rubric doesn't cover.

See `AGENT_CONTRACT.md` for the full spec.

## How the score works

You start at **99** — the highest possible. Each question you answer can deduct points depending on the answer you give. The final score is:

```
final = max(0, 99 − cohort_scale × Σ deductions)
```

Where:
- `cohort_scale` is set by `Q-cohort_size` (range 0.5 – 1.5). Small cohorts get a discount on penalties; huge cohorts get amplification beyond raw deductions, because harm at 10M-people scale isn't just proportional to harm at 1-person scale.
- `Σ deductions` is the sum of per-answer score values across every question you answered.

The output shows the layered math: raw deductions → cohort amplification → final score. The original Sweet Summer Child Score hid this layering and produced a "WTF" moment for some users — this version is explicit about how the number was derived.

## Tier names

| Score | Tier | Meaning |
|---|---|---|
| 0 | Beyond the wall | Risk exceeds what the rubric can measure. Reconsider scope. |
| 1–20 | The white walkers | This territory belongs to the undead. Turn around. |
| 21–40 | Fields of thorns | Risky path, but maybe winnable with reinforcements. |
| 41–60 | Winter is coming | Plan for the hardships. |
| 61–80 | The stronghold | Shoring up castle walls, with room to stumble. |
| 81–99 | Castles in the sky | High ground. Don't get complacent. |

## What's been deliberately demoted or cut

The 2020 rubric had several "Section #N" Ok-prompt questions and a geographic-distribution question with no scoring weight. These are cut in favour of substantive replacements. See the original [redesign plan](https://github.com/summerscope/summerchild-llm/blob/main/docs/) for the full rationale (if/when committed to docs).

## Status

Hackathon v1. The question set is intentionally extensible — a conversational agent can add questions within a session, and the canonical rubric will iterate as user data and legislative landscape evolve. Open items (weight palette calibration, legislative reference accuracy, copywriting polish) are tracked in the redesign plan.

## License

See [`LICENSE`](LICENSE). Inherited from the original `summerchildpy`.

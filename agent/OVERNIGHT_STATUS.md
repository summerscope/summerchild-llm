# summerchild-agent — overnight build status

Built by Claude overnight (15→16 Jun 2026) against `summerchild-llm`
AGENT_CONTRACT v1.1.

## TL;DR

The agent, FastAPI backend, and Next.js frontend are all in place,
structurally sound, and pass the tests + builds I could run without
your API keys. **Nothing has talked to an actual LLM yet.** First thing
in the morning: drop your `ANTHROPIC_API_KEY` in `.env`, boot both
servers, and try a conversation. Expected outcome: the agent asks the
three calibration questions, drives a Phase 1 conversation, transitions
to Phase 2, plays back, and produces a markdown report. Expected
not-yet-tested: streaming protocol compatibility between PydanticAI's
`VercelAIAdapter` and AI SDK v6 `DefaultChatTransport` on the frontend.
That's the highest-risk thing to validate first.

## Run it

```bash
# 1. Backend
cd /Users/nyx/Projects/summerscope/summerchild-llm/agent
cp .env.example .env
# Edit .env: set ANTHROPIC_API_KEY. LOGFIRE_TOKEN optional for first test.
uv sync
uv run uvicorn summerchild_agent.server:app --reload --port 8000

# 2. Frontend (in a second shell)
cd /Users/nyx/Projects/summerscope/summerchild-llm/web
pnpm install   # only needed first time
pnpm dev       # serves on http://localhost:3000

# 3. Open http://localhost:3000 and start typing.
```

The frontend persists a per-browser-session UUID in `localStorage`
under the key `sscs.conversation_id`; the backend uses that to look up
in-memory session state. Restart the backend = state is gone.

## What works (verified)

- 47-question canonical rubric loads cleanly from the repo root's `questions.json` (the legacy `Results` sentinel is filtered).
- Gating evaluation covers both shapes (`depth_in`, `prerequisite`) — tested on `Q-confabulation` (depth gate), `Q-opt_default` (prereq gate).
- `BudgetLedger` enforces the 25% shift-budget cap, rejects additions and de-weights that overrun, and produces a valid `ShiftBudgetAccounting` block at finalisation. The "drop a 9-max to a 4-max costs 5" math from the contract is tested.
- `FinalReport` Pydantic model includes all five session-log sections per the contract; invariants enforced (skipped requires `skip_reason`, de-weighted requires `original_score` + `effective_score` + justification, agent-added question scores must match their tier's palette range).
- FastAPI server boots, `/health` returns OK, `/api/session/{id}/state` returns a live state JSON, `/api/session/{id}/report` returns the markdown report (404 until finalised).
- Frontend builds cleanly (`pnpm build` succeeds, TypeScript clean).
- 27 pytest tests pass; ruff clean.

## What's unverified — please test in this order

1. **End-to-end streaming.** Boot both, send a message. The risk: `VercelAIAdapter.dispatch_request` is configured for `sdk_version=6` but I haven't confirmed the wire bytes match what `DefaultChatTransport` parses. If the frontend hangs or the chat doesn't render, that's the first place to look. CORS is open for `localhost:3000`/`127.0.0.1:3000`.
2. **Phase 1 → Phase 2 transition.** The agent should call `end_phase_1` after enough signal. Watch `/api/session/{id}/state` — `phase` should flip from 1 to 2.
3. **Playback + finalise.** After Phase 2, the agent should call `present_playback` and then `finalise`. The "Download report" link in the chat footer hits `/api/session/{id}/report`.
4. **Budget bite.** Add a context that obviously needs new questions to see whether the agent respects the budget or hits the `BudgetExceeded → ModelRetry` path. If you see the model retrying its own tool calls in traces, that's the budget enforcement working.

## Calls I made (some may be wrong — push back)

- **No AI Elements components.** `npx ai-elements@latest add ...` is blocked by the sandbox safety classifier (external package execution without prior declaration). I built a minimal chat UI directly with `@ai-sdk/react`'s `useChat` + `DefaultChatTransport`. Drop-in swap: `cd web && npx ai-elements@latest add conversation message prompt-input response` (you'll need to approve it interactively), then replace the JSX in `src/components/Chat.tsx` with the `<Conversation>` / `<Message>` / `<PromptInput>` versions. The transport + `useChat` wiring stays the same.
- **PydanticAI version.** The hackathon brief says "Pydantic AI V2 BETA agents", but PydanticAI is on v1 (v2 is reserved future; no beta is published). We installed `pydantic-ai>=1.0.0` (got 1.107.0). The PydanticAI team committed to no breaking changes until v2 ships — treat what's in the brief as "current pydantic-ai 1.x".
- **AI SDK v6.** Picked up `ai@6.x` + `@ai-sdk/react@3.x`. The `useChat` API changed significantly in v6 (no more `input`/`handleInputChange`/`handleSubmit`; transport is now its own object). Configured the backend with `sdk_version=6` on `VercelAIAdapter.dispatch_request`.
- **Anthropic provider dummy key at import time.** The Anthropic provider validates `ANTHROPIC_API_KEY` at construction, which means module import fails without one. I `os.environ.setdefault` a placeholder so the package imports for tests / structural checks; the *real* call fails at runtime if the placeholder is what's actually set. See `agent.py` top — annotated.
- **`AgentAddedQuestion.created_at = datetime.utcnow()`.** `utcnow()` is deprecated in 3.12+; should be `datetime.now(UTC)`. Cosmetic, ignore for now.
- **Markdown report shape is barebones.** `FinalReport.to_markdown()` renders score, dimensions, math, and budget accounting. It does NOT yet render the per-question recommendations (with tradeoffs and legislative references). That's a real fill-in — see "Highest-value next things" below.
- **Persona prompt is locked in code, not Logfire managed variable.** The wiring for `logfire.template_var` is left to do because resolving it needs a Logfire token. The current persona lives at `DEFAULT_SYSTEM_PROMPT` in `src/summerchild_agent/managed_vars.py` — edit + restart works for iteration tonight; managed-variable wiring is morning work.

## What I deliberately left undone

- **`logfire.template_var` for the system prompt and bounds.** Set up requires `LOGFIRE_TOKEN`; deferring until you can provide it. Code structure makes the swap mechanical — `default_system_prompt()` and `default_agent_bounds()` already centralise the fallback, so the wire-up is "fetch managed var, fall back to these on error."
- **Pydantic Evals scaffolding.** Item #12 on the priority list. Out of overnight scope.
- **Prompt-iteration loop.** Item #13. Needs real traces to find regressions in.
- **AI Gateway swap-in.** Stretch #14. The model id is already a single string in `managed_vars.py` so the swap is roughly: `"anthropic:claude-sonnet-4-6"` → `"gateway/anthropic:claude-sonnet-4-6"`.
- **Logfire dashboard + alerts.** Stretch #16. Configure in Logfire UI once it's receiving traces.
- **JS/TS Logfire SDK on the frontend.** Stretch #15. Add `@logfire/browser` (or whatever the JS package is called — confirm) to `web/`.
- **Tests for the agent itself.** Covered the deterministic layers (rubric, budget, models). The agent-level tests need PydanticAI's `TestModel` / `FunctionModel` patterns to exercise tool gating without a real API key. ~30 min of work; useful but I chose to spend the time on the FastAPI + frontend layer first.

## Highest-value next things in priority order

1. **Boot both servers + send "hello" — verify the streaming wire works.** Highest-risk unknown.
2. **Render the full report with recommendations.** `FinalReport.to_markdown()` currently shows the score math and budget accounting but doesn't pull each answered question's `recommendation.tradeoff/considerations/references` into the report. That's a 30-line rewrite in `models.py` and is the deliverable the user actually walks away with.
3. **Wire managed variables.** Once `LOGFIRE_TOKEN` is set, replace `default_system_prompt()` and `default_agent_bounds()` with `logfire.template_var(...)` calls so you can tune the persona from the Logfire UI mid-demo.
4. **Two Pydantic Evals.** Completion rate (does the agent finalise across N personas?) and score stability (same persona → variance). These hit two big scoring categories (Pydantic Evals 30 pts, Prompt optimization 25 pts).
5. **Persona iteration on real traces.** The system prompt at `managed_vars.py:18` is a first draft. Run a few conversations, see where the voice slips, tighten.

## Project layout (everything lives inside `summerchild-llm/`)

```
summerchild-llm/
├── AGENT_CONTRACT.md          # the spec
├── questions.json             # the canonical rubric
├── README.md
├── LICENSE
├── static-walker/
│   └── summerchild.py         # deterministic reference walker
├── agent/                     # ← this Python project
│   ├── pyproject.toml
│   ├── uv.lock
│   ├── .env.example           # ANTHROPIC_API_KEY, LOGFIRE_TOKEN, overrides
│   ├── .gitignore             # scoped to agent/ + caches
│   ├── README.md
│   ├── OVERNIGHT_STATUS.md    # this file
│   ├── src/
│   │   └── summerchild_agent/
│   │       ├── __init__.py
│   │       ├── models.py          # Pydantic models matching the contract
│   │       ├── rubric.py          # Loads + indexes questions.json, gating eval
│   │       ├── budget.py          # Shift-budget ledger
│   │       ├── state.py           # SessionState + SessionDeps + log builder
│   │       ├── managed_vars.py    # System prompt + bounds (with defaults)
│   │       ├── agent.py           # PydanticAI Agent + Phase 1/2 tools
│   │       ├── logfire_config.py  # Logfire setup (no-op without LOGFIRE_TOKEN)
│   │       └── server.py          # FastAPI app + streaming /api/chat + REST
│   └── tests/
│       ├── test_rubric.py
│       ├── test_budget.py
│       └── test_models.py
└── web/                       # Next.js + React frontend
    ├── package.json
    ├── src/
    │   ├── app/
    │   │   ├── layout.tsx
    │   │   └── page.tsx       # main chat page
    │   ├── components/
    │   │   ├── Chat.tsx       # useChat + DefaultChatTransport
    │   │   ├── ScoreSidebar.tsx
    │   │   └── TransparencyBlurb.tsx
    │   └── lib/
    │       ├── api.ts
    │       └── use-conversation-id.ts
    └── …
```

## Two more things

- The rubric and the contract live alongside this project at the repo root — `agent/` reads them via `Path(__file__).resolve().parents[3]` in `rubric.py`. The contract amendment we discussed (adding `source_question_ids` to playback bullets) is reflected in the agent's `PlaybackBullet` model and enforced by the `present_playback` tool — if you want it codified in `AGENT_CONTRACT.md` itself, that's a small inline edit.
- The chat sidebar polls `/api/session/{id}/state` every 2 seconds. Cheap, but if it's annoying in dev open `web/src/components/ScoreSidebar.tsx` and bump the interval. Sidebar is the agent's behaviour made observable — it'll be the most useful thing to point at during demo.

Sleep well 🌙

# summerchild-agent

Conversational AI agent that drives the Sweet Summer Child Score
risk-assessment quiz, consuming the canonical rubric and contract from
the sibling repo at `summerscope/summerchild-llm`.

This is the hackathon prototype. See `OVERNIGHT_STATUS.md` for what works
right now and what's outstanding.

## Stack

- **Agent**: PydanticAI 1.x against Anthropic Claude Sonnet 4.6 (model id
  resolved from a Logfire managed variable, with env-var fallback)
- **Backend**: FastAPI with streaming via PydanticAI's `VercelAIAdapter`
- **Observability**: Logfire instrumentation, built-in PII scrubbing,
  managed variables for the system prompt and agent-agency bounds
- **Frontend**: Next.js + Vercel AI Elements (`web/`)
- **Canonical rubric**: read-only consumer of
  `../summerchild-llm/questions.json` and `AGENT_CONTRACT.md` v1.1

## Run locally

```bash
cp .env.example .env
# fill in ANTHROPIC_API_KEY and (optionally) LOGFIRE_TOKEN

uv sync
uv run uvicorn summerchild_agent.server:app --reload

# in another shell
cd web && pnpm install && pnpm dev
```

Backend on `:8000`, frontend on `:3000`. The frontend posts to
`http://localhost:8000/api/chat`.

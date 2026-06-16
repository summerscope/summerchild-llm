"use client";

import { useEffect, useState } from "react";
import { stateEndpoint } from "@/lib/api";

interface SessionState {
  conversation_id: string;
  phase: number;
  depth: string | null;
  cohort_multiplier: number | null;
  asked_canonical_count: number;
  added_questions_count: number;
  skipped_count: number;
  inferred_count: number;
  de_weightings_count: number;
  pending_question_id: string | null;
  playback_presented: boolean;
  final_report_ready: boolean;
  budget: {
    fraction: number;
    canonical_max_session: number;
    shift_budget: number;
    spent_additions: number;
    spent_de_weighting: number;
    spent_total: number;
    remaining: number;
  };
}

/**
 * Live view of session state, polled every 2 seconds.
 *
 * Shows the agent's running belief: phase, depth, cohort multiplier,
 * questions asked / added / skipped, and shift-budget consumption. The
 * sidebar's job is to make the agent's behaviour observable to a sceptical
 * user without having to read the chat back.
 */
export function ScoreSidebar({ conversationId }: { conversationId: string }) {
  const [state, setState] = useState<SessionState | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const res = await fetch(stateEndpoint(conversationId));
        if (!res.ok) return;
        const data = (await res.json()) as SessionState;
        if (!cancelled) setState(data);
      } catch {
        // backend may be down briefly; ignore.
      }
    }
    tick();
    const handle = setInterval(tick, 2000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [conversationId]);

  if (!state) {
    return (
      <div className="text-sm text-zinc-700 dark:text-zinc-300">
        Waiting for the conversation to begin…
      </div>
    );
  }

  const totalAsked = state.asked_canonical_count + state.added_questions_count;
  const budgetPct =
    state.budget.shift_budget > 0
      ? (state.budget.spent_total / state.budget.shift_budget) * 100
      : 0;

  return (
    <div className="space-y-6 text-sm text-zinc-900 dark:text-zinc-50">
      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-200">
          Phase
        </h2>
        <p className="font-medium text-zinc-900 dark:text-zinc-100">
          {state.phase === 1 ? "Conversation" : "Re-weight + playback"}
          {state.final_report_ready && " (finalised)"}
        </p>
      </section>

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-200">
          Calibration
        </h2>
        <dl className="space-y-1">
          <div className="flex justify-between">
            <dt>LLM depth</dt>
            <dd className="font-mono">{state.depth ?? "—"}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Cohort multiplier</dt>
            <dd className="font-mono">
              {state.cohort_multiplier?.toFixed(2) ?? "—"}
            </dd>
          </div>
        </dl>
      </section>

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-200">
          Questions
        </h2>
        <dl className="space-y-1">
          <div className="flex justify-between">
            <dt>Asked (canonical)</dt>
            <dd className="font-mono">{state.asked_canonical_count}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Asked (agent-added)</dt>
            <dd className="font-mono">{state.added_questions_count}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Inferred (from braindump)</dt>
            <dd className="font-mono">{state.inferred_count}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Skipped</dt>
            <dd className="font-mono">{state.skipped_count}</dd>
          </div>
          <div className="flex justify-between font-medium text-zinc-900 dark:text-zinc-100">
            <dt>Total asked</dt>
            <dd className="font-mono">{totalAsked}</dd>
          </div>
        </dl>
      </section>

      <section>
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-200">
          Shift budget
        </h2>
        <div className="space-y-2">
          <div className="h-2 w-full rounded-full bg-zinc-300 dark:bg-zinc-700">
            <div
              className={`h-2 rounded-full ${
                budgetPct > 90
                  ? "bg-rose-500"
                  : budgetPct > 60
                    ? "bg-amber-500"
                    : "bg-emerald-500"
              }`}
              style={{ width: `${Math.min(100, budgetPct)}%` }}
            />
          </div>
          <dl className="space-y-1 text-xs">
            <div className="flex justify-between">
              <dt>Spent / budget</dt>
              <dd className="font-mono">
                {state.budget.spent_total} /{" "}
                {state.budget.shift_budget.toFixed(1)}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt>· additions</dt>
              <dd className="font-mono">{state.budget.spent_additions}</dd>
            </div>
            <div className="flex justify-between">
              <dt>· de-weightings</dt>
              <dd className="font-mono">{state.budget.spent_de_weighting}</dd>
            </div>
            <div className="flex justify-between">
              <dt>Canonical max</dt>
              <dd className="font-mono">{state.budget.canonical_max_session}</dd>
            </div>
          </dl>
        </div>
      </section>

      {state.pending_question_id && (
        <section>
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-700 dark:text-zinc-200">
            Pending
          </h2>
          <p className="font-mono text-xs">{state.pending_question_id}</p>
        </section>
      )}
    </div>
  );
}

"use client";

import { useState } from "react";

/**
 * The 10-second transparency moment: where does your data go.
 * Dismissible per-session (state lives in component, not persisted).
 */
export function TransparencyBlurb() {
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;
  return (
    <div className="border-b border-amber-400 bg-amber-50 px-6 py-3 text-sm text-amber-950 dark:border-amber-600 dark:bg-amber-950 dark:text-amber-50">
      <div className="flex items-start justify-between gap-4">
        <p className="leading-relaxed">
          This conversation is ephemeral — nothing is stored on our side after
          you close the tab. Anonymised traces are sent to Logfire (with PII
          scrubbed) so we can improve the system. Your messages also pass
          through Anthropic, subject to their terms. You can leave anytime;
          nothing is tied to your identity.
        </p>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="shrink-0 rounded-md border border-amber-500 bg-amber-100 px-3 py-1 text-xs font-medium text-amber-950 hover:bg-amber-200 dark:border-amber-500 dark:bg-amber-900 dark:text-amber-50 dark:hover:bg-amber-800"
        >
          Got it
        </button>
      </div>
    </div>
  );
}

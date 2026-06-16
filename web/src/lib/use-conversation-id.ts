"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "sscs.conversation_id";

/**
 * Mints a UUID per browser-session and persists it in localStorage. Survives
 * accidental refresh; cleared by browser-side mechanisms only. Matches the
 * ephemeral privacy posture: the server keeps state keyed by this id but
 * forgets it on restart, and nothing on the server ties it to identity.
 */
export function useConversationId(): string | null {
  const [id, setId] = useState<string | null>(null);

  useEffect(() => {
    let existing = window.localStorage.getItem(STORAGE_KEY);
    if (!existing) {
      existing = crypto.randomUUID();
      window.localStorage.setItem(STORAGE_KEY, existing);
    }
    setId(existing);
  }, []);

  return id;
}

export function resetConversationId(): string {
  const fresh = crypto.randomUUID();
  window.localStorage.setItem(STORAGE_KEY, fresh);
  window.location.reload();
  return fresh;
}

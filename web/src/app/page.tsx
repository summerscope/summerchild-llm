"use client";

import { useEffect, useState } from "react";
import { Chat } from "@/components/Chat";
import { ScoreSidebar } from "@/components/ScoreSidebar";
import { TransparencyBlurb } from "@/components/TransparencyBlurb";
import { useConversationId } from "@/lib/use-conversation-id";

export default function Home() {
  const conversationId = useConversationId();

  // Arm a beforeunload guard once meaningful state exists.
  const [hasState, setHasState] = useState(false);
  useEffect(() => {
    if (!hasState) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [hasState]);

  if (!conversationId) {
    return (
      <main className="flex h-dvh items-center justify-center bg-zinc-50 dark:bg-zinc-950">
        <p className="text-zinc-700 dark:text-zinc-300">Loading…</p>
      </main>
    );
  }

  return (
    <main className="grid h-dvh grid-cols-1 bg-zinc-50 dark:bg-zinc-950 md:grid-cols-[1fr_320px]">
      <div className="flex h-dvh flex-col overflow-hidden">
        <header className="border-b border-zinc-300 px-6 py-4 dark:border-zinc-700">
          <h1 className="text-lg font-semibold tracking-tight text-zinc-900 dark:text-zinc-50">
            Sweet Summer Child Score
          </h1>
          <p className="text-sm text-zinc-700 dark:text-zinc-300">
            Conversational risk assessment for automated decision systems
          </p>
        </header>
        <TransparencyBlurb />
        <Chat conversationId={conversationId} onStateChange={setHasState} />
      </div>
      <aside className="hidden border-l border-zinc-300 bg-white p-6 dark:border-zinc-700 dark:bg-zinc-900 md:block">
        <ScoreSidebar conversationId={conversationId} />
      </aside>
    </main>
  );
}

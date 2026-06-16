"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useEffect, useMemo, useRef, useState } from "react";
import { CHAT_ENDPOINT, reportEndpoint } from "@/lib/api";

interface ChatProps {
  conversationId: string;
  onStateChange?: (hasState: boolean) => void;
}

/**
 * Streaming chat against the FastAPI backend, using AI SDK v6's
 * `useChat` + `DefaultChatTransport`.
 *
 * AI Elements wraps the same primitive — we drop down a level here because
 * the `ai-elements` CLI install is blocked by the sandbox policy. Swap-in
 * is straightforward — see OVERNIGHT_STATUS.md.
 */
export function Chat({ conversationId, onStateChange }: ChatProps) {
  // Memoise the transport so it isn't rebuilt on every render.
  const transport = useMemo(
    () =>
      new DefaultChatTransport({
        api: CHAT_ENDPOINT,
        headers: { "X-Conversation-Id": conversationId },
      }),
    [conversationId],
  );

  const { messages, sendMessage, status, error } = useChat({
    id: conversationId,
    transport,
  });

  const [input, setInput] = useState("");
  const isLoading = status === "submitted" || status === "streaming";

  // Notify parent once meaningful state exists (for the close-confirm guard).
  useEffect(() => {
    onStateChange?.(messages.length > 1);
  }, [messages.length, onStateChange]);

  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;
    sendMessage({ text: trimmed });
    setInput("");
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-6 [scroll-behavior:smooth]"
      >
        {messages.length === 0 && (
          <div className="mx-auto max-w-xl rounded-lg border border-dashed border-zinc-300 bg-white p-6 text-sm text-zinc-600 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300">
            <p className="mb-2 font-medium text-zinc-900 dark:text-zinc-100">
              Tell me about the system you&apos;re assessing.
            </p>
            <p>
              Drop a paragraph — what it does, who uses it, where the LLM (if
              any) sits. I&apos;ll work the rest out from there.
            </p>
          </div>
        )}
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          {messages.map((m) => (
            <MessageBubble key={m.id} role={m.role}>
              {renderMessageText(m)}
            </MessageBubble>
          ))}
          {isLoading && (
            <div className="flex items-center gap-2 text-sm text-zinc-500">
              <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-500" />
              <span>thinking…</span>
            </div>
          )}
          {error && (
            <div className="rounded-md border border-rose-300 bg-rose-50 p-3 text-sm text-rose-900 dark:border-rose-700 dark:bg-rose-950 dark:text-rose-100">
              {error.message}
            </div>
          )}
        </div>
      </div>
      <form
        onSubmit={handleSubmit}
        className="border-t border-zinc-200 bg-white p-4 dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div className="mx-auto flex max-w-2xl gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={isLoading}
            placeholder="Tell me about your system…"
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 placeholder-zinc-400 focus:border-zinc-500 focus:outline-none disabled:bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100 dark:placeholder-zinc-500 dark:disabled:bg-zinc-900"
          />
          <button
            type="submit"
            disabled={isLoading || input.trim() === ""}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:bg-zinc-300 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-700 dark:disabled:text-zinc-400"
          >
            Send
          </button>
        </div>
        <DownloadReportLink conversationId={conversationId} />
      </form>
    </div>
  );
}

function MessageBubble({
  role,
  children,
}: {
  role: string;
  children: React.ReactNode;
}) {
  const isUser = role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-lg px-4 py-3 text-sm ${
          isUser
            ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
            : "bg-white text-zinc-900 ring-1 ring-zinc-200 dark:bg-zinc-900 dark:text-zinc-100 dark:ring-zinc-800"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

// AI SDK v6 messages carry `parts: UIMessagePart[]`. Render the text parts
// concatenated; ignore tool calls / reasoning here — those surface in the
// sidebar instead.
function renderMessageText(m: UIMessage): string {
  return m.parts
    .filter((p): p is { type: "text"; text: string; state?: "streaming" | "done" } =>
      p.type === "text" && typeof (p as { text?: unknown }).text === "string",
    )
    .map((p) => p.text)
    .join("");
}

function DownloadReportLink({ conversationId }: { conversationId: string }) {
  return (
    <div className="mx-auto mt-2 flex max-w-2xl items-center justify-between text-xs text-zinc-500">
      <span>Session: {conversationId.slice(0, 8)}</span>
      <a
        href={reportEndpoint(conversationId)}
        className="underline-offset-2 hover:text-zinc-900 hover:underline dark:hover:text-zinc-100"
      >
        Download report (only available once finalised)
      </a>
    </div>
  );
}

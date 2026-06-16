"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useEffect, useMemo, useRef, useState } from "react";
import { CHAT_ENDPOINT, reportEndpoint, stateEndpoint } from "@/lib/api";

interface ChatProps {
  conversationId: string;
  onStateChange?: (hasState: boolean) => void;
}

/**
 * Streaming chat against the FastAPI backend, using AI SDK v6's
 * `useChat` + `DefaultChatTransport`.
 *
 * Input is an auto-growing textarea — supports longform braindumps without
 * scrolling. Enter submits; Shift-Enter inserts a newline (convention from
 * Slack / ChatGPT / Linear).
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
  const [reportReady, setReportReady] = useState(false);
  const isLoading = status === "submitted" || status === "streaming";

  // Poll session state to know when the report is ready. This is intentionally
  // separate from the sidebar's polling — the download link wants minimal
  // dependency on the sidebar component existing.
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const res = await fetch(stateEndpoint(conversationId));
        if (!res.ok) return;
        const data = (await res.json()) as { final_report_ready?: boolean };
        if (!cancelled) setReportReady(Boolean(data.final_report_ready));
      } catch {
        // backend may be briefly down; ignore.
      }
    }
    tick();
    const handle = setInterval(tick, 3000);
    return () => {
      cancelled = true;
      clearInterval(handle);
    };
  }, [conversationId]);

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

  // Auto-grow textarea: track scrollHeight on each change, no inner scrollbar.
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  function submit() {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;
    sendMessage({ text: trimmed });
    setInput("");
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    submit();
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Enter submits; Shift-Enter inserts a newline.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-6">
        {messages.length === 0 && (
          <div className="mx-auto max-w-2xl rounded-lg border border-zinc-300 bg-white p-6 text-zinc-800 dark:border-zinc-600 dark:bg-zinc-900 dark:text-zinc-100">
            <p className="mb-2 text-base font-medium text-zinc-900 dark:text-zinc-50">
              Tell me about the system you&apos;re assessing.
            </p>
            <p className="text-sm leading-relaxed text-zinc-700 dark:text-zinc-300">
              Drop a paragraph (or several) — what it does, who uses it, where
              the LLM (if any) sits, what could go wrong. I&apos;ll work the
              rest out from there.
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
            <div className="flex items-center gap-2 text-sm text-zinc-700 dark:text-zinc-300">
              <div className="h-1.5 w-1.5 animate-pulse rounded-full bg-zinc-700 dark:bg-zinc-300" />
              <span>thinking…</span>
            </div>
          )}
          {error && (
            <div className="rounded-md border border-rose-400 bg-rose-50 p-3 text-sm text-rose-900 dark:border-rose-500 dark:bg-rose-950 dark:text-rose-100">
              {error.message}
            </div>
          )}
        </div>
      </div>
      <form
        onSubmit={handleSubmit}
        className="border-t border-zinc-300 bg-white p-4 dark:border-zinc-700 dark:bg-zinc-900"
      >
        <div className="mx-auto flex max-w-2xl flex-col gap-2">
          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isLoading}
              placeholder="Tell me about your system… (Enter to send, Shift-Enter for new line)"
              rows={4}
              className="flex-1 resize-none overflow-hidden rounded-md border border-zinc-400 bg-white px-3 py-2 text-base leading-relaxed text-zinc-900 placeholder-zinc-600 focus:border-zinc-700 focus:outline-none disabled:bg-zinc-100 disabled:text-zinc-500 dark:border-zinc-500 dark:bg-zinc-950 dark:text-zinc-50 dark:placeholder-zinc-300 dark:focus:border-zinc-300 dark:disabled:bg-zinc-900"
              style={{ minHeight: "6rem", maxHeight: "60vh" }}
            />
            <button
              type="submit"
              disabled={isLoading || input.trim() === ""}
              className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:bg-zinc-300 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-700 dark:disabled:text-zinc-400"
            >
              Send
            </button>
          </div>
          <SessionFooter
            conversationId={conversationId}
            reportReady={reportReady}
          />
        </div>
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
        className={`max-w-[85%] whitespace-pre-wrap rounded-lg px-4 py-3 text-base leading-relaxed ${
          isUser
            ? "bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
            : "bg-white text-zinc-900 ring-1 ring-zinc-300 dark:bg-zinc-900 dark:text-zinc-100 dark:ring-zinc-700"
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

function SessionFooter({
  conversationId,
  reportReady,
}: {
  conversationId: string;
  reportReady: boolean;
}) {
  return (
    <div className="flex items-center justify-between text-xs text-zinc-700 dark:text-zinc-200">
      <span>Session: {conversationId.slice(0, 8)}</span>
      {reportReady ? (
        <a
          href={reportEndpoint(conversationId)}
          className="font-medium text-emerald-700 underline underline-offset-2 hover:text-emerald-900 dark:text-emerald-300 dark:hover:text-emerald-100"
        >
          Download your report
        </a>
      ) : null}
    </div>
  );
}

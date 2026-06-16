"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type UIMessage } from "ai";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  CHAT_ENDPOINT,
  pendingEndpoint,
  reportEndpoint,
  stateEndpoint,
} from "@/lib/api";

interface ChatProps {
  conversationId: string;
  onStateChange?: (hasState: boolean) => void;
}

interface PendingQuestion {
  question_id: string;
  source: "canonical" | "agent-added";
  text: string;
  preferred_modality: "buttons" | "open";
  answers: { key: string; text: string }[];
}

/**
 * Streaming chat against the FastAPI backend, using AI SDK v6's
 * `useChat` + `DefaultChatTransport`.
 *
 * Input is an auto-growing textarea — supports longform braindumps without
 * scrolling. Enter submits; Shift-Enter inserts a newline. Assistant
 * messages render through `react-markdown` so `**bold**`, lists, and code
 * fences show as formatted output.
 *
 * When a button-modality question is pending, polled from
 * `/api/session/{id}/pending`, the choices render as clickable buttons —
 * clicking sends the choice as a user message.
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
  const [pending, setPending] = useState<PendingQuestion | null>(null);
  const isLoading = status === "submitted" || status === "streaming";

  // Poll session state + pending question. Both are cheap; one combined
  // tick keeps the request count down. Pending question only refreshes
  // while we're NOT mid-stream so we don't show buttons for a turn-stale
  // question.
  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const [stateRes, pendingRes] = await Promise.all([
          fetch(stateEndpoint(conversationId)),
          fetch(pendingEndpoint(conversationId)),
        ]);
        if (stateRes.ok) {
          const data = (await stateRes.json()) as {
            final_report_ready?: boolean;
          };
          if (!cancelled) setReportReady(Boolean(data.final_report_ready));
        }
        if (pendingRes.ok) {
          const data = (await pendingRes.json()) as {
            pending: PendingQuestion | null;
          };
          if (!cancelled) setPending(data.pending);
        }
      } catch {
        // backend may be briefly down; ignore.
      }
    }
    tick();
    const handle = setInterval(tick, 1500);
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
  }, [messages, pending]);

  // Auto-grow textarea: track scrollHeight on each change, no inner scrollbar.
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  function submit(text?: string) {
    const finalText = (text ?? input).trim();
    if (!finalText || isLoading) return;
    sendMessage({ text: finalText });
    if (text === undefined) setInput("");
    // Optimistically clear pending so the buttons disappear immediately —
    // server will confirm on next poll.
    setPending(null);
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

  const showButtons = Boolean(
    pending && pending.preferred_modality === "buttons" && !isLoading,
  );

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
              <MessageBody role={m.role} text={renderMessageText(m)} />
            </MessageBubble>
          ))}
          {showButtons && pending ? (
            <AnswerButtons
              pending={pending}
              disabled={isLoading}
              onPick={(key, text) => submit(`${key}) ${text}`)}
              onNotSure={() => submit("I'm not sure")}
            />
          ) : null}
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
              placeholder={
                messages.length === 0
                  ? "Tell me about your system… (Enter to send, Shift-Enter for new line)"
                  : pending
                    ? `Or write your own answer to: "${truncate(pending.text, 100)}"`
                    : "Your answer… (Enter to send, Shift-Enter for new line)"
              }
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
        className={`max-w-[85%] rounded-lg px-4 py-3 text-base leading-relaxed ${
          isUser
            ? "whitespace-pre-wrap bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900"
            : "bg-white text-zinc-900 ring-1 ring-zinc-300 dark:bg-zinc-900 dark:text-zinc-100 dark:ring-zinc-700"
        }`}
      >
        {children}
      </div>
    </div>
  );
}

/**
 * Renders assistant messages through react-markdown (so `**bold**`, lists,
 * `code`, links, tables all show formatted). User messages stay plain text
 * with whitespace preserved — they typed it, we don't second-guess.
 */
function MessageBody({ role, text }: { role: string; text: string }) {
  if (role === "user") return <>{text}</>;
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ node, ...props }) => (
            <p className="mb-3 last:mb-0" {...props} />
          ),
          ul: ({ node, ...props }) => (
            <ul className="mb-3 list-disc pl-5 last:mb-0" {...props} />
          ),
          ol: ({ node, ...props }) => (
            <ol className="mb-3 list-decimal pl-5 last:mb-0" {...props} />
          ),
          li: ({ node, ...props }) => <li className="mb-1" {...props} />,
          strong: ({ node, ...props }) => (
            <strong className="font-semibold" {...props} />
          ),
          em: ({ node, ...props }) => <em className="italic" {...props} />,
          code: ({ node, ...props }) => (
            <code
              className="rounded bg-zinc-100 px-1 py-0.5 font-mono text-sm dark:bg-zinc-800"
              {...props}
            />
          ),
          a: ({ node, ...props }) => (
            <a
              className="text-emerald-700 underline underline-offset-2 hover:text-emerald-900 dark:text-emerald-300 dark:hover:text-emerald-100"
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            />
          ),
          h1: ({ node, ...props }) => (
            <h1 className="mb-2 mt-2 text-xl font-semibold" {...props} />
          ),
          h2: ({ node, ...props }) => (
            <h2 className="mb-2 mt-2 text-lg font-semibold" {...props} />
          ),
          h3: ({ node, ...props }) => (
            <h3 className="mb-2 mt-2 text-base font-semibold" {...props} />
          ),
          blockquote: ({ node, ...props }) => (
            <blockquote
              className="mb-3 border-l-4 border-zinc-400 pl-3 italic text-zinc-700 dark:border-zinc-500 dark:text-zinc-300"
              {...props}
            />
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/**
 * Inline answer choices for button-modality questions. Each renders as a
 * full-width pill so longer answer text wraps cleanly. Click submits the
 * choice as a user message; the textarea remains available for typed
 * answers if the user wants to go off-script.
 */
function AnswerButtons({
  pending,
  disabled,
  onPick,
  onNotSure,
}: {
  pending: PendingQuestion;
  disabled: boolean;
  onPick: (key: string, text: string) => void;
  onNotSure: () => void;
}) {
  // One button per row — keeps long answer text readable. The previous
  // wrap-grid version got cramped when answers were full sentences.
  return (
    <div className="flex flex-col gap-2">
      {pending.answers.map((a) => (
        <button
          key={a.key}
          type="button"
          disabled={disabled}
          onClick={() => onPick(a.key, a.text)}
          className="flex w-full items-start gap-3 rounded-md border border-zinc-400 bg-white px-3 py-2 text-left text-sm leading-relaxed text-zinc-900 hover:border-zinc-700 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-500 dark:bg-zinc-900 dark:text-zinc-50 dark:hover:border-zinc-300 dark:hover:bg-zinc-800"
        >
          <span className="shrink-0 font-mono font-semibold text-zinc-700 dark:text-zinc-200">
            {a.key})
          </span>
          <span>{a.text}</span>
        </button>
      ))}
      {/* Nope-out option — discourages donkey-voting on questions the user
          can't honestly answer. The agent's prompt knows to either ask a
          clarifying question or skip the canonical when it sees this. */}
      <button
        type="button"
        disabled={disabled}
        onClick={onNotSure}
        className="self-start rounded-md border border-dashed border-zinc-400 bg-transparent px-3 py-1.5 text-xs text-zinc-700 hover:border-zinc-700 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-600 dark:text-zinc-200 dark:hover:border-zinc-300 dark:hover:text-zinc-50"
      >
        I&apos;m not sure
      </button>
    </div>
  );
}

/**
 * Trim a string to roughly `max` chars at a word boundary, appending "…"
 * when truncation actually happens. First-line preference: if the input
 * has a newline before `max`, snap to it (most canonical questions have a
 * short first sentence then a longer explainer — first line is the right
 * hint for a placeholder).
 */
function truncate(text: string, max: number): string {
  const firstLine = text.split("\n")[0] ?? text;
  const candidate = firstLine.length <= max ? firstLine : firstLine.slice(0, max);
  if (candidate.length === text.length) return text;
  const lastSpace = candidate.lastIndexOf(" ");
  const cut = lastSpace > max * 0.6 ? candidate.slice(0, lastSpace) : candidate;
  return `${cut.trimEnd()}…`;
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

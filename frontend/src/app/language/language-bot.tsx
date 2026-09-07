"use client";

import { Bot, Loader2, Send, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState, useTransition } from "react";
import { botAction } from "@/app/language/actions";
import type {
  BotMessage,
  BotResponseLanguage,
  DhivehiInference,
} from "@/lib/types";

function boundedHistory(messages: BotMessage[], prompt: string): BotMessage[] {
  const history = [...messages, { role: "user" as const, content: prompt }].slice(-20);
  while (history.length > 1 && history.reduce((sum, item) => sum + item.content.length, 0) > 20_000) {
    history.shift();
  }
  return history;
}

export function LanguageBot() {
  const router = useRouter();
  const [messages, setMessages] = useState<BotMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [responseLanguage, setResponseLanguage] = useState<BotResponseLanguage>("auto");
  const [lastInference, setLastInference] = useState<DhivehiInference | null>(null);
  const [busy, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = prompt.trim();
    if (content === "" || busy) return;
    const next = boundedHistory(messages, content);
    setMessages(next);
    setPrompt("");
    setError(null);
    startTransition(async () => {
      const result = await botAction(next, responseLanguage);
      if (!result.ok) {
        setError(result.message);
        if (result.signedOut) router.replace("/sign-in");
        return;
      }
      setLastInference(result.inference);
      setMessages([...next, { role: "assistant", content: result.inference.text }]);
    });
  }

  return (
    <section className="panel overflow-hidden" aria-labelledby="dhivehi-bot-heading">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span className="rounded-md bg-accent/10 p-2 text-accent">
            <Bot className="size-4" aria-hidden="true" />
          </span>
          <div>
            <h2 id="dhivehi-bot-heading" className="text-sm font-semibold text-ink">
              Dhivehi intelligence assistant
            </h2>
            <p className="mt-0.5 text-xs text-ink-faint">
              Private Qwen · Thaana, Romanized Dhivehi, English, and mixed text
            </p>
          </div>
        </div>
        <button
          type="button"
          onClick={() => {
            setMessages([]);
            setLastInference(null);
            setError(null);
          }}
          disabled={messages.length === 0 || busy}
          className="inline-flex items-center gap-1.5 rounded-md border border-line px-2.5 py-1.5 text-xs text-ink-muted disabled:opacity-40"
        >
          <Trash2 className="size-3.5" aria-hidden="true" />
          Clear
        </button>
      </div>

      <div className="min-h-64 max-h-[34rem] space-y-3 overflow-y-auto bg-surface-sunken/40 p-4">
        {messages.length === 0 ? (
          <div className="mx-auto max-w-xl py-12 text-center">
            <p className="text-sm font-medium text-ink">Ask a question or analyze text</p>
            <p className="mt-1 text-xs leading-5 text-ink-faint">
              The bot runs locally on cyber-ai. It does not have access to intelligence records
              unless you include relevant text in the conversation.
            </p>
          </div>
        ) : (
          messages.map((message, index) => (
            <div
              key={`${message.role}-${index}`}
              className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[88%] whitespace-pre-wrap rounded-lg px-3.5 py-2.5 text-sm leading-6 ${
                  message.role === "user"
                    ? "bg-accent text-bg"
                    : "border border-line bg-surface text-ink"
                }`}
                dir="auto"
              >
                {message.content}
              </div>
            </div>
          ))
        )}
        {busy ? (
          <div className="flex items-center gap-2 text-xs text-ink-faint">
            <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
            Qwen is preparing a response…
          </div>
        ) : null}
      </div>

      <form onSubmit={submit} className="border-t border-line p-4">
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value.slice(0, 4_000))}
          placeholder="ސުވާލެއް ލިޔުއް، or ask in English…"
          className="min-h-24 w-full resize-y rounded-md border border-line bg-surface-sunken px-3 py-2.5 text-sm leading-6 text-ink outline-none focus:border-accent"
          dir="auto"
          spellCheck={false}
        />
        <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
          <label className="flex items-center gap-2 text-xs text-ink-muted">
            Reply in
            <select
              value={responseLanguage}
              onChange={(event) => setResponseLanguage(event.target.value as BotResponseLanguage)}
              disabled={busy}
              className="rounded-md border border-line bg-surface px-2 py-1.5 text-xs text-ink"
            >
              <option value="auto">Same language</option>
              <option value="dhivehi">Dhivehi / Thaana</option>
              <option value="english">English</option>
            </select>
          </label>
          <button
            type="submit"
            disabled={busy || prompt.trim() === ""}
            className="inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2 text-sm font-semibold text-bg disabled:opacity-50"
          >
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
            Send
          </button>
        </div>
        {error ? (
          <p className="mt-3 rounded-md border border-reject/30 bg-reject/10 px-3 py-2 text-sm text-reject">
            {error}
          </p>
        ) : null}
        {lastInference ? (
          <p className="mt-2 text-[11px] text-ink-faint">
            {lastInference.model} · {lastInference.model_revision.slice(0, 12)} · Verify
            consequential claims.
          </p>
        ) : null}
      </form>
    </section>
  );
}

import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  ApiError,
  CHAT_INPUT_MAX_CHARS,
  chat,
  type ChatMessage,
  type ChatResponse,
  type ContactCardData,
  type ContactType,
  type ExportFilter,
  type PrimaryFund,
  type SearchContactsResult,
  type ToolCallTrace,
} from "../api/client";
import { useTTSPlayback } from "../hooks/useTTSPlayback";
import { ContactCardList } from "./ContactCard";
import { DictationButton } from "./DictationButton";
import { MessageErrorBoundary } from "./MessageErrorBoundary";

/**
 * Chat interface — message list + input box + char counter.
 *
 * Voice-friendly rendering: react-markdown is locked to a small allow-list
 * (paragraphs, bold/italic, lists). No headings, code blocks, or tables —
 * those don't speak well in Phase 3 voice mode and aren't useful here.
 *
 * History is held in component state and sent on every request, capped
 * client-side at the same turn limit the backend uses.
 */

const ALLOWED_MD_ELEMENTS = ["p", "strong", "em", "ul", "ol", "li"];
const HISTORY_MAX_TURNS = 20;

interface ContactGroup {
  contacts: ContactCardData[];
  truncated: boolean;
  limit: number;
  /** True if the search filtered by ex_government — surfaces the pill. */
  showExGov: boolean;
  /** Filter the export endpoints accept — same subset of search params.
   *  Lets the Export button reproduce this list as a Sheet/CSV. */
  exportFilter: ExportFilter;
}

const _PRIMARY_FUNDS: ReadonlyArray<PrimaryFund> = [
  "Critical Minerals",
  "Maritime",
  "Energy",
  "General",
];
const _CONTACT_TYPES: ReadonlyArray<ContactType> = [
  "LP",
  "Portfolio",
  "Government",
  "Intermediary",
  "Advisor",
  "Inspiration",
  "Other",
];

function _coerceExportFilter(params: Record<string, unknown>): ExportFilter {
  const out: ExportFilter = {};
  if (typeof params.query === "string" && params.query.length > 0) {
    out.query = params.query;
  }
  if (
    typeof params.primary_fund === "string" &&
    (_PRIMARY_FUNDS as readonly string[]).includes(params.primary_fund)
  ) {
    out.primary_fund = params.primary_fund as PrimaryFund;
  }
  if (
    typeof params.contact_type === "string" &&
    (_CONTACT_TYPES as readonly string[]).includes(params.contact_type)
  ) {
    out.contact_type = params.contact_type as ContactType;
  }
  return out;
}

type Bubble =
  | { kind: "user"; content: string }
  | {
      kind: "assistant";
      content: string;
      contactGroups: ContactGroup[];
      // True iff the triggering user message was dictation-originated
      // (mode==="voice"). Frozen at bubble-creation time so that
      // already-played replies never auto-play again on re-render.
      shouldAutoPlayTTS: boolean;
    }
  | { kind: "error"; content: string };

/** Extract every `update_contact` result that succeeded, keyed by
 *  contact id. Lets us patch already-rendered cards in prior bubbles
 *  without requiring the user to refresh the page (Day 5 smoke 3b
 *  caught the stale-render bug).
 *
 *  Only handles `update_contact` today:
 *  - `create_contact` returns a brand-new id, which by definition
 *    can't exist in any prior bubble — nothing to patch retroactively.
 *  - `resolve_change_request` mutates a contact but only returns
 *    {request_id, status, applied} — no updated contact payload yet.
 *    If the handler is extended to include the updated contact,
 *    hook it in here. */
function extractContactUpdates(
  traces: ToolCallTrace[],
): Map<number, ContactCardData> {
  const updates = new Map<number, ContactCardData>();
  for (const t of traces) {
    if (t.name !== "update_contact") continue;
    const r = t.result as { updated?: ContactCardData };
    if (r.updated && typeof r.updated.id === "number") {
      updates.set(r.updated.id, r.updated);
    }
  }
  return updates;
}

/** Replace any contacts in prior assistant bubbles' contactGroups
 *  whose id matches a successful update from the latest response. Pure
 *  and structure-preserving: bubbles unaffected by the updates are
 *  returned by identity so React doesn't re-render them. */
function patchBubblesWithUpdates(
  bubbles: Bubble[],
  updates: Map<number, ContactCardData>,
): Bubble[] {
  if (updates.size === 0) return bubbles;
  return bubbles.map((b) => {
    if (b.kind !== "assistant" || b.contactGroups.length === 0) return b;
    let bubbleChanged = false;
    const newGroups = b.contactGroups.map((g) => {
      let groupChanged = false;
      const newContacts = g.contacts.map((c) => {
        const update = updates.get(c.id);
        if (update) {
          groupChanged = true;
          bubbleChanged = true;
          return update;
        }
        return c;
      });
      return groupChanged ? { ...g, contacts: newContacts } : g;
    });
    return bubbleChanged ? { ...b, contactGroups: newGroups } : b;
  });
}

/** Extract every search_contacts result from a chat response. Empty
 *  results are surfaced too — the user gets explicit "no contacts
 *  matched" feedback instead of an unexplained absence of cards. */
function extractContactGroups(traces: ToolCallTrace[]): ContactGroup[] {
  const groups: ContactGroup[] = [];
  for (const t of traces) {
    if (t.name !== "search_contacts") continue;
    const r = t.result as Partial<SearchContactsResult>;
    if (!Array.isArray(r.results)) continue;
    groups.push({
      contacts: r.results,
      truncated: Boolean(r.truncated),
      limit: typeof r.limit === "number" ? r.limit : r.results.length,
      // The search params live on the trace itself. If ex_government was
      // filtered, the user is asking about ex-gov contacts, so it's worth
      // surfacing the pill on each card.
      showExGov: Boolean(t.params?.ex_government),
      // Reproduce-this-list filter for the Export button. Backend
      // /export accepts only query/primary_fund/contact_type today;
      // anything richer (gender, country, lp_subtype, fly_status,
      // ex_government) is ignored and the export returns the broader
      // visible set. Worth widening the export schema later.
      exportFilter: _coerceExportFilter(t.params ?? {}),
    });
  }
  return groups;
}

function errorMessageFor(e: unknown): string {
  if (e instanceof ApiError) {
    switch (e.status) {
      case 413:
        return "Message too long. Try breaking it into smaller pieces.";
      case 429:
        return "Too many requests. Wait a minute and try again.";
      case 402:
        return "Today's token budget is exhausted. Resets tomorrow.";
      case 401:
        return "Your session expired. Refresh and sign in again.";
      default:
        if (e.status >= 500) return "Something went wrong. Try again.";
        return `Request failed (${e.status}).`;
    }
  }
  return "Network error. Check your connection and try again.";
}

export function ChatInterface() {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  // Phase 3 Slice 3 — tracks how the current input value originated.
  // Set to "voice" when MicButton.onTranscript fires; persists through
  // manual edits (a dictated message edited by hand is still voice-
  // originated); cleared on send so the next message starts fresh.
  const [lastInputSource, setLastInputSource] = useState<"text" | "voice">(
    "text",
  );
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [bubbles, sending]);

  const charCount = input.length;
  const overLimit = charCount > CHAT_INPUT_MAX_CHARS;
  const canSend = !sending && input.trim().length > 0 && !overLimit;

  const send = async () => {
    const message = input.trim();
    if (sending || !message || message.length > CHAT_INPUT_MAX_CHARS) return;
    const history: ChatMessage[] = bubbles
      .filter(
        (b): b is Bubble & { kind: "user" | "assistant" } =>
          b.kind === "user" || b.kind === "assistant",
      )
      .slice(-HISTORY_MAX_TURNS)
      .map((b) => ({ role: b.kind, content: b.content }));

    setBubbles((prev) => [...prev, { kind: "user", content: message }]);
    setInput("");
    setSending(true);
    const mode = lastInputSource;
    setLastInputSource("text");

    try {
      const response: ChatResponse = await chat.send({
        message,
        history,
        mode,
      });
      const updates = extractContactUpdates(response.tool_calls);
      setBubbles((prev) => [
        ...patchBubblesWithUpdates(prev, updates),
        {
          kind: "assistant",
          content: response.reply,
          contactGroups: extractContactGroups(response.tool_calls),
          // Auto-play TTS for every dictation-originated reply.
          // Audio playback is always-on for voice mode; the only way
          // to silence it is to type instead of dictate.
          shouldAutoPlayTTS: mode === "voice",
        },
      ]);
    } catch (e) {
      setBubbles((prev) => [
        ...prev,
        { kind: "error", content: errorMessageFor(e) },
      ]);
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send();
    }
  };

  // Send-button pulse counter — incremented every time a dictation
  // transcript lands. Used as a React key on the Send button so the
  // remount restarts the two-iteration CSS animation cleanly.
  const [sendPulseKey, setSendPulseKey] = useState(0);

  // Drop transcript into the textarea and flag the next send as voice-
  // originated so /chat returns an ear-friendly reply AND triggers
  // TTS auto-play on the assistant bubble. Bump the pulse key so the
  // Send button blinks twice to cue the user to send.
  const onDictationTranscript = (text: string) => {
    setInput((prev) => (prev.trim() ? `${prev} ${text}` : text));
    setLastInputSource("voice");
    setSendPulseKey((k) => k + 1);
    inputRef.current?.focus();
  };

  const hasMessages = bubbles.length > 0 || sending;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hasMessages ? (
        <div className="flex justify-center pb-3 pt-2">
          <DictationButton
            size="large"
            disabled={sending}
            onTranscript={onDictationTranscript}
          />
        </div>
      ) : null}
      <div
        ref={scrollRef}
        className="flex-1 space-y-4 overflow-y-auto px-1 py-4"
      >
        {bubbles.length === 0 && !sending ? (
          <p className="mx-auto mt-12 max-w-prose text-center text-sm italic opacity-60">
            Welcome to the Global DIN team portal. Right now, our portal is just
            a place for finding contacts, adding more contacts, and adding notes
            to our contacts. In just a few weeks, we can use this portal for
            much more — from sending emails to searching our knowledge base, to
            uploading docs to our Google Drive. We&apos;ll add more capabilities
            every week. Got an idea? Email{" "}
            <a
              href="mailto:support@example.com"
              className="underline hover:opacity-100"
            >
              support@example.com
            </a>
            .
          </p>
        ) : null}

        {bubbles.map((b, i) => (
          <MessageErrorBoundary key={i}>
            <Bubble bubble={b} />
          </MessageErrorBoundary>
        ))}

        {sending ? (
          <div className="flex justify-start">
            <div
              className="inline-flex items-center gap-2 rounded-lg bg-din-cream px-4 py-2 text-sm italic opacity-70 dark:bg-din-navy-soft"
              role="status"
              aria-label="Goddess is thinking"
            >
              <Loader2 size={14} className="animate-spin" />
              Thinking…
            </div>
          </div>
        ) : null}
      </div>

      <div className="border-t border-din-blue/20 pt-3 dark:border-din-cream/20">
        {hasMessages ? (
          <div className="mb-2 flex justify-center">
            <DictationButton
              size="compact"
              disabled={sending}
              onTranscript={onDictationTranscript}
            />
          </div>
        ) : null}
        <div className="flex items-center gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Type a question…"
            rows={2}
            disabled={sending}
            className="flex-1 resize-none rounded border border-din-blue/30 bg-white p-3 text-sm focus:border-din-blue focus:outline-none focus:ring-1 focus:ring-din-gold disabled:opacity-60 dark:border-din-cream/30 dark:bg-din-navy-soft dark:text-din-cream"
          />
          <button
            key={`send-${sendPulseKey}`}
            type="button"
            onClick={send}
            disabled={!canSend}
            className={`inline-flex h-10 shrink-0 items-center justify-center rounded bg-din-blue px-6 text-xs font-bold uppercase tracking-wide text-white hover:bg-din-blue-dark focus:outline-none focus:ring-2 focus:ring-din-gold disabled:cursor-not-allowed disabled:opacity-50 dark:bg-din-cream dark:text-din-navy dark:hover:bg-din-cream/85 ${
              sendPulseKey > 0 ? "animate-send-pulse" : ""
            }`}
          >
            {sending ? "…" : "Send"}
          </button>
        </div>

        <div className="mt-1">
          <span
            className={`text-xs ${
              overLimit
                ? "font-bold text-din-red dark:text-din-red-soft"
                : "opacity-60"
            }`}
          >
            {charCount.toLocaleString()} /{" "}
            {CHAT_INPUT_MAX_CHARS.toLocaleString()}
          </span>
        </div>
      </div>
    </div>
  );
}

/** Headless effect: plays the assistant reply's audio exactly once
 *  on mount. No UI; the dictation button is the only voice
 *  affordance in the new portal layout. */
function AutoPlayTTS({ text }: { text: string }) {
  const { play } = useTTSPlayback();
  const didPlayRef = useRef(false);
  useEffect(() => {
    if (didPlayRef.current || text.trim().length === 0) return;
    didPlayRef.current = true;
    void play(text);
  }, [text, play]);
  return null;
}

function Bubble({ bubble }: { bubble: Bubble }) {
  if (bubble.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-din-blue px-4 py-2 text-sm text-white dark:bg-din-cream dark:text-din-navy">
          {bubble.content}
        </div>
      </div>
    );
  }

  if (bubble.kind === "error") {
    return (
      <div className="flex justify-center">
        <div className="max-w-[85%] rounded border border-din-red/50 bg-din-red/5 px-4 py-2 text-center text-xs text-din-red dark:text-din-red-soft">
          {bubble.content}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[95%] space-y-3 sm:max-w-[85%]">
        {bubble.contactGroups.map((g, i) => (
          <ContactCardList
            key={i}
            contacts={g.contacts}
            truncated={g.truncated}
            limit={g.limit}
            showExGov={g.showExGov}
            exportFilter={g.exportFilter}
          />
        ))}
        {bubble.content ? (
          <div className="din-md rounded-lg bg-din-cream px-4 py-2 text-sm text-din-navy dark:bg-din-navy-soft dark:text-din-cream">
            <ReactMarkdown
              allowedElements={ALLOWED_MD_ELEMENTS}
              unwrapDisallowed
            >
              {bubble.content}
            </ReactMarkdown>
            {bubble.shouldAutoPlayTTS ? (
              <AutoPlayTTS text={bubble.content} />
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

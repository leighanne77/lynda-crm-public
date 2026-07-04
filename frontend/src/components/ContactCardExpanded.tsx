/**
 * Expanded contact view — Phase 2 Slice 6.8.
 *
 * Opens when the user double-clicks a ContactCard. Renders:
 *   1. The full ContactCard on top (same visual as the inline list)
 *   2. A scrollable notes pane (was hidden on the inline card)
 *   3. The change log at the very bottom (fetched from
 *      GET /api/contacts/{id}/changelog)
 *
 * Closes on Escape, click-outside the panel, or the X button. Voice-
 * first: Esc is the primary close path so the user doesn't have to
 * click a target. Modal is portal-mounted into the document body so
 * the overlay reliably covers whatever container the card was in.
 *
 * For redacted rows: the parent skips the double-click handler (we
 * never open the expansion on a redacted card — there's nothing
 * useful to show beyond what the inline tile already does), so this
 * component assumes a full row with notes potentially populated.
 */

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ExternalLink, X } from "lucide-react";

import {
  type ChangelogEntry,
  contacts as contactsApi,
  type ContactCardData,
  nextSteps as nextStepsApi,
  type NextStepRow,
} from "../api/client";
import { ContactCard } from "./ContactCard";

interface ContactCardExpandedProps {
  contact: ContactCardData;
  onClose: () => void;
}

export function ContactCardExpanded({
  contact,
  onClose,
}: ContactCardExpandedProps) {
  const [log, setLog] = useState<ChangelogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [steps, setSteps] = useState<NextStepRow[] | null>(null);
  const [stepsError, setStepsError] = useState<string | null>(null);

  // Close on Escape — voice-first low-click-count path.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Fetch the changelog when the modal opens. AbortController cancels
  // the request if the user closes the modal before it resolves.
  useEffect(() => {
    const ac = new AbortController();
    contactsApi
      .changelog(contact.id, ac.signal)
      .then((entries) => setLog(entries))
      .catch((err) => {
        if (ac.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Failed to load history");
      });
    return () => ac.abort();
  }, [contact.id]);

  // Fetch next-steps in parallel — separate request, independent error
  // state. AbortController cancels both on close.
  useEffect(() => {
    const ac = new AbortController();
    contactsApi
      .nextSteps(contact.id, ac.signal)
      .then((rows) => setSteps(rows))
      .catch((err) => {
        if (ac.signal.aborted) return;
        setStepsError(
          err instanceof Error ? err.message : "Failed to load next steps",
        );
      });
    return () => ac.abort();
  }, [contact.id]);

  async function handleComplete(stepId: number) {
    try {
      await nextStepsApi.complete(stepId);
      // Refetch so done_at + ordering refresh from server.
      const fresh = await contactsApi.nextSteps(contact.id);
      setSteps(fresh);
    } catch (err) {
      setStepsError(err instanceof Error ? err.message : "Failed to mark done");
    }
  }

  const modal = (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Expanded view for ${contact.name}`}
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-din-navy/60 px-4 py-8 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-2xl rounded-md bg-din-cream-soft text-din-navy shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          aria-label="Close expanded view"
          onClick={onClose}
          className="absolute right-3 top-3 z-10 rounded-full p-1 text-din-navy/60 hover:bg-din-navy/10 hover:text-din-navy"
        >
          <X size={18} />
        </button>

        <div className="flex justify-center p-6 pb-2">
          <ContactCard contact={contact} />
        </div>

        {/* Slice 6.10 — Next Steps pane. Sits ABOVE notes per
            spec: the forward-looking actions are what the user
            wants to see first when opening a contact. */}
        <section className="border-t border-din-navy/10 px-6 py-4">
          <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-din-navy/70">
            Next steps
          </h4>
          {stepsError ? (
            <p className="text-sm italic text-din-red">{stepsError}</p>
          ) : steps === null ? (
            <p className="text-sm italic text-din-navy/50">
              Loading next steps…
            </p>
          ) : steps.filter((s) => !s.done).length === 0 ? (
            <p className="text-sm italic text-din-navy/50">
              No pending next steps. Ask Goddess: "add a next step for{" "}
              {contact.name} to ... ".
            </p>
          ) : (
            <ol className="space-y-1.5 text-sm">
              {steps
                .filter((s) => !s.done)
                .map((step) => (
                  <li
                    key={step.id}
                    className="flex flex-col gap-1 rounded border border-din-navy/10 bg-white/60 px-2 py-1.5"
                  >
                    <div className="flex items-center gap-2">
                      <span
                        className="shrink-0 rounded-full border border-din-navy/25 bg-white px-1.5 py-px text-[8px] font-bold uppercase tracking-wide text-din-navy/60"
                        title={`Assigned to ${step.owner_name ?? "unknown"}`}
                      >
                        {step.owner_initials ?? "?"}
                      </span>
                      <span className="flex-1 truncate" title={step.title}>
                        {step.title}
                      </span>
                      {step.google_task_url ? (
                        <a
                          href={step.google_task_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Open Google Tasks (find the DIN: Next Steps list in the sidebar)"
                          className="shrink-0 text-din-blue hover:text-din-navy"
                        >
                          <ExternalLink size={14} />
                        </a>
                      ) : null}
                      <button
                        type="button"
                        onClick={() => handleComplete(step.id)}
                        title="Mark done"
                        className="shrink-0 rounded-full p-1 text-din-navy/60 hover:bg-din-gold/20 hover:text-din-navy"
                      >
                        <Check size={14} />
                      </button>
                    </div>
                    {/* Assignee name — explicit, not just the initials
                        pill. Makes "who's doing this" the foreground
                        concept, not a decorative badge. */}
                    {step.owner_name ? (
                      <div className="pl-7 text-[10px] uppercase tracking-wide text-din-navy/55">
                        Assigned to {step.owner_name}
                      </div>
                    ) : null}
                  </li>
                ))}
            </ol>
          )}
          {steps && steps.some((s) => s.done) ? (
            <details className="mt-3 text-xs">
              <summary className="cursor-pointer text-din-navy/55">
                Show {steps.filter((s) => s.done).length} completed
              </summary>
              <ol className="mt-1 space-y-1 pl-2">
                {steps
                  .filter((s) => s.done)
                  .map((step) => (
                    <li
                      key={step.id}
                      className="flex items-center gap-2 text-din-navy/55"
                    >
                      <span className="font-mono text-[10px]">
                        {step.done_at
                          ? new Date(step.done_at).toLocaleDateString()
                          : ""}
                      </span>
                      <span className="line-through opacity-70">
                        {step.title}
                      </span>
                      <span className="opacity-60">
                        ({step.owner_initials})
                      </span>
                    </li>
                  ))}
              </ol>
            </details>
          ) : null}
        </section>

        {/* Notes pane — scrollable; renders even when empty so the
            user knows the section exists. */}
        <section className="border-t border-din-navy/10 px-6 py-4">
          <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-din-navy/70">
            Notes
          </h4>
          {contact.notes ? (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">
              {contact.notes}
            </p>
          ) : (
            <p className="text-sm italic text-din-navy/50">
              No notes on this contact.
            </p>
          )}
        </section>

        {/* Change log — newest first. MVP shows actor + timestamp +
            action label. Slice 6.9 adds field-level diffs to each row. */}
        <section className="border-t border-din-navy/10 px-6 py-4">
          <h4 className="mb-2 text-xs font-bold uppercase tracking-wide text-din-navy/70">
            Change log
          </h4>
          {error ? (
            <p className="text-sm italic text-din-red">{error}</p>
          ) : log === null ? (
            <p className="text-sm italic text-din-navy/50">Loading history…</p>
          ) : log.length === 0 ? (
            <p className="text-sm italic text-din-navy/50">
              No recorded changes yet.
            </p>
          ) : (
            <ol className="space-y-2 text-sm">
              {log.map((entry) => (
                <li key={entry.id} className="flex gap-3">
                  <span className="shrink-0 font-mono text-xs text-din-navy/60">
                    {new Date(entry.when).toLocaleDateString(undefined, {
                      year: "numeric",
                      month: "short",
                      day: "numeric",
                    })}
                  </span>
                  <div className="flex-1">
                    <div>
                      <span className="font-medium">
                        {entry.actor_name ?? `User ${entry.actor_id}`}
                      </span>{" "}
                      <span className="text-din-navy/70">
                        {entry.action_label}
                      </span>
                    </div>
                    <ChangelogDetail entry={entry} />
                  </div>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </div>
  );

  return createPortal(modal, document.body);
}

/**
 * Renders the action-specific detail line(s) for one change-log entry.
 * Falls back to nothing if metadata is absent (older rows pre-Slice 6.9)
 * or empty — the parent already shows actor + action label.
 *
 * Privacy: for redacted-view callers the backend strips diff entries
 * whose field isn't in reveal_fields, so this component renders
 * whatever it gets — the gate lives server-side.
 */
function ChangelogDetail({ entry }: { entry: ChangelogEntry }) {
  const m = entry.metadata;
  if (!m) return null;

  // update_contact -> field-level diff list
  if (entry.action === "update_contact" && m.changes && m.changes.length > 0) {
    return (
      <ul className="mt-1 space-y-0.5 text-xs">
        {m.changes.map((c, i) => (
          <li key={i} className="text-din-navy/70">
            <span className="font-mono text-din-navy/85">{c.field}</span>:{" "}
            <span className="line-through opacity-60">
              {formatValue(c.old)}
            </span>
            {" → "}
            <span className="font-medium">{formatValue(c.new)}</span>
          </li>
        ))}
      </ul>
    );
  }

  // transfer_contact -> "from X to Y" with admin tag when applicable
  if (entry.action === "transfer_contact") {
    const from = m.old_owner_name ?? "former owner";
    const to = m.new_owner_name ?? "new owner";
    const adminTag = m.by_admin ? " (admin override)" : "";
    return (
      <div className="mt-1 text-xs text-din-navy/70">
        from <span className="font-medium">{from}</span> to{" "}
        <span className="font-medium">{to}</span>
        {adminTag}
      </div>
    );
  }

  // resolve_change_request -> kind + decision + optional note
  if (entry.action === "resolve_change_request") {
    const kindLabel = m.kind === "off_fly_list" ? "off-fly-list" : m.kind;
    const verb = m.decision === "approve" ? "approved" : "disapproved";
    return (
      <div className="mt-1 text-xs text-din-navy/70">
        {verb} the {kindLabel} request
        {m.note ? (
          <>
            {" — "}
            <span className="italic">"{m.note}"</span>
          </>
        ) : null}
      </div>
    );
  }

  // request_change -> kind + optional reason
  if (entry.action === "request_change") {
    const kindLabel = m.kind === "off_fly_list" ? "off-fly-list" : m.kind;
    return (
      <div className="mt-1 text-xs text-din-navy/70">
        filed {kindLabel} request
        {m.reason ? (
          <>
            {" — "}
            <span className="italic">"{m.reason}"</span>
          </>
        ) : null}
      </div>
    );
  }

  return null;
}

/** Pretty-print an arbitrary metadata value for display. */
function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "(empty)";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "(empty)";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

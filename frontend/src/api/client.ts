/**
 * Typed fetch wrapper around the lynda-crm backend.
 *
 * Every call sends `credentials: "include"` so the httpOnly session
 * cookie travels automatically. Same shape across login, chat, and
 * everything downstream.
 *
 * Throws `ApiError` with status + body so callers can render specific
 * error UI (401 → redirect to login; 402 → "budget exhausted"; 429 →
 * "slow down"; 413 → "message too long"; 5xx → generic).
 */

const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
    message?: string,
  ) {
    super(message ?? `HTTP ${status}`);
    this.name = "ApiError";
  }
}

interface RequestOpts {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: opts.method ?? "GET",
    credentials: "include",
    headers: opts.body
      ? { "Content-Type": "application/json", Accept: "application/json" }
      : { Accept: "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
    signal: opts.signal,
  });

  // 204 No Content has no body — return as null cast to T (caller asserts).
  if (response.status === 204) {
    return null as T;
  }

  const contentType = response.headers.get("content-type") ?? "";
  const data = contentType.includes("application/json")
    ? await response.json()
    : await response.text();

  if (!response.ok) {
    throw new ApiError(response.status, data);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, { method: "GET", signal }),
  post: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "POST", body, signal }),
  patch: <T>(path: string, body?: unknown, signal?: AbortSignal) =>
    request<T>(path, { method: "PATCH", body, signal }),
  delete: <T>(path: string, signal?: AbortSignal) =>
    request<T>(path, { method: "DELETE", signal }),
};

// ---------------------------------------------------------------------------
// Typed endpoint wrappers
// ---------------------------------------------------------------------------

export interface CurrentUser {
  id: number;
  email: string;
  name: string | null;
  role: string;
  intro_seen: boolean;
}

export const auth = {
  /** Start the Google OAuth flow — full page navigation, not fetch. */
  startLogin: () => {
    window.location.href = `${API_BASE}/auth/google`;
  },
  /**
   * LOCAL-ONLY dev sign-in that skips Google. The backend 404s this
   * route whenever ENTERPRISE_MODE is on, so it can never work in
   * production. Surfaced only on the dev login screen (import.meta.env.DEV).
   */
  devLogin: () => {
    window.location.href = `${API_BASE}/auth/dev-login`;
  },
  logout: () => api.post<null>("/auth/logout"),
};

export const users = {
  me: (signal?: AbortSignal) => api.get<CurrentUser>("/users/me", signal),
  markIntroSeen: () => api.patch<null>("/users/me/intro-seen"),
};

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ToolCallTrace {
  name: string;
  params: Record<string, unknown>;
  result: Record<string, unknown>;
}

/** Mirrors backend `_format_contact()` (app/services/tool_dispatch.py). */
export type PrimaryFund =
  | "Critical Minerals"
  | "Maritime"
  | "Energy"
  | "General";
export type ContactType =
  | "LP"
  | "Potential LP"
  | "Portfolio"
  | "Government"
  | "Intermediary"
  | "Advisor"
  | "Inspiration"
  | "Other";
export type Gender = "Female" | "Male" | "Unknown";
export type LpSubtype =
  | "Sovereign Wealth Fund"
  | "Family Office"
  | "Pension"
  | "Endowment"
  | "Insurance"
  | "Foundation"
  | "Other";
export type FlyStatus =
  | "Must Fly"
  | "Fly List"
  | "Maybe Must Fly"
  | "Unknown"
  | "Off Fly List";
export type ExGovernment = "Yes" | "No" | "Don't Know";

export interface ContactCardData {
  id: number;
  name: string;
  company_name: string | null;
  title: string | null;
  email: string | null;
  cell_phone: string | null;
  office_phone: string | null;
  primary_fund: PrimaryFund;
  contact_type: ContactType;
  sectors: string[];
  is_private: boolean;
  gender: Gender;
  country: string | null;
  lp_subtype: LpSubtype | null;
  fly_status: FlyStatus;
  image_url: string | null;
  ex_government: ExGovernment;
  /** Slice 6.11 — current government employee (of any nation). Drives
   *  the 3-side fund-colored border on the card. Auto-set from email
   *  domain on create; owner can toggle. False on redacted rows
   *  (the flag is derived from PII so we don't leak it). */
  is_gov_employee?: boolean;
  /** User-set rolodex patina marks. null = use deterministic auto-pick;
   *  [] = explicit no patina; [items] = up to 3 user-added items.
   *  Optional in the TS type for ergonomics in test fixtures and sample
   *  data — the real API always returns either null or a list. */
  patina_overrides?: PatinaItemPayload[] | null;
  /** Free-text notes — Slice 6.8 surfaced this in tool results so the
   *  expanded card view + chat readback can display them. Always null
   *  on redacted rows (notes is on the never-reveal PII list). */
  notes?: string | null;
  /** First name of the teammate who owns this contact (e.g. "Alex Rivera").
   *  Optional in the TS type for fixtures; the real API always returns it
   *  on rows from search/create/update. */
  owner_name?: string | null;
  /** Short all-caps badge label (1–3 chars) derived from owner_name —
   *  e.g. "LA", "E", "HJ". Used by the corner ownership pill on the card. */
  owner_initials?: string | null;
  /** True when the calling user is the owner. Optional for the same
   *  fixture reason; real API always sets it. */
  is_self_owned?: boolean;
  /** Phase 2 Slice 6.5 — when true, this row is a partial-reveal of a
   *  PRIVATE contact owned by a teammate. Only the fields listed in
   *  reveal_fields are populated; everything else (name, email, phone,
   *  title, notes, image_url) is forced to a placeholder or null.
   *  Render with the RedactedContactCard variant. */
  is_redacted?: boolean;
  /** Phase 2 Slice 6.5 — list of column names visible on this row.
   *  Owners see their own (so the settings UI can render it); non-owners
   *  viewing a fully-visible row see null (they don't need it). On a
   *  redacted row, the list of fields that ARE populated. */
  reveal_fields?: string[] | null;
  /** ISO timestamps. Used to render the auto-UPDATED stamp for 14 days
   *  after a contact is modified. Optional in the TS type for fixtures. */
  created_at?: string;
  updated_at?: string;
}

// Individual patina-item shapes come from Pydantic via scripts/sync_types.py.
// CI fails if generated_types.ts drifts from the backend source of truth.
import type {
  CheckMarkItem,
  DogEarItem,
  DoodleItem,
  MailingLabelItem,
  PencilNoteItem,
  SmudgeItem,
  StickerItem,
  TypewrittenItem,
} from "./generated_types";

export type {
  CheckMarkItem,
  DogEarItem,
  DoodleItem,
  MailingLabelItem,
  PencilNoteItem,
  SmudgeItem,
  StickerItem,
  TypewrittenItem,
} from "./generated_types";

/** Patina item payload — discriminated union over the individual kinds.
 *  The per-kind shapes are generated from Pydantic; the union itself is
 *  a one-line hand-written composition. */
export type PatinaItemPayload =
  | SmudgeItem
  | DogEarItem
  | PencilNoteItem
  | DoodleItem
  | CheckMarkItem
  | TypewrittenItem
  | MailingLabelItem
  | StickerItem;

/** Shape returned by the `search_contacts` tool. */
export interface SearchContactsResult {
  count: number;
  truncated: boolean;
  limit: number;
  results: ContactCardData[];
}

export interface ChatRequestBody {
  message: string;
  history: ChatMessage[];
  mode?: "text" | "voice";
}

export interface ChatResponse {
  reply: string;
  tool_calls: ToolCallTrace[];
  input_tokens_used: number;
  output_tokens_used: number;
}

/** Mirrors backend `chat_input_max_chars` (app/config.py). */
export const CHAT_INPUT_MAX_CHARS = 4000;

export const chat = {
  send: (body: ChatRequestBody, signal?: AbortSignal) =>
    api.post<ChatResponse>("/chat", body, signal),
};

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export interface AuditRow {
  id: number;
  user_id: number;
  user_email: string | null;
  action: string;
  target_type: string | null;
  target_id: number | null;
  payload_hash: string | null;
  created_at: string;
}

export interface AuditListResponse {
  rows: AuditRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface AuditListQuery {
  page?: number;
  page_size?: number;
  user_id?: number;
  action?: string;
}

function _qs(q: AuditListQuery): string {
  const params = new URLSearchParams();
  if (q.page !== undefined) params.set("page", String(q.page));
  if (q.page_size !== undefined) params.set("page_size", String(q.page_size));
  if (q.user_id !== undefined) params.set("user_id", String(q.user_id));
  if (q.action) params.set("action", q.action);
  const s = params.toString();
  return s ? `?${s}` : "";
}

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

/** Filter criteria for /export/sheets and /export/csv. Mirrors a subset
 *  of search_contacts — same shape the chat already exercises. */
export interface ExportFilter {
  query?: string;
  primary_fund?: PrimaryFund;
  contact_type?: ContactType;
}

export interface ExportSheetsSuccess {
  kind: "sheet";
  sheet_url: string;
  sheet_id: string;
  contact_count: number;
}

export interface ExportSheetsCsvFallback {
  kind: "csv";
  blob: Blob;
  filename: string;
}

export type ExportSheetsResult = ExportSheetsSuccess | ExportSheetsCsvFallback;

function _filenameFromContentDisposition(
  header: string | null,
  fallback: string,
): string {
  if (!header) return fallback;
  const m = /filename="?([^";]+)"?/i.exec(header);
  return m ? m[1] : fallback;
}

export const exports = {
  /** POST /export/sheets. Returns either a Sheet metadata payload (JSON)
   *  or — when Drive scope was revoked / admin not approved — an
   *  automatic CSV download (text/csv body). Uses raw fetch so we can
   *  branch on Content-Type without going through the JSON-only helper. */
  exportSheets: async (filter: ExportFilter): Promise<ExportSheetsResult> => {
    const response = await fetch(`${API_BASE}/export/sheets`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "*/*" },
      body: JSON.stringify(filter),
    });
    if (!response.ok) {
      const body = response.headers
        .get("content-type")
        ?.includes("application/json")
        ? await response.json()
        : await response.text();
      throw new ApiError(response.status, body);
    }
    const ct = response.headers.get("content-type") ?? "";
    if (ct.includes("text/csv")) {
      const blob = await response.blob();
      const filename = _filenameFromContentDisposition(
        response.headers.get("content-disposition"),
        "din_contacts.csv",
      );
      return { kind: "csv", blob, filename };
    }
    const json = (await response.json()) as ExportSheetsSuccess;
    return { ...json, kind: "sheet" };
  },

  /** POST /export/csv — always returns a CSV download. Use when the
   *  user explicitly wants a file rather than a sheet. */
  exportCsv: async (filter: ExportFilter): Promise<ExportSheetsCsvFallback> => {
    const response = await fetch(`${API_BASE}/export/csv`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json", Accept: "*/*" },
      body: JSON.stringify(filter),
    });
    if (!response.ok) {
      const body = response.headers
        .get("content-type")
        ?.includes("application/json")
        ? await response.json()
        : await response.text();
      throw new ApiError(response.status, body);
    }
    const blob = await response.blob();
    const filename = _filenameFromContentDisposition(
      response.headers.get("content-disposition"),
      "din_contacts.csv",
    );
    return { kind: "csv", blob, filename };
  },
};

// ---------------------------------------------------------------------------
// Admin
// ---------------------------------------------------------------------------

export const admin = {
  /** Fetch one page of audit rows. Admins only — non-admins get 403. */
  listAudit: (q: AuditListQuery = {}, signal?: AbortSignal) =>
    api.get<AuditListResponse>(`/admin/audit${_qs(q)}`, signal),
  /** URL the browser navigates to for the CSV download. The session
   *  cookie travels automatically; the backend handles auth + role. */
  auditCsvUrl: (q: AuditListQuery = {}) =>
    `${API_BASE}/admin/audit.csv${_qs(q)}`,
};

// ---------------------------------------------------------------------------
// Reviews (Phase 2 review queue UI)
// ---------------------------------------------------------------------------

export type ReviewStatus = "pending" | "approved" | "disapproved";
export type ReviewKind = "off_fly_list" | "patina_override";

export interface ReviewRow {
  id: number;
  requester_id: number;
  requester_email: string | null;
  contact_id: number;
  contact_name: string;
  kind: ReviewKind;
  payload: Record<string, unknown> | unknown[] | null;
  reason: string | null;
  status: ReviewStatus;
  resolution_note: string | null;
  created_at: string;
  resolved_at: string | null;
  resolved_by_id: number | null;
}

export interface ReviewListResponse {
  rows: ReviewRow[];
  total: number;
  page: number;
  page_size: number;
}

export interface ReviewListQuery {
  status?: ReviewStatus | "all" | "";
  kind?: ReviewKind;
  page?: number;
  page_size?: number;
}

function _reviewQs(q: ReviewListQuery): string {
  const params = new URLSearchParams();
  if (q.status !== undefined) params.set("status", q.status);
  if (q.kind) params.set("kind", q.kind);
  if (q.page !== undefined) params.set("page", String(q.page));
  if (q.page_size !== undefined) params.set("page_size", String(q.page_size));
  const s = params.toString();
  return s ? `?${s}` : "";
}

export const reviews = {
  /** List change requests filed against contacts the current user owns. */
  list: (q: ReviewListQuery = {}, signal?: AbortSignal) =>
    api.get<ReviewListResponse>(`/admin/reviews${_reviewQs(q)}`, signal),
  /** Approve (apply change) or disapprove (close) a pending request. */
  resolve: (id: number, decision: "approve" | "disapprove", note?: string) =>
    api.post<ReviewRow>(`/admin/reviews/${id}/resolve`, { decision, note }),
};

// Phase 2 Slice 6.8 — contact change-log entries.
// Slice 6.9 adds `metadata` for field-level diff rendering. Shape varies
// by action; the renderer in ContactCardExpanded normalises:
//   update_contact -> { changes: [{ field, old, new }] }
//   transfer_contact -> { old_owner_name, new_owner_name, by_admin, ... }
//   resolve_change_request -> { kind, decision, applied, note, ... }
//   request_change -> { kind, request_id, reason, ... }
//   create_contact -> { initial_fields: { ... } }
//   delete_contact -> { last_state: { name, company_name, ... } }
export interface ChangelogChange {
  field: string;
  old: unknown;
  new: unknown;
}

export interface ChangelogMetadata {
  changes?: ChangelogChange[];
  initial_fields?: Record<string, unknown>;
  last_state?: Record<string, unknown>;
  old_owner_name?: string | null;
  new_owner_name?: string | null;
  by_admin?: boolean;
  kind?: string;
  decision?: "approve" | "disapprove";
  applied?: boolean;
  note?: string | null;
  reason?: string | null;
  request_id?: number;
  contact_id?: number;
  // Forward-compat — unknown keys allowed.
  [key: string]: unknown;
}

export interface ChangelogEntry {
  id: number;
  when: string; // ISO timestamp
  actor_id: number;
  actor_name: string | null;
  action: string;
  action_label: string;
  metadata?: ChangelogMetadata | null;
}

export const contacts = {
  /** Fetch the audit-derived change history for one contact (newest first).
   *  Returns 404 if the contact is fully hidden from the caller. */
  changelog: (id: number, signal?: AbortSignal) =>
    api.get<ChangelogEntry[]>(`/contacts/${id}/changelog`, signal),
  /** Fetch open + recently-completed next-steps for a contact. */
  nextSteps: (id: number, signal?: AbortSignal) =>
    api.get<NextStepRow[]>(`/contacts/${id}/next-steps`, signal),
};

// Phase 2 Slice 6.10 — next-steps activity log.
export interface NextStepRow {
  id: number;
  contact_id: number;
  title: string;
  owner_id: number;
  owner_name: string | null;
  owner_initials: string | null;
  created_by_id: number;
  google_task_list_id: string | null;
  google_task_url: string | null;
  done: boolean;
  done_at: string | null;
  created_at: string;
}

export const nextSteps = {
  /** Mark a next-step done. Only the step's owner or contact owner may
   *  succeed; everyone else gets 403 from the backend. */
  complete: (id: number) =>
    api.patch<NextStepRow>(`/next-steps/${id}`, { done: true }),
};

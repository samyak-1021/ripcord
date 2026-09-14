// Thin typed client for the Ripcord FastAPI backend. The dashboard is a pure
// frontend — it talks to the backend over REST + SSE, never via Next API routes.
//
// Auth: every request carries the operator's API key. The key lives in
// localStorage rather than a cookie because the API is a separate origin and
// this is an operator tool, not an end-user app — there is no session to
// federate and no server component that needs to read it.

export type Variant = {
  id?: number;
  key: string;
  value?: unknown;
  weight: number;
};

export type Rule = {
  id?: number;
  attribute: string;
  operator: string;
  values: string[];
  priority?: number;
  variant?: string | null;
};

export type Flag = {
  id: number;
  key: string;
  name: string;
  description: string | null;
  enabled: boolean;
  rollout_percentage: number;
  version: number;
  created_at: string;
  updated_at: string;
  off_variant: string | null;
  rules: Rule[];
  variants: Variant[];
};

export type Evaluation = {
  flag_key: string;
  user_id: string;
  enabled: boolean;
  reason: string;
  variant: string | null;
  value: unknown;
};

export type AuditEntry = {
  id: number;
  flag_key: string;
  action: string;
  actor: string;
  details: Record<string, unknown> | null;
  created_at: string;
};

export type Stats = {
  flags_total: number;
  flags_enabled: number;
  flags_disabled: number;
  evaluations_total: number;
  evaluations_by_result: Record<string, number>;
};

export type ApiKey = {
  key_id: string;
  name: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
};

export type FlagUpdate = {
  version: number;
  name?: string;
  enabled?: boolean;
  rollout_percentage?: number;
  rules?: Omit<Rule, "id">[];
  variants?: Omit<Variant, "id">[];
  off_variant?: string | null;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const STORAGE_KEY = "ripcord.apiKey";

// --- Credential storage ------------------------------------------------------

let memoryKey: string | null = null;
const listeners = new Set<(key: string | null) => void>();

/** Read the stored key. Safe during SSR, where localStorage doesn't exist. */
export function getApiKey(): string | null {
  if (typeof window === "undefined") return memoryKey;
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Private mode / blocked storage: fall back to this tab's memory only.
    return memoryKey;
  }
}

export function setApiKey(key: string | null): void {
  memoryKey = key;
  try {
    if (key) window.localStorage.setItem(STORAGE_KEY, key);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable — the in-memory copy still works for this tab */
  }
  listeners.forEach((fn) => fn(key));
}

/** Subscribe to key changes so the UI can re-render when it's set or cleared. */
export function onApiKeyChange(fn: (key: string | null) => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// --- Errors ------------------------------------------------------------------

// Error that carries the HTTP status, so callers can branch on 401 / 404 / 409.
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** True when the failure means "your credential is missing, wrong or revoked". */
export function isAuthError(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 401 || e.status === 403);
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(body.detail ?? `Request failed (${res.status})`, res.status);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

function headers(withBody = false): HeadersInit {
  const key = getApiKey();
  return {
    ...(withBody ? { "Content-Type": "application/json" } : {}),
    ...(key ? { Authorization: `Bearer ${key}` } : {}),
  };
}

function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  return fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...headers(init.body !== undefined), ...(init.headers ?? {}) },
  }).then((r) => handle<T>(r));
}

// --- API ---------------------------------------------------------------------

export const api = {
  // EventSource cannot set request headers, so the SSE stream takes the key as
  // a query parameter. The backend allows that on /stream only.
  streamUrl: () => {
    const key = getApiKey();
    return key
      ? `${API_URL}/stream?api_key=${encodeURIComponent(key)}`
      : `${API_URL}/stream`;
  },

  listFlags: () => request<Flag[]>("/flags"),

  getFlag: (key: string) => request<Flag>(`/flags/${key}`),

  createFlag: (body: {
    key: string;
    name: string;
    enabled: boolean;
    rollout_percentage: number;
    variants?: Omit<Variant, "id">[];
    off_variant?: string | null;
  }) => request<Flag>("/flags", { method: "POST", body: JSON.stringify(body) }),

  updateFlag: (key: string, body: FlagUpdate) =>
    request<Flag>(`/flags/${key}`, { method: "PATCH", body: JSON.stringify(body) }),

  deleteFlag: (key: string) =>
    request<void>(`/flags/${key}`, { method: "DELETE" }),

  evaluate: (flagKey: string, userId: string, context: Record<string, string>) =>
    request<Evaluation>("/evaluate", {
      method: "POST",
      body: JSON.stringify({ flag_key: flagKey, user_id: userId, context }),
    }),

  listAudit: (flagKey?: string) =>
    request<AuditEntry[]>(
      `/audit${flagKey ? `?flag_key=${encodeURIComponent(flagKey)}` : ""}`,
    ),

  getStats: () => request<Stats>("/stats"),

  listKeys: () => request<ApiKey[]>("/keys"),

  createKey: (body: { name: string; scopes: string[] }) =>
    request<ApiKey & { key: string }>("/keys", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  revokeKey: (keyId: string) =>
    request<ApiKey>(`/keys/${keyId}`, { method: "DELETE" }),
};

// Thin typed client for the Ripcord FastAPI backend. The dashboard is a pure
// frontend — it talks to the backend over REST + SSE, never via Next API routes.

export type Rule = {
  id?: number;
  attribute: string;
  operator: string;
  values: string[];
  priority?: number;
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
  rules: Rule[];
};

export type Evaluation = {
  flag_key: string;
  user_id: string;
  enabled: boolean;
  reason: string;
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

export type FlagUpdate = {
  version: number;
  name?: string;
  enabled?: boolean;
  rollout_percentage?: number;
  rules?: Omit<Rule, "id">[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const json = { "Content-Type": "application/json" };

// Error that carries the HTTP status, so callers can branch on 404 / 409.
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new ApiError(body.detail ?? `Request failed (${res.status})`, res.status);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

export const api = {
  streamUrl: () => `${API_URL}/stream`,

  listFlags: () => fetch(`${API_URL}/flags`).then((r) => handle<Flag[]>(r)),

  getFlag: (key: string) =>
    fetch(`${API_URL}/flags/${key}`).then((r) => handle<Flag>(r)),

  createFlag: (body: {
    key: string;
    name: string;
    enabled: boolean;
    rollout_percentage: number;
  }) =>
    fetch(`${API_URL}/flags`, {
      method: "POST",
      headers: json,
      body: JSON.stringify(body),
    }).then((r) => handle<Flag>(r)),

  updateFlag: (key: string, body: FlagUpdate) =>
    fetch(`${API_URL}/flags/${key}`, {
      method: "PATCH",
      headers: json,
      body: JSON.stringify(body),
    }).then((r) => handle<Flag>(r)),

  deleteFlag: (key: string) =>
    fetch(`${API_URL}/flags/${key}`, { method: "DELETE" }).then((r) =>
      handle<void>(r),
    ),

  evaluate: (flagKey: string, userId: string, context: Record<string, string>) =>
    fetch(`${API_URL}/evaluate`, {
      method: "POST",
      headers: json,
      body: JSON.stringify({ flag_key: flagKey, user_id: userId, context }),
    }).then((r) => handle<Evaluation>(r)),

  listAudit: (flagKey?: string) => {
    const qs = flagKey ? `?flag_key=${encodeURIComponent(flagKey)}` : "";
    return fetch(`${API_URL}/audit${qs}`).then((r) => handle<AuditEntry[]>(r));
  },

  getStats: () => fetch(`${API_URL}/stats`).then((r) => handle<Stats>(r)),
};

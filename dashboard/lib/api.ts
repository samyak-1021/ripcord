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

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = (await res.json().catch(() => ({}))) as { detail?: string };
    throw new Error(body.detail ?? `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

const json = { "Content-Type": "application/json" };

export const api = {
  streamUrl: () => `${API_URL}/stream`,

  listFlags: () => fetch(`${API_URL}/flags`).then((r) => handle<Flag[]>(r)),

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

  updateFlag: (
    key: string,
    body: { version: number; enabled?: boolean; rollout_percentage?: number },
  ) =>
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
};

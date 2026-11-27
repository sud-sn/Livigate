/** Fetch wrapper: same-origin cookie session, CSRF header on writes, server error text surfaced as-is. */

export class ApiError extends Error {
  status: number;
  fields?: string[];
  constructor(message: string, status: number, fields?: string[]) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

let csrfToken = "";
export const setCsrfToken = (token: string) => {
  csrfToken = token;
};

export const SIGNED_OUT_EVENT = "lv:signed-out";

type Options = { method?: "GET" | "POST" | "PATCH"; body?: unknown; signal?: AbortSignal };

async function request(path: string, { method = "GET", body, signal }: Options = {}): Promise<Response> {
  const headers: Record<string, string> = { accept: "application/json" };
  if (body !== undefined) headers["content-type"] = "application/json";
  if (method !== "GET") headers["x-csrf-token"] = csrfToken;
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if ((err as Error).name === "AbortError") throw err;
    throw new ApiError("Can't reach Levigate. Check your connection; nothing you typed was lost.", 0);
  }
  if (res.ok) return res;
  let message = "Something went wrong. Try again.";
  let fields: string[] | undefined;
  try {
    const data = await res.json();
    message = data.error ?? message;
    fields = data.fields;
  } catch {
    /* non-JSON error body */
  }
  if (res.status === 401 && !path.startsWith("/api/auth/demo")) {
    window.dispatchEvent(new CustomEvent(SIGNED_OUT_EVENT, { detail: message }));
  }
  throw new ApiError(message, res.status, fields);
}

export async function api<T>(path: string, opts?: Options): Promise<T> {
  const res = await request(path, opts);
  return (await res.json()) as T;
}

/** Download a file response (CSV export, audit pack) without exposing it in a URL. */
export async function download(path: string, body: unknown, fallbackName: string): Promise<string> {
  const res = await request(path, { method: "POST", body });
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  return name;
}

export const newIdempotencyKey = () =>
  (crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, "");

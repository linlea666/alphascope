/** Browser-side calls to the same-origin proxy (/api/bff/...), which adds the session. */
export async function bff<T = unknown>(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api/bff/${path}`, {
    method,
    headers: { "content-type": "application/json", "x-newsroom": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (data as { detail?: unknown }).detail;
    const msg = typeof d === "string" ? d : d && typeof d === "object" && "message" in d ? String((d as { message: string }).message) : `HTTP ${res.status}`;
    throw Object.assign(new Error(msg), { status: res.status, detail: d });
  }
  return data as T;
}

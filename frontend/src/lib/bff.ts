/** Browser-side calls to the same-origin proxy (/api/bff/...). */
export async function bff<T = unknown>(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown): Promise<T> {
  const res = await fetch(`/api/bff/${path}`, {
    method,
    headers: { "content-type": "application/json", "x-alphascope": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error((data as { detail?: string }).detail ?? `HTTP ${res.status}`);
  return data as T;
}

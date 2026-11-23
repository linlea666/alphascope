/** Browser-side calls to the same-origin proxy (/api/bff/...), which adds the session. Every write announces its
 * outcome through the toaster (components/Toaster): a short confirmation, or the API's error message. */

export type ToastDetail = { ok: boolean; message?: string };
type Opts = { toast?: string | false };

export function toast(detail: ToastDetail) {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent<ToastDetail>("nr-toast", { detail }));
}

export async function bff<T = unknown>(path: string, method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" = "GET", body?: unknown,
  opts: Opts = {}): Promise<T> {
  const write = method !== "GET" && opts.toast !== false;
  let res: Response;
  try {
    res = await fetch(`/api/bff/${path}`, {
      method,
      headers: { "content-type": "application/json", "x-newsroom": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (e) {
    if (write) toast({ ok: false, message: (e as Error).message });
    throw e;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = (data as { detail?: unknown }).detail;
    const msg = typeof d === "string" ? d : d && typeof d === "object" && "message" in d ? String((d as { message: string }).message)
      : Array.isArray(d) ? d.map((x) => (x && typeof x === "object" && "msg" in x ? String(x.msg) : String(x))).join("; ") : `HTTP ${res.status}`;
    if (write) toast({ ok: false, message: msg });
    throw Object.assign(new Error(msg), { status: res.status, detail: d });
  }
  if (write) toast({ ok: true, message: opts.toast || undefined });
  return data as T;
}

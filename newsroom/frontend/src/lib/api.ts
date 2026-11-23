import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { API_BASE, SESSION_COOKIE } from "./session";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

/** Server-side read from the newsroom API with the signed-in user's session. */
export async function nrGet<T>(path: string, params?: Record<string, string | number | boolean | undefined | null>): Promise<T> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) redirect("/login");
  const url = new URL(`/api/${path}`, API_BASE);
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, String(v));
  }
  let res: Response;
  try {
    res = await fetch(url, { cache: "no-store", headers: { Authorization: `Bearer ${token}` } });
  } catch {
    throw new ApiError(0, `cannot reach the newsroom API at ${API_BASE}`);
  }
  if (res.status === 401) redirect("/login");
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.json() as Promise<T>;
}

/** Like nrGet, but a 403 becomes null so a page can show "no access" instead of failing. */
export async function nrGetOptional<T>(path: string, params?: Record<string, string | number | boolean | undefined | null>): Promise<T | null> {
  try {
    return await nrGet<T>(path, params);
  } catch (e) {
    if (e instanceof ApiError && (e.status === 403 || e.status === 404)) return null;
    throw e;
  }
}

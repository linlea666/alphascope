import { NextResponse, type NextRequest } from "next/server";
import { API_BASE, SESSION_COOKIE } from "@/lib/session";

export async function POST(req: NextRequest) {
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (token) {
    await fetch(new URL("/api/auth/logout", API_BASE), {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      cache: "no-store",
    }).catch(() => undefined);
  }
  const out = NextResponse.json({ ok: true });
  out.cookies.delete(SESSION_COOKIE);
  return out;
}

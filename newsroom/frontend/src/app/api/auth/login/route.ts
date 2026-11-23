import { NextResponse, type NextRequest } from "next/server";
import { API_BASE, SESSION_COOKIE } from "@/lib/session";

export async function POST(req: NextRequest) {
  const origin = req.headers.get("origin");
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
  if (!origin || !host || new URL(origin).host !== host)
    return NextResponse.json({ detail: "cross-site request refused" }, { status: 403 });
  const res = await fetch(new URL("/api/auth/login", API_BASE), {
    method: "POST",
    headers: { "content-type": "application/json", "user-agent": req.headers.get("user-agent") ?? "" },
    body: await req.text(),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) return NextResponse.json(data, { status: res.status });
  const out = NextResponse.json({ must_change_password: data.must_change_password });
  out.cookies.set(SESSION_COOKIE, data.token, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.NEWSROOM_COOKIE_SECURE === "true",
    path: "/",
    expires: new Date(data.expires_at),
  });
  return out;
}

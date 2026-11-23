import { NextResponse, type NextRequest } from "next/server";
import { API_BASE, SESSION_COOKIE } from "@/lib/session";

/** Same-origin proxy from the browser to the newsroom API (which only listens on localhost). It attaches the session
 *  from the httpOnly cookie; the API decides every permission. Writes must come from this site (Origin matches Host)
 *  and carry a custom header that a cross-site form cannot send. Binary responses (card images) pass through. */
async function forward(req: NextRequest, path: string[]): Promise<Response> {
  if (path.some((seg) => seg === "." || seg === ".." || seg.includes("/") || seg.includes("\\")))
    return NextResponse.json({ detail: "bad path" }, { status: 400 });
  if (req.method !== "GET") {
    const origin = req.headers.get("origin");
    const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
    if (!origin || !host || new URL(origin).host !== host || req.headers.get("x-newsroom") !== "1")
      return NextResponse.json({ detail: "cross-site request refused" }, { status: 403 });
  }
  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token) return NextResponse.json({ detail: "not signed in" }, { status: 401 });
  const url = new URL(`/api/${path.map(encodeURIComponent).join("/")}`, API_BASE);
  req.nextUrl.searchParams.forEach((v, k) => url.searchParams.set(k, v));
  const res = await fetch(url, {
    method: req.method,
    headers: { "content-type": "application/json", Authorization: `Bearer ${token}` },
    body: req.method === "GET" || req.method === "DELETE" ? undefined : await req.text(),
    cache: "no-store",
  });
  const headers = new Headers({ "content-type": res.headers.get("content-type") ?? "application/json" });
  const disposition = res.headers.get("content-disposition");
  if (disposition) headers.set("content-disposition", disposition);
  return new NextResponse(res.body, { status: res.status, headers });
}

type Ctx = { params: Promise<{ path: string[] }> };
export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PUT(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
export async function PATCH(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}

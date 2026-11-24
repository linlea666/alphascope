import { NextResponse, type NextRequest } from "next/server";
import { API_BASE } from "@/lib/session";

/** Public entry for RPA and browser extensions (docs/15-publish-api.md). The API itself only listens on localhost;
 *  this passes the caller's own Bearer token through and never adds a session, so a token is the only way in.
 *  CORS is open because an extension calls from its own origin; no cookies are read or sent. */
const CORS = { "access-control-allow-origin": "*", "access-control-allow-headers": "authorization, content-type",
  "access-control-allow-methods": "GET, POST, OPTIONS" };

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  if (path.some((seg) => seg === "." || seg === ".." || seg.includes("/") || seg.includes("\\")))
    return NextResponse.json({ detail: "bad path" }, { status: 400, headers: CORS });
  const auth = req.headers.get("authorization");
  if (!auth?.startsWith("Bearer nrt_")) return NextResponse.json({ detail: "missing token" }, { status: 401, headers: CORS });
  const url = new URL(`/api/feed/${path.map(encodeURIComponent).join("/")}`, API_BASE);
  req.nextUrl.searchParams.forEach((v, k) => url.searchParams.set(k, v));
  const res = await fetch(url, {
    method: req.method,
    headers: { "content-type": "application/json", Authorization: auth },
    body: req.method === "GET" ? undefined : await req.text(),
    cache: "no-store",
  });
  const headers = new Headers({ ...CORS, "content-type": res.headers.get("content-type") ?? "application/json" });
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
export function OPTIONS() {
  return new NextResponse(null, { status: 204, headers: CORS });
}

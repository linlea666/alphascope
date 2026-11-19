import { NextResponse, type NextRequest } from "next/server";

/** Same-origin proxy from the browser to the API (which only listens on localhost).
 *  Writes require the request to come from this site (Origin matches Host) and carry a custom header, which a
 *  cross-site form or fetch cannot send without a CORS preflight that this route never approves. */
const BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
const ALLOWED = [/^research\/experiments\/\d+\/reviews$/, /^research\/(events|reports)$/, /^research\/notices\/\d+\/watch$/, /^watch(\/|$)/, /^alerts(\/|$)/, /^sims(\/|$)/, /^settings(\/|$)/];
// read-only data the browser may refresh in place (e.g. re-sorting a table without reloading the page)
const READ_ONLY = [/^market(\/|$)/];

async function forward(req: NextRequest, path: string[]): Promise<Response> {
  if (path.some((seg) => seg === "." || seg === ".." || seg.includes("/") || seg.includes("\\")))
    return NextResponse.json({ detail: "bad path" }, { status: 400 });
  const sub = path.map(encodeURIComponent).join("/");
  const readOnly = READ_ONLY.some((re) => re.test(sub));
  if (!ALLOWED.some((re) => re.test(sub)) && !(readOnly && req.method === "GET"))
    return NextResponse.json({ detail: "not allowed" }, { status: 404 });
  if (req.method !== "GET") {
    const origin = req.headers.get("origin");
    const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host");
    if (!origin || !host || new URL(origin).host !== host || req.headers.get("x-alphascope") !== "1") {
      return NextResponse.json({ detail: "cross-site request refused" }, { status: 403 });
    }
  }
  const url = new URL(`/api/v1/${sub}`, BASE);
  req.nextUrl.searchParams.forEach((v, k) => url.searchParams.set(k, v));
  const res = await fetch(url, {
    method: req.method,
    headers: { "content-type": "application/json" },
    body: req.method === "GET" || req.method === "DELETE" ? undefined : await req.text(),
    cache: "no-store",
  });
  return new NextResponse(await res.text(), { status: res.status, headers: { "content-type": "application/json" } });
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
export async function DELETE(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}

import createMiddleware from "next-intl/middleware";
import { NextResponse, type NextRequest } from "next/server";
import { routing } from "./i18n/routing";

const intl = createMiddleware(routing);

/** Compares every character so the response time does not reveal how much of the password matched. */
function same(a: string, b: string) {
  let diff = a.length ^ b.length;
  for (let i = 0; i < b.length; i++) diff |= (a.charCodeAt(i) || 0) ^ b.charCodeAt(i);
  return diff === 0;
}

// Former pages now shown as views of a workspace; bookmarks and internal links keep working.
const MOVED: Record<string, [string, string]> = {
  discover: ["traders", "discover"], rankings: ["traders", "rankings"], timing: ["traders", "timing"], okx: ["traders", "okx"],
  legs: ["cycles", "legs"], zones: ["cycles", "zones"], holders: ["cycles", "holders"], swings: ["cycles", "swings"],
};
const MOVED_PATH = new RegExp(`^/(?:(${routing.locales.join("|")})/)?(${Object.keys(MOVED).join("|")})/?$`);

/** Optional HTTP basic auth: set ALPHASCOPE_WEB_AUTH="user:password" (the server installer does).
 *  It also guards the same-origin proxy under /api/bff, which is the only way the browser can write. */
export default function middleware(req: NextRequest) {
  const auth = process.env.ALPHASCOPE_WEB_AUTH;
  if (auth && !same(req.headers.get("authorization") ?? "", `Basic ${btoa(auth)}`)) {
    return new NextResponse("Authentication required", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="AlphaScope", charset="UTF-8"' },
    });
  }
  if (req.nextUrl.pathname.startsWith("/api/")) return NextResponse.next();
  const moved = req.nextUrl.pathname.match(MOVED_PATH);
  if (moved) {
    const [workspace, view] = MOVED[moved[2]];
    const url = req.nextUrl.clone();
    url.pathname = `/${moved[1] ?? routing.defaultLocale}/${workspace}`;
    url.searchParams.set("view", view);
    return NextResponse.redirect(url);
  }
  return intl(req);
}

export const config = { matcher: ["/((?!_next|_vercel|.*\\..*).*)"] };

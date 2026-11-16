import createMiddleware from "next-intl/middleware";
import { NextResponse, type NextRequest } from "next/server";
import { routing } from "./i18n/routing";

const intl = createMiddleware(routing);

/** Optional HTTP basic auth: set ALPHASCOPE_WEB_AUTH="user:password" (the server installer does).
 *  It also guards the same-origin proxy under /api/bff, which is the only way the browser can write. */
export default function middleware(req: NextRequest) {
  const auth = process.env.ALPHASCOPE_WEB_AUTH;
  if (auth && req.headers.get("authorization") !== `Basic ${btoa(auth)}`) {
    return new NextResponse("Authentication required", {
      status: 401,
      headers: { "WWW-Authenticate": 'Basic realm="AlphaScope", charset="UTF-8"' },
    });
  }
  if (req.nextUrl.pathname.startsWith("/api/")) return NextResponse.next();
  return intl(req);
}

export const config = { matcher: ["/((?!_next|_vercel|.*\\..*).*)"] };

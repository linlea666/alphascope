import createMiddleware from "next-intl/middleware";
import { NextResponse, type NextRequest } from "next/server";
import { routing } from "./i18n/routing";
import { SESSION_COOKIE } from "./lib/session";

const intl = createMiddleware(routing);
const LOGIN = new RegExp(`^/(?:(${routing.locales.join("|")})/)?login/?$`);

/** Sends visitors without a session to the login page. This is navigation only: the API checks every request. */
export default function middleware(req: NextRequest) {
  const path = req.nextUrl.pathname;
  if (path.startsWith("/api/")) return NextResponse.next();
  if (!req.cookies.get(SESSION_COOKIE) && !LOGIN.test(path)) {
    const url = req.nextUrl.clone();
    const locale = routing.locales.find((l) => path === `/${l}` || path.startsWith(`/${l}/`)) ?? routing.defaultLocale;
    url.pathname = `/${locale}/login`;
    url.search = "";
    return NextResponse.redirect(url);
  }
  return intl(req);
}

export const config = { matcher: ["/((?!_next|_vercel|.*\\..*).*)"] };

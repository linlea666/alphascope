"use client";

import { useLocale, useTranslations } from "next-intl";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { input, primary } from "./ui";

export function LoginForm() {
  const t = useTranslations("login");
  const locale = useLocale();
  const [error, setError] = useState<string | null>(null);
  const [mustChange, setMustChange] = useState(false);
  const [pending, setPending] = useState(false);

  async function login(form: FormData) {
    setPending(true);
    setError(null);
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ username: form.get("username"), password: form.get("password") }),
    });
    setPending(false);
    if (!res.ok) return setError(res.status === 423 ? t("locked") : t("failed"));
    const data = await res.json();
    if (data.must_change_password) setMustChange(true);
    else window.location.href = `/${locale}`;
  }

  async function change(form: FormData) {
    setPending(true);
    setError(null);
    try {
      await bff("auth/password", "POST", { current: form.get("current"), new: form.get("new") });
      window.location.href = `/${locale}`;
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  if (mustChange)
    return (
      <form action={change} className="space-y-3">
        <p className="text-sm text-warn">{t("changeTitle")}</p>
        <input name="current" type="password" placeholder={t("current")} className={input} required />
        <input name="new" type="password" placeholder={t("new")} className={input} minLength={10} required />
        {error && <p className="text-sm text-down">{error}</p>}
        <button className={primary} disabled={pending}>{t("change")}</button>
      </form>
    );
  return (
    <form action={login} className="space-y-3">
      <input name="username" placeholder={t("username")} className={input} autoComplete="username" required />
      <input name="password" type="password" placeholder={t("password")} className={input} autoComplete="current-password" required />
      {error && <p className="text-sm text-down">{error}</p>}
      <button className={primary} disabled={pending}>{t("signIn")}</button>
    </form>
  );
}

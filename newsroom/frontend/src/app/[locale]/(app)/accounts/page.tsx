import { getLocale, getTranslations } from "next-intl/server";
import { StateBadge } from "@/components/badges";
import { FieldForm, type Field } from "@/components/FieldForm";
import { Badge, PageHeader, Panel } from "@/components/ui";
import { nrGet, nrGetOptional } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { platformName } from "@/lib/glossary";
import type { Account } from "@/lib/types";

const CLASSES = ["whale_open", "flip", "whale_add", "underwater_add", "whale_close", "liquidation", "near_liquidation", "notable_trader_move"];

export default async function AccountsPage() {
  const tcl = await getTranslations("classes");
  const [t, tc, tw, locale, accounts, styles] = await Promise.all([getTranslations("accounts"), getTranslations("common"),
    getTranslations("statusWords"), getLocale(), nrGet<Account[]>("accounts"), nrGet<{ key: string; name: string }[]>("styles")]);
  const styleKeys = [...new Set(styles.map((s) => s.key))];
  const styleOpts = styleKeys.map((k) => ({ value: k, label: `${styles.find((s) => s.key === k)?.name ?? k}（${k.startsWith("zh_") ? "中文" : "English"}）` }));
  const styleName = (k: string | null) => (k ? styles.find((s) => s.key === k)?.name ?? k : "");
  const fields = (a?: Account): Field[] => [
    { name: "handle", label: t("handle"), value: a?.handle, required: true },
    { name: "display_name", label: t("name"), value: a?.display_name, required: true },
    { name: "language", label: tc("language"), type: "select", options: [{ value: "zh", label: "中文" }, { value: "en", label: "English" }], value: a?.language ?? "zh" },
    { name: "platform", label: t("platform"), type: "select", options: [{ value: "x", label: "X" }, { value: "okx_square", label: "OKX 广场 / OKX Square" },
      { value: "binance_square", label: "币安广场 / Binance Square" }], value: a?.platform ?? "x" },
    { name: "venues", label: t("venues"), type: "list", value: a?.venues ?? ["hyperliquid"], placeholder: "hyperliquid, okx, binance" },
    { name: "name_display", label: t("nameDisplay"), type: "select", value: a?.name_display ?? "nickname", help: t("nameDisplayHelp"),
      options: [{ value: "nickname", label: t("nameFull") }, { value: "masked", label: t("nameMasked") }] },
    { name: "status", label: tc("status"), type: "select", options: ["active", "paused", "retired"].map((v) => ({ value: v, label: tw(v) })),
      value: a?.status ?? "paused", help: t("statusHelp") },
    { name: "brand", label: t("brand"), value: a?.brand ?? "AlphaScope" },
    { name: "timezone", label: t("timezone"), value: a?.timezone ?? "UTC", required: true },
    { name: "style_key", label: t("style"), type: "select", options: styleOpts, value: a?.style_key ?? styleKeys[0], help: t("styleHelp") },
    { name: "min_score", label: t("minScore"), type: "number", value: a?.min_score ?? 0 },
    { name: "daily_post_budget", label: t("budget"), type: "number", value: a?.daily_post_budget ?? 5 },
    { name: "min_gap_minutes", label: t("gap"), type: "number", value: a?.min_gap_minutes ?? 60 },
    { name: "allowed_event_classes", label: t("classes"), type: "multi", value: a?.allowed_event_classes ?? [], wide: true,
      options: CLASSES.map((c) => ({ value: c, label: tcl(c) })), help: t("classesHelp") },
    { name: "long_post_allowed", label: t("longPosts"), type: "checkbox", value: a?.long_post_allowed },
    { name: "link_in_thread", label: t("linkInThread"), type: "checkbox", value: a?.link_in_thread ?? true, help: t("linkInThreadHelp") },
    { name: "reply_explorer_link", label: t("replyLink"), type: "checkbox", value: a?.reply_explorer_link ?? false, help: t("replyLinkHelp") },
    { name: "rpa_enabled", label: "RPA", type: "checkbox", value: a?.rpa_enabled },
    { name: "authorization_status", label: t("auth"), type: "select", options: ["pending", "authorized", "expired", "revoked"], value: a?.authorization_status ?? "pending" },
    { name: "authorization_expires_at", label: t("authExpires"), value: a?.authorization_expires_at, placeholder: "2027-06-30T00:00:00Z" },
    { name: "authorization_note", label: t("authNote"), value: a?.authorization_note },
    { name: "positioning", label: t("positioning"), type: "textarea", value: a?.positioning },
    { name: "audience_profile", label: t("audience"), type: "json", value: a?.audience_profile ?? {} },
  ];
  const perms = await Promise.all(accounts.map((a) => nrGetOptional<{ user_id: number; username: string; permissions: string[] }[]>(`accounts/${a.id}/permissions`)));
  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />
      <div className="space-y-5">
        {accounts.map((a, i) => (
          <Panel key={a.id} title={<span>@{a.handle} · {a.display_name} <Badge tone="accent">{platformName(a.platform)}</Badge> <Badge>{(a.venues ?? []).join(", ") || "all"}</Badge> <StateBadge state={a.status} /> <Badge tone={a.authorization_status === "authorized" ? "up" : ["revoked", "expired"].includes(a.authorization_status) ? "down" : "neutral"}>{t("authShort")}：{tw(a.authorization_status)}</Badge></span>}
            hint={`${a.language === "zh" ? "中文" : "English"} · ${a.timezone} · ${t("style")}：${styleName(a.style_key)}`
              + (a.authorization_expires_at ? ` · ${t("authUntil")} ${fmtTime(a.authorization_expires_at, locale, a.timezone)}` : "")}>
            <p className="mb-3 text-sm">{a.positioning}</p>
            <details><summary className="cursor-pointer text-sm text-accent">{t("edit")}</summary>
              <div className="mt-3"><FieldForm fields={fields(a)} path={`accounts/${a.id}`} method="PUT" /></div>
            </details>
            {perms[i] && (
              <details className="mt-3"><summary className="cursor-pointer text-sm text-accent">{t("permissions")}</summary>
                <div className="mt-2 space-y-1 text-xs">
                  {perms[i]!.map((p) => <div key={p.user_id}>{p.username} (#{p.user_id}): {p.permissions.join(", ")}</div>)}
                </div>
                <div className="mt-3">
                  <FieldForm path={`accounts/${a.id}/permissions`} method="PUT" fields={[
                    { name: "user_id", label: t("userId"), type: "number", required: true },
                    { name: "permissions", label: "view, draft, review, publish, manage", type: "list", placeholder: "view, draft" },
                  ]} />
                </div>
              </details>
            )}
          </Panel>
        ))}
        <Panel title={t("new")}><FieldForm fields={fields()} path="accounts" /></Panel>
      </div>
    </>
  );
}

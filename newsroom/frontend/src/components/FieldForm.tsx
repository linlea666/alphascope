"use client";

import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { bff } from "@/lib/bff";
import { input, primary } from "./ui";

export type Field = {
  name: string;
  label: string;
  type?: "text" | "number" | "checkbox" | "select" | "textarea" | "json" | "list" | "password" | "numbers";
  options?: (string | { value: string; label: string })[];
  value?: unknown;
  required?: boolean;
  placeholder?: string;
  wide?: boolean;
  numeric?: boolean;
};

/** A small generic form: collects typed values, sends them through the proxy, shows the result or the error. */
export function FieldForm({ fields, path, method = "POST", submitLabel, showResult = false }: {
  fields: Field[];
  path: string;
  method?: "POST" | "PUT" | "PATCH";
  submitLabel?: string;
  showResult?: boolean;
}) {
  const tc = useTranslations("common");
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  return (
    <form
      className="grid gap-3 md:grid-cols-2"
      action={async (f) => {
        setError(null);
        setResult(null);
        const body: Record<string, unknown> = {};
        try {
          for (const fd of fields) {
            const raw = f.get(fd.name);
            const s = raw === null ? "" : String(raw);
            if (fd.type === "checkbox") body[fd.name] = raw === "on";
            else if (fd.type === "number") body[fd.name] = s === "" ? null : Number(s);
            else if (fd.type === "select" && fd.numeric) body[fd.name] = s === "" ? null : Number(s);
            else if (fd.type === "json") body[fd.name] = s.trim() ? JSON.parse(s) : {};
            else if (fd.type === "list") body[fd.name] = s.split(",").map((x) => x.trim()).filter(Boolean);
            else if (fd.type === "numbers") body[fd.name] = s.split(",").map((x) => x.trim()).filter(Boolean).map(Number);
            else body[fd.name] = s === "" && !fd.required ? null : s;
          }
        } catch {
          return setError("invalid JSON");
        }
        setBusy(true);
        try {
          const out = await bff(path, method, body);
          if (showResult) setResult(JSON.stringify(out, null, 2));
          router.refresh();
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      {fields.map((fd) => (
        <label key={fd.name} className={`block text-xs text-muted ${fd.wide || fd.type === "textarea" || fd.type === "json" ? "md:col-span-2" : ""}`}>
          {fd.label}
          {fd.type === "checkbox" ? (
            <input type="checkbox" name={fd.name} defaultChecked={Boolean(fd.value)} className="ml-2" />
          ) : fd.type === "select" ? (
            <select name={fd.name} defaultValue={String(fd.value ?? "")} className={input}>
              {fd.options!.map((o) => typeof o === "string"
                ? <option key={o} value={o}>{o}</option>
                : <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          ) : fd.type === "textarea" || fd.type === "json" ? (
            <textarea name={fd.name} className={`${input} ${fd.type === "json" ? "h-28 font-mono text-xs" : "h-40"}`} placeholder={fd.placeholder}
              defaultValue={fd.type === "json" ? JSON.stringify(fd.value ?? {}, null, 2) : String(fd.value ?? "")} required={fd.required} />
          ) : (
            <input name={fd.name} type={fd.type === "number" ? "number" : fd.type === "password" ? "password" : "text"} step="any"
              className={input} placeholder={fd.placeholder} required={fd.required}
              defaultValue={Array.isArray(fd.value) ? fd.value.join(", ") : fd.value === null || fd.value === undefined ? "" : String(fd.value)} />
          )}
        </label>
      ))}
      <div className="md:col-span-2">
        <button className={primary} disabled={busy}>{busy ? tc("loading") : submitLabel ?? tc("save")}</button>
        {error && <span className="ml-3 text-xs text-down">{error}</span>}
        {result && <pre className="mt-2 max-h-60 overflow-auto rounded bg-panel-2 p-2 text-xs">{result}</pre>}
      </div>
    </form>
  );
}

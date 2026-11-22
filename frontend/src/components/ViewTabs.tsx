import { Link } from "@/i18n/routing";

/** Tabs for a workspace page that hosts several former pages; `view` selects one and keeps the rest of the query. */
export function ViewTabs({ pathname, views, active }: {
  pathname: "/traders" | "/cycles";
  views: { key: string; label: string }[];
  active: string;
}) {
  return (
    <nav className="flex flex-wrap gap-2 border-b border-border pb-3">
      {views.map((v) => (
        <Link
          key={v.key}
          scroll={false}
          href={{ pathname, query: { view: v.key } }}
          className={`rounded border px-3 py-1 text-sm ${v.key === active ? "border-accent bg-accent text-white" : "border-border text-muted hover:text-text"}`}
        >
          {v.label}
        </Link>
      ))}
    </nav>
  );
}

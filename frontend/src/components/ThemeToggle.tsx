"use client";

export function ThemeToggle({ label }: { label: string }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={() => {
        const root = document.documentElement;
        const next = root.dataset.theme === "light" ? "dark" : "light";
        root.dataset.theme = next;
        try {
          localStorage.setItem("theme", next);
        } catch {
          /* storage may be unavailable */
        }
      }}
      className="rounded border border-border px-2 py-1 text-xs text-muted hover:text-text"
    >
      ◐
    </button>
  );
}

/** Inline script that applies the saved theme before first paint. */
export const themeInitScript = `try{var t=localStorage.getItem("theme");if(t)document.documentElement.dataset.theme=t}catch(e){}`;

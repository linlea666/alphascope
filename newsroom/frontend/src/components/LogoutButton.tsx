"use client";

export function LogoutButton({ label }: { label: string }) {
  return (
    <button
      className="hover:text-text"
      onClick={async () => {
        await fetch("/api/auth/logout", { method: "POST" });
        window.location.href = "/";
      }}
    >
      {label}
    </button>
  );
}

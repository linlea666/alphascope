import type { ReactNode } from "react";
import { Nav } from "@/components/Nav";

export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <>
      <Nav />
      <main className="mx-auto max-w-[1500px] px-4 py-6">{children}</main>
    </>
  );
}

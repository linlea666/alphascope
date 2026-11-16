import { Link } from "@/i18n/routing";
import { shortAddr } from "@/lib/format";

export function AddressLink({ address, name }: { address: string; name?: string | null }) {
  return (
    <Link href={`/trader/${address}`} className="group inline-flex flex-col leading-tight">
      <span className="num text-accent group-hover:underline">{shortAddr(address)}</span>
      {name && <span className="max-w-[180px] truncate text-[11px] text-muted">{name}</span>}
    </Link>
  );
}

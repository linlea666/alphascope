import { Link } from "@/i18n/routing";
import { isOkx, shortAddr } from "@/lib/format";
import { getWatchContext } from "@/lib/watch";
import { WatchButton } from "./WatchButton";

/** Trader link used in every table: the owner's alias (if any) leads, then the short address, plus a star.
 *  OKX traders have a public nickname, so it leads when there is no alias. */
export async function AddressLink({ address, name, star = true }: { address: string; name?: string | null; star?: boolean }) {
  const { aliases, watched } = await getWatchContext();
  const alias = aliases[address] ?? (isOkx(address) ? name : null);
  return (
    <span className="inline-flex items-start gap-1.5">
      {star && <WatchButton address={address} watched={watched.has(address)} compact />}
      <Link href={`/trader/${address}`} className="group inline-flex flex-col leading-tight">
        {alias ? (
          <>
            <span className="text-accent group-hover:underline">{alias}</span>
            <span className="num text-[11px] text-muted">{shortAddr(address)}</span>
          </>
        ) : (
          <>
            <span className="num text-accent group-hover:underline">{shortAddr(address)}</span>
            {name && <span className="max-w-[180px] truncate text-[11px] text-muted">{name}</span>}
          </>
        )}
      </Link>
    </span>
  );
}

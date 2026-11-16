import { cache } from "react";
import { api } from "./api";

/** Aliases and watched addresses, fetched once per server render and shared by every AddressLink. */
export const getWatchContext = cache(async () => {
  try {
    const [aliases, watch] = await Promise.all([api.watchAliases(), api.watchlist()]);
    return { aliases: aliases.aliases, watched: new Set(watch.items.map((i) => i.address)) };
  } catch {
    return { aliases: {} as Record<string, string>, watched: new Set<string>() };
  }
});

import { cache } from "react";
import { api } from "./api";

/** Aliases and watched addresses, fetched once per server render and shared by every AddressLink. */
export const getWatchContext = cache(async () => {
  try {
    const [aliases, watch, alerts] = await Promise.all([api.watchAliases(), api.watchlist(), api.alerts()]);
    return {
      aliases: aliases.aliases,
      watched: new Set(watch.items.map((i) => i.address)),
      alerted: new Set(alerts.subscriptions.map((s) => s.address)),
    };
  } catch {
    return { aliases: {} as Record<string, string>, watched: new Set<string>(), alerted: new Set<string>() };
  }
});

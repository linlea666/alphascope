import { Link } from "@/i18n/routing";
import { get } from "@/lib/api";
import { exchange, exchangeLink, formatValue } from "@/lib/exchange";
import { profileLink, profileStatus, type ProfileMarket } from "@/lib/binanceProfiles";
import { utc } from "@/lib/research";
import { PriceLadder } from "./MarketCharts";
import { ExchangeRefresh } from "./ExchangeRefresh";
import { Panel, PageHeader, th, td } from "./ui";

export async function BinanceProfileMarket({
  coin,
  group,
  locale,
}: {
  coin: string;
  group: "traders" | "whales";
  locale: string;
}) {
  const zh = locale === "zh";
  const t = (cn: string, en: string) => (zh ? cn : en);
  const fmt = (v?: number | null) => formatValue(v, zh);
  const [obs, market] = await Promise.all([
    exchange.summary(coin).catch(() => null),
    get<ProfileMarket>("/api/v1/market/binance-profiles", { coin }).catch(() => null),
  ]);
  const roster = obs?.readings.roster;
  const avg = roster?.data.entry?.[group];
  const labels = {
    longs: t("多仓", "Longs"),
    shorts: t("空仓", "Shorts"),
    price: t("标记价", "Mark"),
    zone: t("集中区", "Zone"),
    empty: t("此价格范围暂无可用仓位", "No eligible positions in range"),
  };
  const levels = (["long", "short"] as const).flatMap((side) => {
    const price = avg?.[side];
    return price && price > 0 && !roster?.stale
      ? [{ price, label: side === "long" ? t("多仓均价", "Long entry") : t("空仓均价", "Short entry") }]
      : [];
  });
  const now = market?.structure;
  const button = (active: boolean) =>
    "rounded border px-3 py-2 " + (active ? "border-accent" : "border-border");
  return (
    <div className="space-y-4">
      <ExchangeRefresh />
      <PageHeader
        title={t("币安开仓成本与强平价", "Binance entry and liquidation prices")}
        subtitle={t("BTC、ETH · USDT 本位永续", "BTC and ETH · USDT perpetuals")}
      />
      <nav className="flex flex-wrap items-center gap-2 text-sm">
        {["BTC", "ETH"].map((c) => (
          <Link key={c} href={profileLink(c, group)} className={button(coin === c)}>
            {c}
          </Link>
        ))}
        <Link href={exchangeLink(coin, { cohort: group })} className="text-accent">
          {t("交易所资金行为", "Exchange activity")} →
        </Link>
        <Link href={{ pathname: "/market", query: { coin } }} className="text-accent">
          {t("其他交易所的仓位分布", "Other exchange positions")} →
        </Link>
      </nav>
      <Panel title={t("币安盈利筛选组在哪里开仓？", "Where did the Binance profit cohort enter?")}>
        <div className="mb-3 flex gap-2 text-sm">
          {(["traders", "whales"] as const).map((g) => (
            <Link key={g} className={button(group === g)} href={profileLink(coin, g)}>
              {g === "whales" ? t("其中的巨鲸", "Whale subset") : t("全部入选交易员", "All selected traders")}
            </Link>
          ))}
        </div>
        <p>
          {t("多仓平均开仓价", "Average long entry")} <b>{fmt(avg?.long)}</b> USDT ·{" "}
          {t("空仓平均开仓价", "Average short entry")} <b>{fmt(avg?.short)}</b> USDT
        </p>
        <p className="text-xs text-muted">
          Binance Smart Signal · {utc(roster?.at)} {roster?.stale && t("已过期", "Stale")}
        </p>
      </Panel>
      <Panel
        title={t("已分享主页的仓位样本", "Positions from monitored public profiles")}
        hint={t(
          "统计设置里选定且主动分享持仓的交易员。上方群体切换只影响均价参考线。",
          "Includes configured traders sharing positions. The cohort selector changes only the reference prices.",
        )}
      >
        <p>
          {t("新鲜公开主页", "Fresh public profiles")} {market?.fresh ?? "—"} / {market?.configured ?? "—"} ·{" "}
          {t("本币仓位", "Positions in this asset")} {market?.fresh ? market.positions.length : "—"}
        </p>
        <p className="text-xs text-muted">
          {market
            ? market.enabled
              ? profileStatus(market.status, zh)
              : t("采集未启用", "Collection disabled")
            : t("服务暂不可用", "Service unavailable")}{" "}
          · {t("最近成功", "Last success")} {utc(market?.last_success)} ·{" "}
          <Link href="/settings" className="text-accent">
            {t("采集设置", "Collection settings")}
          </Link>
        </p>
      </Panel>
      {now && market?.ref && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel
            title={t("公开仓位的开仓成本分布", "Entry prices of shared positions")}
            hint={t(
              "柱长表示该价格区间的仓位名义金额。虚线标出参考价格。",
              "Bars show notional exposure per price band. Dashed lines show reference prices.",
            )}
          >
            <PriceLadder
              long={now.entries.long.bins}
              short={now.entries.short.bins}
              longZones={now.entries.long.zones}
              shortZones={now.entries.short.zones}
              refPx={market.ref}
              locale={locale}
              labels={labels}
              levels={levels}
            />
          </Panel>
          <Panel
            title={t("哪些仓位接近强平？", "Where could these positions be liquidated?")}
            hint={t(
              "只计入币安给出有效强平价的仓位。价格触及后仍需核实是否发生清算。",
              "Includes eligible exchange-reported liquidation prices. A price touch still needs confirmation of liquidation.",
            )}
          >
            <p className="text-sm">
              {t("缺少强平价", "Missing liquidation price")}{" "}
              {now.liquidations.long.without_liq.positions + now.liquidations.short.without_liq.positions} ·{" "}
              {t("已越过参考价", "Crossed reference price")}{" "}
              {now.liquidations.long.crossed.positions + now.liquidations.short.crossed.positions}
            </p>
            <PriceLadder
              long={now.liquidations.long.bins}
              short={now.liquidations.short.bins}
              longZones={now.liquidations.long.zones}
              shortZones={now.liquidations.short.zones}
              refPx={market.ref}
              locale={locale}
              labels={labels}
            />
          </Panel>
        </div>
      )}
      {market && market.positions.length > 0 && (
        <Panel title={t("查看每笔仓位", "Inspect each position")}>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  {[
                    t("公开主页", "Profile"),
                    t("方向", "Side"),
                    coin,
                    t("开仓均价", "Entry"),
                    t("标记价", "Mark"),
                    t("强平价", "Liquidation"),
                    t("杠杆", "Leverage"),
                    t("采集时间", "Collected"),
                  ].map((k) => (
                    <th key={k} className={th}>
                      {k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {market.positions.map((p) => (
                  <tr key={p.profile_id + p.side}>
                    <td className={td}>
                      <a
                        className="text-accent"
                        href={`https://www.binance.com/en/smart-money/profile/${p.profile_id}`}
                      >
                        {p.profile_id} ↗
                      </a>
                    </td>
                    <td className={td}>{p.side === "LONG" ? labels.longs : labels.shorts}</td>
                    <td className={td}>{fmt(p.qty)}</td>
                    <td className={td}>{fmt(p.entry)}</td>
                    <td className={td}>{fmt(p.mark)}</td>
                    <td className={td}>{fmt(p.liq)}</td>
                    <td className={td}>{p.lev ? fmt(p.lev) + "×" : "—"}</td>
                    <td className={td}>{utc(p.observed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </div>
  );
}

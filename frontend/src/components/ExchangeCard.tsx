import { profileLink } from "@/lib/binanceProfiles";
import { Link } from "@/i18n/routing";
import { exchangeLink, formatValue, type Observation } from "@/lib/exchange";
import { utc } from "@/lib/research";

function Distribution({
  coin,
  share,
  people,
  zh,
}: {
  coin: string;
  share: number | null;
  people: boolean;
  zh: boolean;
}) {
  if (share === null) return null;
  const long = Math.round(share * 100),
    short = 100 - long;
  return (
    <div className="space-y-1">
      <div className="text-xs text-muted">
        {people
          ? zh
            ? "每 100 名交易员"
            : "Per 100 traders"
          : zh
            ? "每 100 USDT 仓位金额"
            : "Per 100 USDT of exposure"}
      </div>
      <div className="flex justify-between text-sm font-medium">
        <Link prefetch={false} href={exchangeLink(coin, { side: "long" })}>
          {zh ? "持多" : "Long"} {long}
        </Link>
        <Link prefetch={false} href={exchangeLink(coin, { side: "short" })}>
          {zh ? "持空" : "Short"} {short}
        </Link>
      </div>
      <div aria-hidden="true" className="flex h-1.5 overflow-hidden rounded bg-down/70">
        <span className="bg-up" style={{ width: long + "%" }} />
      </div>
    </div>
  );
}

export function ExchangeCard({ coin, obs, zh }: { coin: string; obs: Observation | null; zh: boolean }) {
  const positions = obs?.readings.ALL,
    trades = obs?.readings["24h"],
    binance = obs?.readings.funding;
  const binanceFresh = binance && !binance.stale;
  const funding = binanceFresh ? binance : obs?.readings.okx_funding;
  const g = positions?.data.traders,
    net = trades?.data.traders.net;
  const stale = positions?.stale || trades?.stale;
  const pct = g?.money_share;
  const stance =
    pct == null
      ? null
      : pct > 0.5
        ? zh
          ? "多仓金额更多"
          : "more long exposure"
        : pct < 0.5
          ? zh
            ? "空仓金额更多"
            : "more short exposure"
          : zh
            ? "多空金额相同"
            : "equal long and short exposure";
  const movement =
    net == null
      ? null
      : net > 0
        ? zh
          ? "净买入"
          : "net buying"
        : net < 0
          ? zh
            ? "净卖出"
            : "net selling"
          : zh
            ? "买卖金额持平"
            : "balanced flow";
  const fee = funding?.data;
  return (
    <div className="space-y-3 border-b border-border pb-4 mb-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <b>{zh ? "币安近 30 天盈利筛选组" : "Binance 30-day profit cohort"}</b>
        <a
          className="text-xs text-accent"
          href="https://www.binance.com/en/support/faq/detail/197ed63fbca34124bae53d8e6d4c9d29"
        >
          {zh ? "币安筛选规则" : "Selection rules"}
        </a>
      </div>
      {!stale && stance && movement && (
        <p className="text-lg font-semibold">
          {zh ? `目前${stance}，近 24 小时${movement}。` : `Currently ${stance}, with ${movement} over 24h.`}
        </p>
      )}
      <p className="text-xs text-muted">
        {stale && (zh ? "已过期 · " : "Stale · ")}
        {zh ? "持仓采集" : "Positions observed"} {utc(positions?.at)} · {zh ? "交易采集" : "Flow observed"}{" "}
        {utc(trades?.at)}
      </p>
      {g ? (
        <>
          <Distribution coin={coin} share={g.head_share} people zh={zh} />
          <Distribution coin={coin} share={g.money_share} people={false} zh={zh} />
        </>
      ) : (
        <p>{zh ? "等待币安持仓数据" : "Awaiting Binance positions"}</p>
      )}
      <div className="text-sm">
        <Link
          prefetch={false}
          className="text-accent"
          href={exchangeLink(coin, { view: "flow", window: "24h" })}
        >
          {zh ? "近 24 小时" : "Last 24h"} · {movement ?? "—"}{" "}
          {formatValue(net == null ? null : Math.abs(net), zh, true)} USDT →
        </Link>
        <p className="text-xs text-muted">
          {zh
            ? "买入可能是开多，也可能是把原来的空仓平掉。"
            : "Buying can open longs or close existing shorts."}
        </p>
      </div>
      <div className="rounded border border-border bg-panel-2 p-3 text-sm">
        <b>{zh ? "谁付资金费？" : "Who pays funding?"}</b>
        {!binanceFresh && (
          <p className="text-xs text-muted">
            {zh ? "币安本期费率暂无数据" : "Current Binance rate unavailable"}
            {binance && ` · ${utc(binance.at)}`}
          </p>
        )}
        {fee && !funding?.stale ? (
          <>
            <p>
              {binanceFresh ? "Binance" : "OKX"} ·{" "}
              {fee.payer === "none"
                ? zh
                  ? "本期费率为零"
                  : "Zero funding"
                : fee.payer === "long"
                  ? zh
                    ? "多头付给空头"
                    : "Longs pay shorts"
                  : zh
                    ? "空头付给多头"
                    : "Shorts pay longs"}{" "}
              · {(fee.rate * 100).toFixed(5)}%
            </p>
            <p>
              {zh
                ? `按 1 万 USDT 名义多仓，本期预计${fee.rate < 0 ? "收" : "付"} ${formatValue(fee.per_10000, zh)} USDT。`
                : `A 10,000 USDT long would ${fee.rate < 0 ? "receive" : "pay"} ${formatValue(fee.per_10000, zh)} USDT.`}
            </p>
            <small>
              {binanceFresh ? `Binance ${coin}USDT` : `OKX ${coin}-USDT-SWAP`} ·{" "}
              {zh ? "预计结算" : "Settlement"} {utc(fee.due)}
            </small>
          </>
        ) : null}
      </div>
      <Link prefetch={false} className="text-sm text-accent" href={exchangeLink(coin)}>
        {zh ? "查看持仓与交易明细" : "Explore positions and flow"} →
      </Link>
      <Link prefetch={false} className="block text-sm text-accent" href={profileLink(coin)}>
        {zh ? "查看开仓成本与强平价" : "Explore entry and liquidation prices"} →
      </Link>
    </div>
  );
}

import { formatValue, type Funding, type Observation } from "@/lib/exchange";
import { utc } from "@/lib/research";

const levels = {
  mild: ["较轻", "Mild"],
  moderate: ["中等", "Moderate"],
  high: ["较高", "High"],
  very_high: ["很高", "Very high"],
};

function FundingRow({ venue, instrument, sample, zh }: {
  venue: string; instrument: string; sample?: Funding; zh: boolean;
}) {
  if (!sample || sample.stale) return (
    <div className="text-xs text-muted">
      {venue} {instrument} · {sample
        ? (zh ? "报价已过期" : "Stale quote")
        : (zh ? "暂无报价" : "Quote unavailable")}
      {sample && ` · ${utc(sample.data.source_at)}`}
    </div>
  );
  const fee = sample.data, h = fee.history;
  const side = fee.rate < 0 ? (zh ? "空头" : "shorts") : (zh ? "多头" : "longs");
  const percent = h?.percentile;
  const level = h?.level && levels[h.level][zh ? 0 : 1];
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-2">
        <span className="text-xs text-muted">{venue} · {instrument}</span>
        <span className="font-mono text-xs">{(fee.rate * 100).toFixed(6)}%</span>
      </div>
      <p className="font-semibold">
        {fee.rate === 0 ? (zh ? "本期双方都不用付" : "Neither side pays") :
          fee.rate < 0 ? (zh ? "空头付给多头" : "Shorts pay longs") : (zh ? "多头付给空头" : "Longs pay shorts")}
        {level && ` · ${zh ? "费用" : ""}${level}`}
      </p>
      {fee.rate !== 0 && <p>{zh
        ? `每 1 万 USDT 名义仓位，${side}本期预计付 ${formatValue(fee.per_10000, zh)} USDT。`
        : `Per 10,000 USDT of exposure, ${side} would pay ${formatValue(fee.per_10000, zh)} USDT this settlement.`}</p>}
      {percent != null && (
        <div className="space-y-1">
          <p className="text-xs">{zh ? `与过去一年同样由${side}付费时相比，本期排在第 ${percent} 百分位。`
            : `Percentile ${percent} among historical settlements paid by ${side}.`}</p>
          <div aria-hidden="true" className="relative h-1.5 rounded bg-border">
            <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent" style={{ left: `${percent}%` }} />
          </div>
          <div className="flex justify-between text-xs text-muted"><span>{zh ? "同向费用低" : "Lower fee"}</span><span>{zh ? "同向费用高" : "Higher fee"}</span></div>
        </div>
      )}
      {h?.side_pct != null && <p className="text-xs text-muted">{zh
        ? `历史上每 100 次同周期结算，约 ${Math.round(h.side_pct)} 次由${side}付费。`
        : `In about ${Math.round(h.side_pct)} of every 100 comparable settlements, ${side} paid.`}</p>}
      {fee.rate !== 0 && !level && <p className="text-xs text-muted">{zh
        ? h && !h.hours ? "结算周期待核实，暂不评级。" : "历史样本不足，暂不评级。"
        : h && !h.hours ? "Settlement interval unverified. No rating." : "Insufficient history for a rating."}</p>}
      <p className="text-xs text-muted">{zh ? "报价" : "Quoted"} {utc(fee.source_at)} · {zh ? "预计结算" : "Due"} {utc(fee.due)}</p>
      {h && <details className="text-xs text-muted">
        <summary className="w-fit cursor-pointer text-accent">{zh ? "历史范围与分级口径" : "History and grading"}</summary>
        <p>{h.start} — {h.end} · {h.months}/12 {zh ? "个完整月" : "complete months"}
          {h.hours && ` · ${h.hours}h · ${h.n} ${zh ? "次可比结算" : "comparable settlements"} · ${h.same} ${zh ? "次同向" : "same payer"}`}</p>
        <p>{zh ? "将当前预计费率的绝对值，与本合约同周期、同付款方的历史实际费率比较。并列取中位。低于第 50 百分位为较轻，50 起为中等，80 起为较高，95 起为很高。"
          : "The estimated fee magnitude is ranked against settled rates for this contract, interval and payer. Ties use midrank. Mild below P50, moderate from P50, high from P80, very high from P95."}</p>
      </details>}
    </div>
  );
}

export function FundingPanel({ coin, readings, zh }: { coin: string; readings?: Observation["readings"]; zh: boolean }) {
  return (
    <section className="space-y-2 rounded border border-border bg-panel-2 p-3 text-sm">
      <h3 className="font-bold">{zh ? "谁付资金费？费用高吗？" : "Who pays funding, and how much?"}</h3>
      <FundingRow venue="Binance" instrument={coin + "USDT"} sample={readings?.funding} zh={zh} />
      <div className="border-t border-border pt-2">
        <FundingRow venue="OKX" instrument={coin + "-USDT-SWAP"} sample={readings?.okx_funding} zh={zh} />
      </div>
    </section>
  );
}

import { Badge, Panel, td, th } from "@/components/ui";
import { numeric, percent, research, utc, type FactorTest, type RegimeStats } from "@/lib/research";

const EN: Record<string, string> = {
  oi_chg_1d: "Open interest, 1-day change (log)",
  oi_chg_7d: "Open interest, 7-day change (log)",
  oi_with_price: "1-day OI change × 1-day price direction (adding with the move is positive)",
  top_position_z: "Top-trader position long/short ratio, z-score vs the prior 90 days",
  top_position_chg_1d: "Top-trader position long/short ratio, 1-day change",
  account_ratio_z: "All-account long/short ratio, z-score vs the prior 90 days",
  top_vs_crowd_z: "Top-trader position ratio relative to the all-account ratio, 90-day z-score",
  taker_1d: "Previous day's taker buy/sell volume ratio (log mean)",
  funding_1d: "Funding settled in the previous 24 hours",
  funding_7d_z: "7-day mean funding, z-score vs the prior 90 days",
  mom_1d: "Price momentum, 1 day (control)",
  mom_7d: "Price momentum, 7 days (control)",
};
const REGIMES: Record<string, [string, string, string, string]> = {
  long_build: ["多头建仓", "Long build-up", "价格涨、持仓量增：新开多为主", "Price up, OI up: mostly new longs"],
  short_cover: ["空头回补", "Short covering", "价格涨、持仓量降：空头平仓推动", "Price up, OI down: shorts closing"],
  short_build: ["空头建仓", "Short build-up", "价格跌、持仓量增：新开空为主", "Price down, OI up: mostly new shorts"],
  long_unwind: ["多头去杠杆", "Long unwinding", "价格跌、持仓量降：多头平仓或被强平", "Price down, OI down: longs closing or liquidated"],
};
const STATUS = {
  candidate: { tone: "up" as const, zh: "候选", en: "Candidate" },
  in_sample_only: { tone: "warn" as const, zh: "仅样本内", en: "In-sample only" },
  none: { tone: "neutral" as const, zh: "未通过", en: "Not significant" },
};

function signed(v: number | null | undefined) {
  return v == null ? "—" : (v > 0 ? "+" : "") + numeric(v * 100, 2) + "%";
}

function Regimes({ regimes, today, zh }: { regimes: Record<string, RegimeStats>; today: string | null; zh: boolean }) {
  const all = regimes.all;
  return (
    <table className="w-full text-sm">
      <thead>
        <tr>
          <th className={th}>{zh ? "前一天的状态" : "Previous day"}</th>
          <th className={`${th} text-right`}>{zh ? "天数" : "Days"}</th>
          <th className={`${th} text-right`}>{zh ? "随后 1 天平均" : "Next day, mean"}</th>
          <th className={`${th} text-right`}>{zh ? "上涨比例" : "Up"}</th>
          <th className={`${th} text-right`}>{zh ? "随后 3 天平均" : "Next 3 days, mean"}</th>
          <th className={`${th} text-right`}>{zh ? "上涨比例" : "Up"}</th>
        </tr>
      </thead>
      <tbody>
        {Object.entries(REGIMES).map(([k, [zn, en, zd, ed]]) => {
          const r = regimes[k];
          return (
            <tr key={k} className={`border-t border-border/60 ${k === today ? "bg-accent/10" : ""}`}>
              <td className={td}>
                <b>{zh ? zn : en}</b>
                {k === today && <Badge tone="accent">{zh ? "最新" : "Latest"}</Badge>}
                <div className="text-xs text-muted">{zh ? zd : ed}</div>
              </td>
              <td className={`${td} num text-right`}>{r.n}</td>
              <td className={`${td} num text-right`}>{signed(r.mean_1d)}</td>
              <td className={`${td} num text-right`}>{percent(r.up_1d)}</td>
              <td className={`${td} num text-right`}>{signed(r.mean_3d)}</td>
              <td className={`${td} num text-right`}>{percent(r.up_3d)}</td>
            </tr>
          );
        })}
        <tr className="border-t border-border text-muted">
          <td className={td}>{zh ? "全部交易日" : "All days"}</td>
          <td className={`${td} num text-right`}>{all.n}</td>
          <td className={`${td} num text-right`}>{signed(all.mean_1d)}</td>
          <td className={td}></td>
          <td className={`${td} num text-right`}>{signed(all.mean_3d)}</td>
          <td className={td}></td>
        </tr>
      </tbody>
    </table>
  );
}

/** Exploratory screen of Binance positioning factors: in-sample, out-of-sample, corrected for every test run. */
export async function FactorLab({ coin, zh }: { coin: string; zh: boolean }) {
  const d = await research.factors(coin).catch(() => null);
  if (!d || d.status === "not_run")
    return <Panel>{zh ? "因子筛查尚未运行（每天 UTC 06:40，首次需要先回填币安持仓数据）" : "The factor screen has not run yet (daily at 06:40 UTC, after the Binance positioning backfill)"}</Panel>;
  if (d.status !== "screened")
    return (
      <Panel>
        {zh
          ? `历史数据不足：已有 ${d.days ?? 0} 个完整交易日，至少需要 200 个。币安持仓数据正在回填。`
          : `Not enough history yet: ${d.days ?? 0} complete days, at least 200 needed. Binance positioning data is backfilling.`}
      </Panel>
    );
  const tests = [...(d.tests ?? [])].sort((a, b) => (a.q ?? 2) - (b.q ?? 2));
  const today = d.today;
  const label = (k: string) => (zh ? d.factors[k] : EN[k] ?? k);
  const row = (t: FactorTest) => {
    const st = STATUS[t.status];
    const now = today?.values[t.factor];
    return (
      <tr key={t.factor + t.horizon} className="border-t border-border/60">
        <td className={`${td} whitespace-normal`}>{label(t.factor)}</td>
        <td className={`${td} num text-right`}>{t.horizon}{zh ? " 天" : "d"}</td>
        <td className={`${td} num text-right`}>
          {numeric(t.ic_is, 3)} <span className="text-xs text-muted">n={t.n_is}</span>
        </td>
        <td className={`${td} num text-right`}>{numeric(t.q, 3)}</td>
        <td className={`${td} num text-right`}>
          {numeric(t.ic_oos, 3)} <span className="text-xs text-muted">n={t.n_oos}</span>
        </td>
        <td className={td}>
          <Badge tone={st.tone}>{zh ? st.zh : st.en}</Badge>
        </td>
        <td className={`${td} num text-right`}>{now?.percentile == null ? "—" : `P${Math.round(now.percentile * 100)}`}</td>
      </tr>
    );
  };
  return (
    <div className="space-y-4">
      <Panel
        title={zh ? "价格与持仓量的四种状态，之后发生了什么" : "Price and open interest: what followed each state"}
        hint={
          zh
            ? `币安 ${coin}USDT 永续，${d.start} 至 ${d.end}，按 UTC 日划分。描述性统计，未经显著性检验。`
            : `Binance ${coin}USDT perpetual, ${d.start} to ${d.end}, UTC days. Descriptive; not tested for significance.`
        }
      >
        {d.regimes && <Regimes regimes={d.regimes} today={today?.regime ?? null} zh={zh} />}
      </Panel>
      <Panel
        title={zh ? "因子筛查（探索性）" : "Factor screen (exploratory)"}
        hint={
          zh
            ? `每天 00:00 UTC 只用此前已发布的数据计算因子，与随后 1 天、3 天收益做秩相关（IC）。${d.split} 之前为样本内，之后为样本外。共 ${d.test_count} 项检验一起做错误发现率校正（q < ${d.fdr}），样本外需保持同向且单侧 p < ${d.oos_alpha} 才算候选。候选只是值得预登记、继续前向验证的假设，不是交易信号。`
            : `Factors use only data published before each 00:00 UTC and are rank-correlated (IC) with the next 1- and 3-day returns. In-sample before ${d.split}, out-of-sample after. All ${d.test_count} tests share one false-discovery correction (q < ${d.fdr}); a candidate must also keep its sign out of sample with one-sided p < ${d.oos_alpha}. Candidates are hypotheses worth pre-registering, not trading signals.`
        }
      >
        <div className="-mx-4 -my-4 overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-panel-2">
              <tr>
                <th className={th}>{zh ? "因子" : "Factor"}</th>
                <th className={`${th} text-right`}>{zh ? "周期" : "Horizon"}</th>
                <th className={`${th} text-right`}>{zh ? "样本内 IC" : "In-sample IC"}</th>
                <th className={`${th} text-right`}>q</th>
                <th className={`${th} text-right`}>{zh ? "样本外 IC" : "Out-of-sample IC"}</th>
                <th className={th}>{zh ? "结论" : "Verdict"}</th>
                <th className={`${th} text-right`}>{zh ? "最新读数分位" : "Latest percentile"}</th>
              </tr>
            </thead>
            <tbody>{tests.map(row)}</tbody>
          </table>
        </div>
      </Panel>
      <p className="text-xs text-muted">
        {zh ? "计算于" : "Computed"} {utc(d.computed_at)} · {d.version}
        {today && ` · ${zh ? "最新决策日" : "latest decision day"} ${today.day}`}
      </p>
    </div>
  );
}

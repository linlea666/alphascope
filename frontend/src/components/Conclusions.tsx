import { Badge, Panel } from "@/components/ui";
import { Link } from "@/i18n/routing";
import { numeric, research, type Conclusion } from "@/lib/research";

const TIERS = [
  ["verified", "已有结论", "Concluded", "事先设定的检验已得出结果，正面或负面都列在这里", "A test fixed in advance has decided, either way", "up"],
  ["watching", "观察中", "Being tested", "正在积累样本，或只是筛查出的候选", "Still accumulating, or only a screening candidate", "warn"],
  ["descriptive", "描述性", "Descriptive", "当前读数和历史对照，不构成预测", "Readings and history, not predictions", "neutral"],
] as const;
const HYPOTHESES: Record<string, [string, string]> = {
  broad_long: ["多钱包广泛加多是否强于单一大户加多", "Broad long additions vs one large account"],
  short_cover: ["上涨中加多是否强于空头回补", "Long accumulation vs short covering after a rise"],
  eth_specialist: ["ETH 专项群体偏多是否领先", "ETH specialists' long tilt"],
};
const REGIMES: Record<string, [string, string]> = {
  long_build: ["多头建仓", "long build-up"],
  short_cover: ["空头回补", "short covering"],
  short_build: ["空头建仓", "short build-up"],
  long_unwind: ["多头去杠杆", "long unwinding"],
};
const LEVELS: Record<string, [string, string]> = {
  mild: ["较轻", "mild"], moderate: ["中等", "moderate"], high: ["较高", "high"], very_high: ["很高", "very high"],
};

const pct = (v: number | null | undefined) => (v == null ? "—" : (v > 0 ? "+" : "") + numeric(v * 100, 2) + "%");

type Href = { pathname: "/replay" | "/copy-board"; query?: Record<string, string> };
const replay = (coin: string, tab: string): Href => ({ pathname: "/replay", query: { coin, tab } });

function line(c: Conclusion, zh: boolean): [string, Href] {
  const coin = c.coin ?? "";
  switch (c.kind) {
    case "forecast": {
      const d = c.data;
      const beats = d.p !== null && d.p < 0.05 && (d.mean_diff ?? 0) < 0;
      const text =
        d.p === null
          ? zh ? `积累中（${d.blocks}/8 个独立块）` : `accumulating (${d.blocks}/8 blocks)`
          : beats
            ? zh ? `优于历史频率基准，p=${numeric(d.p, 3)}` : `beats the base-rate baseline, p=${numeric(d.p, 3)}`
            : zh ? `未优于历史频率基准，p=${numeric(d.p, 3)}` : `no better than the base rate, p=${numeric(d.p, 3)}`;
      return [zh ? `${coin} 24 小时方向概率预测：${text}` : `${coin} 24h direction forecast: ${text}`, replay(coin, "archive")];
    }
    case "study": {
      const d = c.data;
      const name = HYPOTHESES[d.hypothesis]?.[zh ? 0 : 1] ?? d.hypothesis;
      const state = d.review === "approve"
        ? zh ? "已审核通过" : "approved"
        : d.review === "reject"
          ? zh ? "审核未通过" : "rejected"
        : d.eligible
          ? zh ? "达到审核条件" : "eligible for review"
          : zh ? `已配对 ${d.pairs} 组，检验 ${d.tested_pairs} 组 / ${d.weeks} 周` : `${d.pairs} pairs, ${d.tested_pairs} tested over ${d.weeks} weeks`;
      return [`${coin} · ${name}${zh ? "：" : ": "}${state}`, replay(coin, "archive")];
    }
    case "regime": {
      const d = c.data;
      const name = d.regime ? REGIMES[d.regime]?.[zh ? 0 : 1] : null;
      const text = name && d.stats
        ? zh
          ? `${d.day} 前一天为「${name}」；历史同状态之后 1 天平均 ${pct(d.stats.mean_1d)}（全部交易日 ${pct(d.all.mean_1d)}），${d.stats.n} 天`
          : `before ${d.day}: ${name}; historically the next day averaged ${pct(d.stats.mean_1d)} (all days ${pct(d.all.mean_1d)}), ${d.stats.n} days`
        : zh ? "最新一天状态不可判定" : "latest state undetermined";
      return [`${coin} · ${text}`, replay(coin, "factors")];
    }
    case "factors": {
      const d = c.data;
      const text = d.candidates.length
        ? zh
          ? `历史筛查有 ${d.candidates.length} 个候选因子（共 ${d.tests} 项检验，已做多重校正），待前向验证`
          : `${d.candidates.length} screening candidates out of ${d.tests} corrected tests, awaiting forward tests`
        : zh ? `历史筛查 ${d.tests} 项检验，没有因子通过` : `no factor passed ${d.tests} corrected tests`;
      return [`${coin} · ${text}`, replay(coin, "factors")];
    }
    case "funding": {
      const d = c.data;
      const payer = d.rate == null || d.rate === 0 ? null : d.rate > 0 ? (zh ? "多头付费" : "longs pay") : zh ? "空头付费" : "shorts pay";
      const level = d.level ? LEVELS[d.level]?.[zh ? 0 : 1] : null;
      const text = payer
        ? `${payer}${level ? (zh ? `，费用${level}（同向历史 P${Math.round(d.percentile ?? 0)}）` : `, ${level} (P${Math.round(d.percentile ?? 0)} of same-payer history)`) : ""}`
        : zh ? "本期费率为零或暂无" : "zero or unavailable";
      return [zh ? `${coin} 币安资金费：${text}` : `${coin} Binance funding: ${text}`, replay(coin, "exchange")];
    }
    case "persistence": {
      const d = c.data;
      const venue = d.venue === "okx" ? "OKX" : "Hyperliquid";
      // OKX sizes both windows by current equity: only the direction test is free of that shared scale
      const p = d.shared_denominator ? d.sign_p : d.p_value;
      const measure = d.shared_denominator
        ? zh ? `方向一致性 ${numeric(d.sign_mean, 2)}` : `direction agreement ${numeric(d.sign_mean, 2)}`
        : zh ? `排名相关 ${numeric(d.mean_rho, 2)}，前 1/5 比后 1/5 多 ${pct(d.top_minus_bottom)}`
          : `rank correlation ${numeric(d.mean_rho, 2)}, top fifth ahead by ${pct(d.top_minus_bottom)}`;
      const text = d.status !== "tested" || p == null
        ? zh ? `积累中（${d.tested_days}/${d.need_days} 个检验日期）` : `accumulating (${d.tested_days}/${d.need_days} dates)`
        : p < 0.05
          ? zh ? `有持续性：${measure}，p=${numeric(p, 3)}` : `persists: ${measure}, p=${numeric(p, 3)}`
          : zh ? `未见持续性：${measure}，p=${numeric(p, 3)}` : `no persistence: ${measure}, p=${numeric(p, 3)}`;
      return [zh ? `${venue} 过去 ${d.lookback} 天跟单收益高的人，之后 ${d.horizon} 天：${text}`
        : `${venue} best ${d.lookback}-day copy returns, next ${d.horizon} days: ${text}`, { pathname: "/copy-board" }];
    }
    case "copy_score": {
      const d = c.data;
      const text = d.status !== "tested"
        ? zh ? `积累中（${d.tested_days}/${d.need_days} 个独立评分日）` : `accumulating (${d.tested_days}/${d.need_days} grading days)`
        : (d.p_value ?? 1) < 0.05 && (d.mean_rho ?? 0) > 0
          ? zh ? `评分高者随后跟单更赚钱，p=${numeric(d.p_value, 3)}` : `higher grades earned more afterwards, p=${numeric(d.p_value, 3)}`
          : zh ? `评分与随后收益没有显著关系，p=${numeric(d.p_value, 3)}` : `no significant link to later returns, p=${numeric(d.p_value, 3)}`;
      return [zh ? `可跟单评分（${d.horizon} 天后验证）：${text}` : `Copyability grades (${d.horizon}-day check): ${text}`, { pathname: "/copy-board" }];
    }
  }
}

/** One place for what the research currently supports, sorted by how strong the evidence is. */
export async function Conclusions({ zh }: { zh: boolean }) {
  const d = await research.conclusions().catch(() => null);
  if (!d) return null;
  return (
    <Panel
      title={zh ? "研究结论 · 按证据强弱分级" : "Research conclusions by strength of evidence"}
      hint={zh ? "只有「已有结论」一栏来自事先设定的检验；其余是正在检验的问题或当前读数。" : "Only the first column comes from tests fixed in advance; the rest are open questions or readings."}
    >
      <div className="grid gap-4 md:grid-cols-3">
        {TIERS.map(([tier, zn, en, zd, ed, tone]) => {
          const items = d.items.filter((c) => c.tier === tier);
          return (
            <div key={tier} className="space-y-2">
              <div className="flex items-center gap-2">
                <Badge tone={tone}>{zh ? zn : en}</Badge>
                <span className="text-xs text-muted">{zh ? zd : ed}</span>
              </div>
              {items.length ? (
                <ul className="space-y-1.5 text-sm">
                  {items.map((c, i) => {
                    const [text, href] = line(c, zh);
                    return (
                      <li key={i}>
                        <Link prefetch={false} href={href} className="hover:text-accent">
                          {text}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-sm text-muted">{zh ? "目前没有" : "None yet"}</p>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

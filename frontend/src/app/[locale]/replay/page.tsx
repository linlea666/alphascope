import {ExchangeDesk} from "@/components/ExchangeDesk";
import { getLocale } from "next-intl/server";
import { Link } from "@/i18n/routing";
import { LinesChart } from "@/components/LinesChart";
import { FactorReviewForm, NoticeWatch, PublishReport, EventEditor } from "@/components/ResearchActions";
import { PageHeader, Panel, Stat, td, th } from "@/components/ui";
import { noticeLabel, research, numeric, percent, utc, type Detail, type EventDef } from "@/lib/research";

type Query = Record<string, string | undefined>;
const control = "rounded border border-border bg-panel-2 px-2 py-1 text-sm";
const tab = (on: boolean) =>
  `rounded px-3 py-1.5 text-sm ${on ? "bg-accent text-white" : "border border-border text-muted hover:text-accent"}`;
function title(e: EventDef, zh: boolean) {
  const names: Record<string, string[]> = {
    "btc-aug-rise": ["8 月上涨", "August advance"],
    "btc-sep-rise": ["9 月上涨", "September advance"],
    "btc-drop-peak1": ["下跌候选 · 首次冲高", "Decline · first peak"],
    "btc-drop-peak2": ["下跌候选 · 二次冲高", "Decline · second peak"],
  };
  return names[e.event_key]?.[zh ? 0 : 1] ?? e.title;
}
function FlowPanel({
  p,
  zh,
  locale,
  frozen = false,
}: {
  p: Detail;
  zh: boolean;
  locale: string;
  frozen?: boolean;
}) {
  const f = p.flow;
  const source = (venue: string, wallet?: string) => ({
    pathname: "/replay",
    query: {
      coin: p.coin,
      event: p.event.id,
      source: venue,
      hours: p.hours,
      mode: p.mode,
      vintage: p.vintage,
      wallet,
    },
  });
  const components = [
    ["long_add", zh ? "新增多仓" : "Long adds"],
    ["long_cut", zh ? "多仓减少" : "Long reductions"],
    ["short_add", zh ? "新增空仓" : "Short adds"],
    ["short_cut", zh ? "空仓减少" : "Short reductions"],
  ] as const;
  return (
    <Panel
      title={title(p.event, zh)}
      hint={`${utc(p.event.start_at)} → ${utc(p.event.end_at)} · ${p.event.source} · v${p.event.revision}`}
    >
      <div className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {components.map(([k, label]) => (
          <Stat key={k} label={label} value={numeric(f[k])} sub={p.coin} />
        ))}
      </div>
      <div className="my-3 grid grid-cols-2 gap-2 text-sm md:grid-cols-4">
        <span>
          {zh ? "净变化" : "Net change"} <b>{numeric(f.net)}</b>
        </span>
        <span>
          {zh ? "剔除最大账户" : "Ex largest"} <b>{numeric(f.without_largest)}</b>
        </span>
        <span>
          {zh ? "前五 / 前十集中度" : "Top 5 / 10"}{" "}
          <b>
            {percent(f.top5_share)} / {percent(f.top10_share)}
          </b>
        </span>
        <span>
          {zh ? "账户 / 成交" : "Wallets / fills"}{" "}
          <b>
            {f.wallets} / {numeric(f.fills, 0)}
          </b>
        </span>
      </div>
      {p.common_wallets !== undefined && (
        <p className="text-xs text-accent">
          {zh ? "共同活跃钱包" : "Common active wallets"} {p.common_wallets} · {numeric(p.common_net)}{" "}
          {p.coin} · {zh ? "净变化 / 总变动" : "Net / gross change"} {percent(p.net_fraction)}
        </p>
      )}
      <p className="my-2 text-xs text-muted">
        {zh ? "HL 逐笔成交 · OKX 整仓开仓记录" : "HL archived fills · OKX whole-position opens"}{" "}
        {p.okx.positions} / {p.okx.traders} {zh ? "人" : "traders"} · {zh ? "证据截止" : "Evidence cutoff"}{" "}
        {utc(p.evidence_cutoff)}
      </p>
      {!f.wallets && (
        <p className="text-sm text-warn">
          {zh ? "窗口内缺少可用的归档成交" : "No eligible archived fills in this window"}
        </p>
      )}
      <LinesChart
        locale={locale}
        lines={[{ name: p.coin + " · USD", data: p.prices }]}
        height={215}
        formats={["usd"]}
      />
      <details className="mt-3">
        <summary className="cursor-pointer text-sm text-accent">
          {zh ? "查看贡献钱包" : "Contributing wallets"} ({f.contributors.length})
        </summary>
        <div className="max-h-80 overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {[
                  zh ? "钱包" : "Wallet",
                  zh ? "净变化" : "Net",
                  zh ? "成交" : "Fills",
                  zh ? "已确认清算成交" : "Verified liquidation fills",
                ].map((x) => (
                  <th className={th} key={x}>
                    {x}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {f.contributors.map((r) => (
                <tr key={r.address}>
                  <td className={td}>
                    <Link
                      className="text-accent"
                      href={{
                        pathname: `/trader/${r.address}`,
                        query: { rc: p.coin, re: p.event.start_at },
                        hash: "replay",
                      }}
                    >
                      {r.address.slice(0, 9)}…{r.address.slice(-4)}
                    </Link>
                  </td>
                  <td className={td}>{numeric(r.net)}</td>
                  <td className={td}>
                    <Link className="text-accent" href={source("hyperliquid", r.address)}>
                      {r.fills}
                    </Link>
                  </td>
                  <td className={td}>{r.liquidation_fills}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
      <div className="mt-2 flex gap-4 text-xs text-accent">
        <Link href={source("hyperliquid")}>{zh ? "HL 逐笔来源" : "HL fill records"}</Link>
        <Link href={source("okx")}>{zh ? "OKX 整仓来源" : "OKX position records"}</Link>
      </div>
      {!frozen && (
        <PublishReport
          body={{ event_id: p.event.id, coin: p.coin, hours: p.hours, mode: p.mode, vintage: p.vintage }}
          zh={zh}
        />
      )}
    </Panel>
  );
}

export default async function ReplayPage({ searchParams }: { searchParams: Promise<Query> }) {
  const q = await searchParams,
    locale = await getLocale(),
    zh = locale === "zh";
  const { coins } = await research.coins();
  const coin = coins.includes(q.coin ?? "") ? q.coin! : (coins[0] ?? "BTC");
  const selectedTab = q.tab ?? "events";
  const href = (params: Query) => ({ pathname: "/replay", query: { coin, ...params } });
  return (
    <div className="space-y-4">
      <PageHeader
        title={zh ? "行情回放实验室" : "Market Replay Lab"}
        subtitle={
          zh
            ? "市场行为、贡献账户与前瞻验证"
            : "Market behaviour, contributing accounts and forward validation"
        }
      >
        <div className="flex gap-2">
          {coins.map((c) => (
            <Link key={c} href={href({ coin: c, tab: selectedTab })} className={tab(c === coin)}>
              {c}
            </Link>
          ))}
        </div>
      </PageHeader>
      <nav className="flex flex-wrap gap-2">
        {[
          ["exchange", zh ? "交易所资金行为" : "Exchange positioning"],
          ["events", zh ? "事件对比" : "Event comparison"],
          ["live", zh ? "群体与清算" : "Cohorts & liquidation"],
          ["archive", zh ? "前瞻档案" : "Forward archive"],
        ].map(([k, name]) => (
          <Link key={k} className={tab(k === selectedTab)} href={href({ tab: k })}>
            {name}
          </Link>
        ))}
      </nav>
      {selectedTab === "exchange" ? <ExchangeDesk coin={coin} q={q} zh={zh}/> : selectedTab === "live" ? (
        <Live coin={coin} q={q} zh={zh} />
      ) : selectedTab === "archive" ? (
        <Forward coin={coin} zh={zh} />
      ) : (
        <Events coin={coin} coins={coins} q={q} zh={zh} locale={locale} />
      )}
    </div>
  );
}
async function Events({
  coin,
  coins,
  q,
  zh,
  locale,
}: {
  coin: string;
  coins: string[];
  q: Query;
  zh: boolean;
  locale: string;
}) {
  if (q.source === "okx" || q.source === "hyperliquid") return <Sources coin={coin} q={q} zh={zh} />;
  if (q.report && /^\d+$/.test(q.report)) {
    const saved = await research.report(Number(q.report), coin).catch(() => null);
    if (!saved) return <Panel>{zh ? "此币种下找不到该报告" : "Report unavailable for this coin"}</Panel>;
    return (
      <>
        <p className="text-xs text-muted">
          {zh ? "已归档证据" : "Archived evidence"} #{q.report} · {utc(saved.published_at)} ·{" "}
          {saved.source_version}
        </p>
        <FlowPanel p={saved.detail} zh={zh} locale={locale} frozen />
      </>
    );
  }
  const { events: catalog, as_of: catalogTime } = await research.catalog(coin);
  if (!catalog.length)
    return (
      <>
        <EventEditor coin={coin} zh={zh} />
        <Panel>
          {zh ? "该币种尚无经过价格核查的事件" : "No price-verified events are available for this coin"}
        </Panel>
      </>
    );
  const choices = [...catalog].sort(
    (a, b) => Number(b.method === "candidate-minute-v1") - Number(a.method === "candidate-minute-v1"),
  );
  const chosen =
    choices.find((e) => e.id === Number(q.event)) ??
    choices.find((e) => e.event_key === "btc-aug-rise") ??
    choices[0];
  const cross = coins.includes(q.peer ?? "") && q.peer !== coin;
  const peers = cross ? (await research.catalog(q.peer!)).events : choices;
  const compare = peers.find((e) => e.id === Number(q.compare) && e.id !== chosen.id);
  const hours = [1, 4, 12, 24, 48, 72].includes(Number(q.hours)) ? Number(q.hours) : 72;
  const mode = q.mode === "as_observed" ? "as_observed" : "retrospective";
  // Event catalogue response and browser/server clocks can differ; pin requests to a backend-supplied vintage.
  const vintage =
    q.vintage &&
    Number.isFinite(Date.parse(q.vintage)) &&
    Date.parse(q.vintage) <= Date.parse(catalogTime) &&
    [chosen, compare].every((e) => !e || Date.parse(e.available_at) <= Date.parse(q.vintage!))
      ? q.vintage
      : catalogTime;
  const sync = coins.includes(q.sync ?? "") && q.sync !== coin ? q.sync : undefined;
  const panels = compare
    ? (await research.compare([chosen.id, compare.id], hours, mode, vintage, !!cross)).panels
    : [await research.replay(chosen.id, hours, mode, vintage, sync)];
  const matches = q.similar === "1" ? await research.similar(chosen.id, hours, vintage, !!cross, mode) : null;
  const select = (name: string, label: string, value: string, options: [string, string][]) => (
    <label className="grid gap-1 text-xs text-muted">
      {label}
      <select name={name} defaultValue={value} className={control}>
        {options.map(([v, t]) => (
          <option key={v} value={v}>
            {t}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <>
      <form className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-panel p-3">
        <input type="hidden" name="coin" value={coin} />
        <input type="hidden" name="vintage" value={vintage} />
        {select(
          "event",
          zh ? "事件" : "Event",
          String(chosen.id),
          choices.map((e) => [String(e.id), title(e, zh)]),
        )}
        {select(
          "hours",
          zh ? "事前窗口" : "Lookback",
          String(hours),
          [1, 4, 12, 24, 48, 72].map((h) => [String(h), h + "h"]),
        )}
        {select("mode", zh ? "数据口径" : "Evidence mode", mode, [
          ["retrospective", zh ? "事后归档" : "Retrospective"],
          ["as_observed", zh ? "当时可得" : "Available at onset"],
        ])}
        {select(
          "peer",
          zh ? "对照币种" : "Comparison asset",
          q.peer ?? coin,
          coins.map((c) => [c, c]),
        )}
        {select("compare", zh ? "对比事件" : "Compare event", compare ? String(compare.id) : "", [
          ["", zh ? "单事件" : "Single event"],
          ...peers
            .filter((e) => e.id !== chosen.id)
            .map((e): [string, string] => [String(e.id), title(e, zh)]),
        ])}
        {select(
          "sync",
          zh ? "同期观察" : "Same dates",
          sync ?? coin,
          coins.map((c) => [c, c]),
        )}
        <label className="text-xs">
          <input type="checkbox" value="1" name="similar" defaultChecked={q.similar === "1"} />{" "}
          {zh ? "历史相似" : "Find analogs"}
        </label>
        <button className={tab(true)}>{zh ? "回放" : "Replay"}</button>
      </form>
      {sync && !compare && (
        <p className="text-sm text-accent">
          {zh ? "同期窗口" : "Same-date window"} · {coin} → {sync}
        </p>
      )}
      <EventEditor coin={coin} parent={chosen.id} zh={zh} />
      {panels.map((p) => (
        <FlowPanel key={p.event.id} p={p} zh={zh} locale={locale} />
      ))}
      {matches && (
        <Panel title={zh ? "历史相似事件" : "Historical analogs"}>
          <p className="mb-2 text-xs text-muted">
            {zh ? "非重叠候选" : "Non-overlapping candidates"} · {matches.n}
          </p>
          {matches.matches.map((m) => (
            <p key={m.event.id}>
              <Link
                className="text-accent"
                href={{ pathname: "/replay", query: { coin: m.event.coin, event: m.event.id, vintage } }}
              >
                {title(m.event, zh)}
              </Link>{" "}
              · {numeric(m.distance)} {m.opposite && (zh ? "· 反向结果" : "· opposite outcome")}
            </p>
          ))}
          {matches.counterexample && (
            <p>
              {zh ? "反例" : "Counterexample"} · {title(matches.counterexample.event, zh)}
            </p>
          )}
        </Panel>
      )}
    </>
  );
}

async function Live({ coin, q, zh }: { coin: string; q: Query; zh: boolean }) {
  const venue = q.venue === "okx" ? "okx" : "hyperliquid";
  const [s, history] = await Promise.all([research.summary(coin), research.timeline(coin, venue)]);
  const last = s.frames.find((f) => f.venue === venue);
  const requested = Number(q.frame);
  const selected = Number.isSafeInteger(requested) && requested > 0 ? { id: requested } : history.rows.at(-1);
  const offset = Math.max(0, Number.parseInt(q.offset || "0", 10) || 0);
  const found = selected ? await research.frame(selected.id, coin, offset).catch(() => null) : null;
  const f = found?.venue === venue ? found : null;
  const link = (venue: string, id?: number, offset?: number) => ({
    pathname: "/replay",
    query: { coin, venue, tab: "live", frame: id, offset },
  });
  return (
    <>
      <div className="flex gap-2">
        {["hyperliquid", "okx"].map((v) => (
          <Link key={v} href={link(v)} className={tab(v === venue)}>
            {v}
          </Link>
        ))}
      </div>
      <Panel title={zh ? "市场行为快照" : "Observed positioning"} hint={f ? utc(f.available_at) : undefined}>
        {f ? (
          <>
            <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
              <Stat
                label={zh ? "专项群体 · 做多人数" : "Specialists · long traders"}
                value={percent(f.metrics.smart.long_share)}
                sub={`n=${f.metrics.smart.n}`}
              />
              <Stat
                label={zh ? "匹配对照 · 做多人数" : "Matched controls · long traders"}
                value={percent(f.metrics.control.long_share)}
                sub={`n=${f.metrics.control.n}`}
              />
              <Stat
                label={zh ? "专项 / 对照 · 多头金额占比" : "Long notional · specialists / controls"}
                value={
                  percent(f.metrics.smart.long_notional_share) +
                  " / " +
                  percent(f.metrics.control.long_notional_share)
                }
              />
              <Stat
                label={zh ? "固定样本净变化" : "Common holder net change"}
                value={numeric(f.metrics.delta.net_units)}
                sub={`${coin} · n=${f.metrics.delta.common_n}`}
              />
              <Stat label="OI" value={numeric(f.context.fields.oi)} sub={f.context.oi_unit} />
              <Stat label={zh ? "资金费率" : "Funding"} value={percent(f.context.fields.funding)} />
              <Stat
                label={zh ? "最近强平距离" : "Nearest liquidation distance"}
                value={percent(f.metrics.nearest_distance)}
                sub={numeric(f.metrics.distance_in_hourly_sigma) + " σ"}
              />
              <Stat
                label={zh ? "≥20× 杠杆金额占比" : "Notional at ≥20× leverage"}
                value={percent(f.metrics.high_leverage_share)}
              />
              <Stat
                label={zh ? "标记价 / 基差" : "Mark / basis"}
                value={numeric(f.context.fields.mark)}
                sub={numeric(f.context.fields.basis_bps) + " bp"}
              />
              <Stat
                label={zh ? "可见买 / 卖深度" : "Visible bid / ask depth"}
                value={numeric(f.context.fields.depth?.bid) + " / " + numeric(f.context.fields.depth?.ask)}
                sub={f.context.oi_unit}
              />
            </div>
            <p className="mt-3 text-xs text-muted">
              {zh ? "新鲜账户 / 观测名单" : "Fresh / watched"} {f.metrics.fresh} / {f.metrics.watched} ·{" "}
              {zh ? "缺测字段" : "Missing fields"} {f.context.missing?.join(", ") || "—"}
            </p>
          </>
        ) : (
          <p>{zh ? "尚无已发布快照" : "No published snapshot"}</p>
        )}
      </Panel>
      {f?.metrics.windows && (
        <Panel title={zh ? "固定持仓账户的数量变化" : "Size changes among common holders"}>
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  {[
                    "h",
                    "n",
                    zh ? "多仓变化" : "Long change",
                    zh ? "空仓变化" : "Short change",
                    zh ? "净变化" : "Net change",
                  ].map((k) => (
                    <th key={k} className={th}>
                      {k}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(f.metrics.windows).map(([h, d]) => (
                  <tr key={h}>
                    {[
                      h,
                      numeric(d?.common_n),
                      numeric(d?.long_change),
                      numeric(d?.short_change),
                      numeric(d?.net_units),
                    ].map((v, i) => (
                      <td key={i} className={td}>
                        {v}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
      <Panel
        title={zh ? "潜在清算区域时间轴" : "Potential liquidation timeline"}
        hint={
          zh
            ? "选择一个观测时点以查看当时冻结的仓位。"
            : "Select a timestamp to inspect the positions frozen at publication."
        }
      >
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {["UTC", zh ? "多头区域" : "Long zones", zh ? "空头区域" : "Short zones"].map((x) => (
                  <th key={x} className={th}>
                    {x}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {history.rows
                .slice()
                .reverse()
                .map((r) => (
                  <tr key={r.id}>
                    <td className={td}>
                      <Link className="text-accent" href={link(venue, r.id)}>
                        {utc(r.available_at)}
                      </Link>
                    </td>
                    {["long", "short"].map((side) => (
                      <td key={side} className={td}>
                        {r.metrics.zones[side]
                          ?.map((z) => `${numeric(z.lo)}–${numeric(z.hi)} ($${numeric(z.notional, 0)})`)
                          .join(" · ") || "—"}
                      </td>
                    ))}
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        {f?.metrics.zone_followup?.map((z) => (
          <p key={z.side + z.lo} className="mt-2 text-xs text-muted">
            {z.side} {numeric(z.lo)}–{numeric(z.hi)} · {zh ? "触及 K 线" : "Touch bar"} {utc(z.touch_bar)} (
            {z.bars}) ·{" "}
            {zh
              ? "提前缩仓 / 疑似强平 / 交易所清算记录"
              : "Reductions / suspected / venue liquidation records"}{" "}
            {z.reductions_before_touch_or_cutoff} / {z.suspected_closes} / {z.venue_liquidation_records}
          </p>
        ))}
        {f && (
          <details className="mt-2" open={offset > 0 || undefined}>
            <summary className="cursor-pointer text-accent">
              {zh ? "查看冻结账户和强平价" : "Frozen accounts and liquidation prices"} ({f.total_accounts})
            </summary>
            <div className="max-h-72 overflow-auto">
              {offset > 0 && (
                <Link className="mr-4 text-accent" href={link(venue, f.id, Math.max(0, offset - 100))}>
                  {zh ? "上一页" : "Previous"}
                </Link>
              )}
              {offset + 100 < f.total_accounts && (
                <Link className="text-accent" href={link(venue, f.id, offset + 100)}>
                  {zh ? "下一页" : "Next"}
                </Link>
              )}
              {f.accounts.map((a) => (
                <p key={a.address} className="text-xs leading-6">
                  <Link
                    className="text-accent"
                    href={{
                      pathname: `/trader/${a.address}`,
                      query: { rc: coin, re: f.available_at },
                      hash: "replay",
                    }}
                  >
                    {a.address}
                  </Link>{" "}
                  · {a.cohort} · {numeric(a.liq_px)} · {utc(a.observed_at)}
                </p>
              ))}
            </div>
          </details>
        )}
      </Panel>
      <Panel
        title={zh ? "异动与后续结果" : "Anomalies and follow-up"}
        hint={last ? utc(last.available_at) : undefined}
      >
        {s.notices
          .filter((n) => n.venue === venue)
          .map((n) => (
            <div
              key={n.id}
              className="flex flex-wrap items-center justify-between gap-2 border-b border-border py-2 text-sm"
            >
              <Link href={link(venue, n.frame_id)} className="text-accent">
                #{n.id} · {noticeLabel(n.kind, zh)}
              </Link>
              <span>
                {utc(n.at)} · {percent(n.evidence.rank)} · n={n.evidence.n}
              </span>
              <span>
                {n.outcome?.status === "settled"
                  ? percent(n.outcome.ret)
                  : (n.outcome?.status ?? (zh ? "跟踪中" : "Tracking"))}
              </span>
              <details className="w-full text-xs">
                <summary className="text-accent">
                  {zh ? "样本、贡献与反向证据" : "Sample, contributors and counterevidence"}
                </summary>
                <p>
                  {zh ? "历史小时数" : "Reference hours"} {n.evidence.history_n} · {zh ? "观测值" : "Value"}{" "}
                  {numeric(n.evidence.value, 5)} · {zh ? "最大净变动占比" : "Largest net change share"}{" "}
                  {percent(n.evidence.counterevidence.largest_share)} · {zh ? "两次均持仓" : "Common holders"}{" "}
                  {n.evidence.counterevidence.common_n}
                </p>
                {n.evidence.contributors.map((a) => (
                  <Link
                    className="mr-2 text-accent"
                    key={a.address}
                    href={{ pathname: `/trader/${a.address}`, query: { rc: coin, re: n.at }, hash: "replay" }}
                  >
                    {a.address.slice(0, 10)}…
                  </Link>
                ))}
              </details>
              <NoticeWatch id={n.id} watching={n.watching} zh={zh} />
            </div>
          ))}
        {!s.notices.some((n) => n.venue === venue) && (
          <p className="text-sm text-muted">
            {zh ? "尚无此交易所的已登记异动" : "No registered anomaly for this exchange"}
          </p>
        )}
      </Panel>
    </>
  );
}

async function Forward({ coin, zh }: { coin: string; zh: boolean }) {
  const data = await research.archive(coin);
  const names: Record<string, string> = {
    broad_long: zh ? "多数钱包加多与大户集中加多" : "Broad vs concentrated additions",
    short_cover: zh ? "新增多仓与空仓缩减" : "Long accumulation vs short covering",
    eth_specialist: zh ? "ETH 专项群体与相对 BTC 收益" : "ETH specialists and BTC-relative returns",
  };
  return (
    <>
      <Panel
        title={zh ? "预登记实验" : "Registered experiments"}
        hint={zh ? "正式因子须由研究负责人审核。" : "Promotion to a formal factor requires research review."}
      >
        {data.studies.map((st) => (
          <div key={st.id} className="border-b border-border py-2">
            <b>
              {names[st.hypothesis] ?? st.hypothesis} · v{st.version}
            </b>
            <p className="text-xs text-muted">
              {utc(st.registered_at)} ·{" "}
              {st.runs[0]?.result.status ?? (zh ? "等待前瞻观测" : "Awaiting forward observations")} ·{" "}
              {zh ? "配对数" : "Pairs"} {st.runs[0]?.result.pairs ?? 0} · q {numeric(st.runs[0]?.result.q)}
            </p>
            {st.review && (
              <p className="text-xs">
                {st.review.decision} · {st.review.rationale}
              </p>
            )}
            {st.runs[0] && (
              <FactorReviewForm
                study={st.id}
                evaluation={st.runs[0].id}
                eligible={!!st.runs[0].result.review_eligible}
                zh={zh}
              />
            )}
          </div>
        ))}
      </Panel>
      <Panel
        title={zh ? "前瞻概率与实绩" : "Forward forecasts and outcomes"}
        hint={
          zh
            ? "影子模型 · 尚未验证可重复的预测优势"
            : "Shadow model · repeatable predictive edge has not been established"
        }
      >
        {data.scores.map((s) => {
          const k = s.skill;
          const verdict =
            k.p === null
              ? zh
                ? `积累中：${k.blocks}/8 个独立块（每块 ${k.block_size} 次预测）`
                : `Accumulating: ${k.blocks}/8 independent blocks of ${k.block_size} forecasts`
              : k.p < 0.05 && (k.mean_diff ?? 0) < 0
                ? zh
                  ? `优于基准 · p=${numeric(k.p, 3)}`
                  : `Beats baseline · p=${numeric(k.p, 3)}`
                : zh
                  ? `未优于基准 · p=${numeric(k.p, 3)}`
                  : `Not better than baseline · p=${numeric(k.p, 3)}`;
          return (
            <div key={s.model + s.horizon} className="mb-2 text-xs">
              <p>
                {s.model} ·{s.horizon}h · n={s.n} · Brier {numeric(s.brier, 4)} / {numeric(s.base_brier, 4)} · Log
                Loss {numeric(s.log_loss, 4)} / {numeric(s.base_log_loss, 4)}
              </p>
              <p className="text-muted">
                {verdict}
                {s.missing_rate !== null &&
                  (zh ? ` · 无法结算 ${percent(s.missing_rate)}` : ` · unsettled ${percent(s.missing_rate)}`)}
              </p>
            </div>
          );
        })}
        <p className="mb-2 text-xs text-muted">
          {zh
            ? "检验：同一批预测上模型与基准 Brier 之差，按连续 2 倍周期的预测分块求和、随机翻转符号（单侧）。每小时发出的长周期预测彼此重叠，普通 t 检验会高估显著性。"
            : "Test: model minus baseline Brier on the same forecasts, summed over consecutive blocks of twice the horizon with random sign flips (one-sided). Hourly forecasts with longer horizons overlap, which makes a plain t-test overconfident."}
        </p>
        {data.calibration.length > 0 && (
          <details>
            <summary className="cursor-pointer text-accent">
              {zh ? "概率校准 · 预测与实测频率" : "Calibration · predicted and observed frequencies"}
            </summary>
            {data.calibration.map((r) => (
              <p key={[r.model, r.horizon, r.cls, r.bin].join("/")} className="text-xs">
                {r.horizon}h · {r.cls} · {percent(r.predicted)} / {percent(r.observed)} · n={r.n}
              </p>
            ))}
          </details>
        )}
        <div className="max-h-96 overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {[
                  "UTC",
                  zh ? "周期" : "Horizon",
                  zh ? "跌 / 震荡 / 涨" : "Down / flat / up",
                  zh ? "实际收益" : "Realized return",
                ].map((x) => (
                  <th key={x} className={th}>
                    {x}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.forecasts.map((r) => (
                <tr key={r.id}>
                  <td className={td}>{utc(r.issued_at)}</td>
                  <td className={td}>{r.horizon}h</td>
                  <td className={td}>
                    <details>
                      <summary>{r.evidence.p.map(percent).join(" / ")}</summary>
                      <p className="text-xs text-muted">
                        {r.model} · {utc(r.cutoff)} · {zh ? "基准" : "Baseline"}{" "}
                        {r.evidence.baseline.map(percent).join(" / ")} · n={r.evidence.analog_n}/
                        {r.evidence.train_n} · Δ4h {percent(r.evidence.momentum_4h)} · ±
                        {percent(r.evidence.band)}
                      </p>
                      <Link
                        className="text-xs text-accent"
                        href={{
                          pathname: "/replay",
                          query: { coin, tab: "live", venue: "hyperliquid", frame: r.frame_id },
                        }}
                      >
                        {zh ? "当时的群体与清算证据" : "Frozen cohort and liquidation evidence"} #{r.frame_id}
                      </Link>
                      {r.evidence.reviewed_factors?.map((f) => (
                        <p className="text-xs" key={f.review}>
                          {f.hypothesis} · {f.treatment === null ? "—" : String(f.treatment)} · #{f.review}
                        </p>
                      ))}
                    </details>
                  </td>
                  <td className={td}>
                    {r.outcome?.status === "settled"
                      ? percent(r.outcome.ret)
                      : (r.outcome?.status ?? (zh ? "待到期" : "Pending"))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!data.forecasts.length && (
          <p className="text-sm text-muted">
            {zh
              ? "等待新鲜标记价与足量历史样本"
              : "Waiting for a fresh mark and sufficient historical samples"}
          </p>
        )}
      </Panel>
    </>
  );
}

async function Sources({ coin, q, zh }: { coin: string; q: Query; zh: boolean }) {
  const venue = q.source === "okx" ? "okx" : "hyperliquid",
    offset = Math.max(0, Number.parseInt(q.offset ?? "0") || 0);
  const info = await research
    .sources(Number(q.event), {
      coin,
      venue,
      offset,
      hours: Number(q.hours ?? 72),
      mode: q.mode === "as_observed" ? "as_observed" : "retrospective",
      as_of: q.vintage ?? "",
      wallet: q.wallet,
    })
    .catch(() => null);
  if (!info) return <Panel>{zh ? "该窗口的来源不可用" : "Evidence unavailable for this window"}</Panel>;
  const page = (n: number) => ({ pathname: "/replay", query: { ...q, coin, offset: n } });
  return (
    <Panel title={title(info.event, zh)} hint={`${coin} · ${venue} · ${utc(info.cutoff)}`}>
      <p className="text-xs text-muted">
        {venue === "okx"
          ? zh
            ? "数量为整仓峰值，价格为平均开仓价"
            : "Quantity is peak position size. Price is average entry."
          : zh
            ? "数量与价格来自逐笔成交"
            : "Sizes and prices refer to individual fills."}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr>
              {[
                zh ? "来源编号" : "Source ID",
                "UTC",
                zh ? "采集 UTC" : "Collected UTC",
                zh ? "账户" : "Trader",
                zh ? "方向" : "Side",
                zh ? "币数" : "Units",
                zh ? "价格" : "Price",
              ].map((k) => (
                <th className={th} key={k}>
                  {k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {info.rows.map((r) => (
              <tr key={r.address + r.reference}>
                <td className={td}>
                  <details>
                    <summary>{r.reference}</summary>
                    {r.sha256 && (
                      <small className="break-all">
                        #{r.archive_id} · SHA256 {r.sha256}
                      </small>
                    )}
                  </details>
                </td>
                <td className={td}>{utc(r.event_time)}</td>
                <td className={td}>{utc(r.known_at)}</td>
                <td className={td}>
                  <Link
                    className="text-accent"
                    href={{
                      pathname: `/trader/${r.address}`,
                      query: { rc: coin, re: r.event_time },
                      hash: "replay",
                    }}
                  >
                    {r.address.slice(0, 14)}
                  </Link>
                </td>
                <td className={td}>{r.side}</td>
                <td className={td}>{numeric(r.quantity, 6)}</td>
                <td className={td}>{numeric(r.price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!info.rows.length && (
        <p className="text-sm text-muted">
          {zh ? "此截止时间没有可用的来源记录" : "No source records available at this cutoff"}
        </p>
      )}
      <div className="mt-3 flex gap-3 text-accent">
        {offset > 0 && <Link href={page(Math.max(0, offset - 50))}>{zh ? "上一页" : "Previous"}</Link>}
        {info.has_more && <Link href={page(offset + 50)}>{zh ? "下一页" : "Next"}</Link>}
        <Link
          href={{
            pathname: "/replay",
            query: {
              coin: info.event.coin,
              event: info.event.id,
              sync: coin,
              hours: q.hours,
              mode: q.mode,
              vintage: q.vintage,
            },
          }}
        >
          {zh ? "返回回放" : "Back to replay"}
        </Link>
      </div>
    </Panel>
  );
}

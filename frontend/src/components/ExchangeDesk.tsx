import { profileLink } from "@/lib/binanceProfiles";
import { ExchangeRefresh } from "./ExchangeRefresh";
import { Link } from "@/i18n/routing";
import { exchange, exchangeLink, formatValue, windows, type Window, type Evidence } from "@/lib/exchange";
import { percent, utc } from "@/lib/research";
import { LinesChart, type Line } from "./LinesChart";
import { NoticeWatch } from "./ResearchActions";
import { Panel, td, th } from "./ui";

type Query = Record<string, string | undefined>;
function History({ e, zh }: { e: Evidence; zh: boolean }) {
  const lines: Line[] = [
    { name: zh ? "多仓金额占比" : "Long exposure share", data: [], connectNulls: false },
    { name: zh ? "近 24h 净买入（USDT）" : "24h net purchases (USDT)", axis: 1, data: [], connectNulls: false },
  ];
  const accounts: Line[] = [
    { name: zh ? "币安全部账户" : "All Binance accounts", data: [], connectNulls: false },
    { name: zh ? "币安盈利筛选组" : "Binance profit cohort", data: [], connectNulls: false },
  ];
  let previous = 0;
  for (const h of e.history) {
    const t = Date.parse(h.bucket);
    if (previous && t - previous > 300000) {
      for (const l of [...lines, ...accounts]) l.data.push([new Date(previous + 300000).toISOString(), null]);
    }
    lines[0].data.push([h.bucket, h.share]);
    lines[1].data.push([h.bucket, h.flow]);
    accounts[0].data.push([h.bucket, h.overall_accounts ?? null]);
    accounts[1].data.push([h.bucket, h.smart_accounts ?? null]);
    previous = t;
  }
  return (
    <>
      <p className="text-sm text-muted">
        {zh ? "开始归档" : "Coverage since"} {utc(e.coverage_start)} ·{" "}
        {zh ? "空档表示缺测" : "Gaps indicate missing samples"}
      </p>
      {e.history.length > 1 && (
        <LinesChart lines={lines} formats={["pct", "num"]} locale={zh ? "zh" : "en"} height={210} />
      )}
      {accounts.some(line => line.data.filter(([, value]) => value != null).length > 1) && (
        <>
          <h3 className="mt-3 text-sm font-semibold">{zh ? "两组做多人数占比的变化" : "Long-account share by cohort"}</h3>
          <LinesChart lines={accounts} formats={["pct"]} locale={zh ? "zh" : "en"} height={210} />
        </>
      )}
    </>
  );
}

export async function ExchangeDesk({ coin, q, zh }: { coin: string; q: Query; zh: boolean }) {
  const t = (cn: string, en: string) => (zh ? cn : en);
  const flow = q.view === "flow";
  const window: Window = flow ? (windows.find((w) => w === q.window) ?? "24h") : "ALL";
  const cohort = q.cohort === "whales" ? "whales" : "traders";
  const side = q.side === "short" ? "short" : q.side === "long" ? "long" : "all";
  const order = q.order === "ASC" ? "ASC" : "DESC";
  const page = Math.min(10000, Math.max(1, Math.floor(Number(q.page) || 1)));
  const vintage = q.as_of && /^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(q.as_of) ? q.as_of + "Z" : q.as_of;
  const frozen = Boolean(q.observation || vintage);
  const [e, list] = await Promise.all([
    exchange.evidence(coin, q.observation, vintage).catch(() => null),
    frozen ? null : exchange.page(coin, { window, cohort, side, order, page }).catch(() => null),
  ]);
  const obs = e?.observation,
    sample = obs?.readings[window],
    group = sample?.data[cohort];
  const state = {
    view: flow ? "flow" : "positions",
    window,
    cohort,
    side,
    order,
    ...(q.observation ? { observation: q.observation } : {}),
    ...(q.as_of ? { as_of: q.as_of } : {}),
  };
  const href = (extra: Record<string, string | number>) => exchangeLink(coin, { ...state, ...extra });
  const choose = (key: string, label: string, active: boolean, extra: Record<string, string | number>) => (
    <Link
      prefetch={false}
      key={key}
      aria-current={active ? "true" : undefined}
      className={`rounded border px-3 py-2 text-sm ${active ? "border-accent bg-panel-2 font-semibold" : "border-border text-muted"}`}
      href={href(extra)}
    >
      {label}
    </Link>
  );
  const allAccounts = obs?.readings.binance_accounts;
  const smartPositions = obs?.readings.ALL;
  const smartShare = smartPositions?.data.traders.head_share;
  const comparable =
    allAccounts &&
    !allAccounts.stale &&
    smartPositions &&
    !smartPositions.stale &&
    smartShare != null &&
    Math.abs(Date.parse(allAccounts.data.source_at) - Date.parse(smartPositions.at)) <= 600000;
  const opposite = comparable && (allAccounts.data.long_share - 0.5) * (smartShare - 0.5) < 0;
  const fmt = (v: number | null | undefined, compact = false) => formatValue(v, zh, compact);
  const tag = (s: "long" | "short") =>
    flow
      ? s === "long"
        ? t("净买入", "Net buys")
        : t("净卖出", "Net sells")
      : s === "long"
        ? t("持有多仓", "Long positions")
        : t("持有空仓", "Short positions");
  return (
    <div className="min-w-0 space-y-4">
      {!frozen && <ExchangeRefresh />}
      <Panel
        title={t("币安近 30 天盈利筛选组", "Binance 30-day profit cohort")}
        hint={t("巨鲸已计入全部入选交易员。", "Whales are included in the full cohort.")}
      >
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2">
            {choose("t", t("全部入选交易员", "All selected traders"), cohort === "traders", {
              cohort: "traders",
            })}
            {choose("w", t("其中的巨鲸", "Whale subset"), cohort === "whales", { cohort: "whales" })}
          </div>
          <div className="flex flex-wrap gap-2">
            {choose("pos", t("当前持仓", "Positions"), !flow, {
              view: "positions",
              window: "ALL",
              side: "all",
            })}
            {choose("flow", t("最近交易", "Recent activity"), flow, {
              view: "flow",
              window: "24h",
              side: "all",
            })}
          </div>
          {flow && (
            <div className="flex flex-wrap gap-2">
              {windows.map((w, i) =>
                choose(
                  w,
                  [
                    t("30 分钟", "30 min"),
                    t("1 小时", "1 hour"),
                    t("24 小时", "24 hours"),
                    t("7 天", "7 days"),
                  ][i],
                  window === w,
                  { window: w },
                ),
              )}
            </div>
          )}
          <p>
            {flow
              ? t(
                  "净买入可能是开多或平空；净卖出可能是开空或平多。",
                  "Buys can open longs or close shorts. Sells can open shorts or close longs.",
                )
              : t("这里展示目前仍持有的仓位。", "These positions remain open.")}
          </p>
          <p className="text-xs text-muted">
            {t("名单刷新", "Roster updated")} {utc(obs?.readings.roster?.data.updated)} ·{" "}
            {t("采集", "Collected")} {utc(sample?.at)} {sample?.stale && t("· 已过期", "· Stale")}
          </p>
          {!frozen && <Link className="text-sm text-accent" href={profileLink(coin, cohort)}>{t("查看开仓成本与强平价", "Explore entry and liquidation prices")} →</Link>}
          <div className="grid gap-3 sm:grid-cols-2">
            {(["long", "short"] as const).map((s) => (
              <div key={s} className="rounded border border-border p-3">
                <strong>{tag(s)}</strong>
                <p>
                  {fmt(group?.[s].n)} {t("人", "traders")} · {fmt(group?.[s].usd, true)} USDT
                </p>
                <small>
                  {fmt(group?.[s].qty)} {coin}
                </small>
              </div>
            ))}
          </div>
        </div>
      </Panel>
      <Panel title={t("交易员明细", "Trader details")}>
        {frozen ? (
          <p>
            {t(
              "此档案保存了群体汇总。实时分页明细另行查看。",
              "This archive contains cohort totals. Individual pages are available in live mode.",
            )}{" "}
            <Link
              prefetch={false}
              className="text-accent"
              href={exchangeLink(coin, { view: state.view, window, cohort, side, order })}
            >
              {t("打开实时明细", "View live details")} →
            </Link>
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              {(["all", "long", "short"] as const).map((s) =>
                choose(s, s === "all" ? t("全部方向", "All sides") : tag(s), side === s, { side: s }),
              )}
              <details className="text-sm">
                <summary className="cursor-pointer p-2">
                  {order === "DESC" ? t("最近交易优先", "Newest first") : t("最早交易优先", "Oldest first")}
                </summary>
                <div className="flex gap-1 py-2">
                  {choose("desc", t("最近优先", "Newest"), order === "DESC", { order: "DESC" })}
                  {choose("asc", t("最早优先", "Oldest"), order === "ASC", { order: "ASC" })}
                </div>
              </details>
            </div>
            {list ? (
              <>
                <p className="my-3 text-xs text-muted">
                  {t("共", "Total")} {list.total}{" "}
                  {t("条记录 · 匿名合计可能覆盖多人", "records · Anonymous totals can include many traders")} ·{" "}
                  {utc(list.collected_at)}
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr>
                        {[
                          t("交易员", "Trader"),
                          t("方向", "Side"),
                          "USDT",
                          coin,
                          ...(flow ? [] : [t("平均开仓价", "Average entry")]),
                          t("最近交易", "Last trade"),
                        ].map((v) => (
                          <th key={v} className={th}>
                            {v}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {list.rows.map((r, i) => (
                        <tr key={r.public_id ?? i} className="border-t border-border">
                          <td className={td}>
                            {r.public_id ? (
                              <>
                                {r.name ?? t("公开交易员", "Public trader")}
                                <small className="block text-muted">Binance · {r.public_id}</small>
                                {/^[0-9]{19}$/.test(r.public_id) && <Link prefetch={false} className="text-xs text-accent"
                                  href={{ pathname: "/settings", query: { profile: r.public_id }, hash: "binance-profiles" }}>
                                  {t("配置主页观察", "Configure profile observation")} →
                                </Link>}
                              </>
                            ) : (
                              <>
                                {t("匿名交易员合计", "Anonymous group")} · {r.n} {t("人", "traders")}
                              </>
                            )}
                          </td>
                          <td className={td}>{tag(r.side === "BUY" ? "long" : "short")}</td>
                          <td className={td}>{fmt(r.usd, true)}</td>
                          <td className={td}>{fmt(r.qty)}</td>
                          {!flow && <td className={td}>{fmt(r.entry)}</td>}
                          <td className={td}>{utc(r.time)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {flow && (
                  <small className="text-muted">
                    {t("— 表示币安未披露这行的币数。", "A dash indicates an undisclosed quantity.")}
                  </small>
                )}
                <nav className="mt-3 flex gap-3">
                  {page > 1 && choose("prev", t("上一页", "Previous"), false, { page: page - 1 })}
                  {page * 10 < list.total && choose("next", t("下一页", "Next"), false, { page: page + 1 })}
                </nav>
              </>
            ) : (
              <p className="mt-3 text-muted">
                {t("币安明细暂不可用", "Binance details currently unavailable")}
              </p>
            )}
          </>
        )}
      </Panel>
      <Panel
        title={t("比较不同群体", "Compare cohorts")}
        actions={obs && <NoticeWatch resource="exchange" id={obs.id} watching={obs.watching} zh={zh} />}
      >
        <p className="mb-2 text-xs text-muted">
          {t(
            "整体账户按人数统计。头部账户由交易所按规模筛选；专项交易员由 AlphaScope 按本币历史表现匹配。",
            "Overall accounts are counted by head. Exchanges select top accounts by size. AlphaScope matches specialists by instrument evidence.",
          )}
        </p>
        {comparable && (
          <p className="mb-3 text-sm">
            {opposite
              ? t("两组账户持仓方向相反。", "The two account groups lean in opposite directions.")
              : t("同一合约的做多人数占比", "Long-account shares for the same contract")}{" "}
            Binance {coin}USDT · {t("整体", "Overall")} {percent(allAccounts.data.long_share)} ·{" "}
            {t("盈利组", "Profit cohort")} {percent(smartShare)} · {t("盈利组减整体", "Cohort minus overall")}{" "}
            {fmt((smartShare - allAccounts.data.long_share) * 100)} {t("个百分点", "pp")}
          </p>
        )}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr>
                {[
                  t("来源与群体", "Source and cohort"),
                  t("做多人数占比", "Long accounts"),
                  t("多仓金额占比", "Long exposure"),
                  t("样本人数", "Traders"),
                  t("更新时间", "Updated"),
                ].map((x) => (
                  <th className={th} key={x}>
                    {x}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(["binance", "okx"] as const).flatMap((v) =>
                (["accounts", "top_accounts", "top_positions"] as const).map((k) => {
                  const r = obs?.readings[`${v}_${k}`],
                    bySize = k === "top_positions";
                  return (
                    <tr key={v + k}>
                      <td className={td}>
                        {v.toUpperCase()} ·{" "}
                        {k === "accounts"
                          ? t("全部账户", "All accounts")
                          : v === "binance"
                            ? t("保证金前 20%", "Top 20% by margin")
                            : t("持仓规模前 5%", "Top 5% by position value")}
                      </td>
                      <td className={td}>{bySize || !r ? "—" : t(`每 100 个约 ${Math.round(r.data.long_share * 100)} 个持多`, `About ${Math.round(r.data.long_share * 100)} per 100 long`)}</td>
                      <td className={td}>{bySize ? percent(r?.data.long_share) : "—"}</td>
                      <td className={td}>{t("交易所未披露", "Undisclosed")}</td>
                      <td className={td}>
                        {r ? utc(r.data.source_at) : t("等待数据", "Awaiting data")}{" "}
                        {r?.stale && t("已过期", "Stale")}
                      </td>
                    </tr>
                  );
                }),
              )}
              {(["traders", "whales"] as const).map((c) => {
                const pos = obs?.readings.ALL,
                  g = pos?.data[c];
                return (
                  <tr key={c}>
                    <td className={td}>
                      Binance ·{" "}
                      {c === "whales"
                        ? t("其中的巨鲸", "Whale subset")
                        : t("近 30 天盈利筛选组", "Profit cohort")}
                    </td>
                    <td className={td}>{percent(g?.head_share)}</td>
                    <td className={td}>{percent(g?.money_share)}</td>
                    <td className={td}>{g ? g.long.n + g.short.n : "—"}</td>
                    <td className={td}>
                      {utc(pos?.at)} {pos?.stale && t("已过期", "Stale")}
                    </td>
                  </tr>
                );
              })}
              {e?.groups.flatMap((f) =>
                (["specialist", "control"] as const).map((k) => (
                  <tr key={f.id + k}>
                    <td className={td}>
                      <Link
                        prefetch={false}
                        className="text-accent"
                        href={{
                          pathname: "/replay",
                          query: { coin, tab: "live", venue: f.venue, frame: f.id },
                        }}
                      >
                        {f.venue} ·{" "}
                        {k === "specialist" ? t("本币专项", "Specialists") : t("匹配对照", "Matched peers")} ↗
                      </Link>
                    </td>
                    <td className={td}>{percent(f[k].long_share)}</td>
                    <td className={td}>{percent(f[k].long_notional_share)}</td>
                    <td className={td}>{f[k].n}</td>
                    <td className={td}>{utc(f.available_at)}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </div>
        {obs && (
          <Link className="text-accent text-sm" href={exchangeLink(coin, { observation: obs.id })}>
            {opposite
              ? t("查看这次分歧", "Inspect this divergence")
              : t("查看本次观察", "Inspect this observation")}{" "}
            #{obs.id} →
          </Link>
        )}
      </Panel>
      <Panel title={t("归档与此前变化", "Archive and prior changes")}>
        {e && <History e={e} zh={zh} />}
        <form className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <input type="hidden" name="coin" value={coin} />
          <input type="hidden" name="tab" value="exchange" />
          <label>
            {t("查看时点（UTC）", "As of (UTC)")}{" "}
            <input
              className="rounded border border-border bg-panel-2 p-2"
              type="datetime-local"
              name="as_of"
              required
              defaultValue={q.as_of?.slice(0, 16)}
            />
          </label>
          <button className="text-accent">{t("查看归档", "Open archive")} →</button>
        </form>
        {frozen && (
          <p className="mt-2">
            {obs
              ? `#${obs.id} · ${utc(obs.published_at)}`
              : t("该时点没有可用归档", "No archive available at this time")}
          </p>
        )}
        {e?.saved.map((r) => (
          <Link
            prefetch={false}
            key={r.id}
            className="mr-3 text-xs text-accent"
            href={exchangeLink(coin, { observation: r.id })}
          >
            {t("已保存", "Saved")} #{r.id} · {utc(r.published_at)}
          </Link>
        ))}
      </Panel>
    </div>
  );
}

# 00 AlphaScope 代码审计（Newsroom 的数据基础）

审计对象：仓库 HEAD `1bdfdca`（2026-11-22），逐个阅读了源码，而不是只看 README。下文所有路径相对仓库根目录。
结论分为“已确认”和“待核实”；没有在代码里找到的能力一律标为“不存在”，不做推测。

## 1. 结论摘要

| 问题 | 结论 |
|---|---|
| 有没有可直接读取的事件流 | 有：`market_event`（`backend/app/models.py:990`），只追加，BIGSERIAL id，覆盖整个雷达样本（约 1.4 万 HL 钱包 + 约 1.8k OKX 交易员） |
| 事件时间准不准 | HL 只有轮询窗口精度（`time_precision=window`，窗口 30 分钟到 12 小时）；只有 OKX 上报 cTime 时是 `exact`（`backend/app/market/observe.py:83`） |
| 首次观测是不是开仓 | 不是。钱包第一次被轮询时已有的仓位记为 `first_seen`，从不计为开仓（`observe.py:6, 78`） |
| 有没有精确开仓时间和已实现盈亏 | 只有已同步成交的钱包（约 2k 个）通过 `position_cycle` / `trade_decision` 提供，且只有 `complete=true` 的周期可信；成交最多滞后约 24 小时，最早到 2026-07-01 |
| 强平 | 确认强平只来自 `fill.liquidation`（HL）和 `okx_position.liquidated`；`/market/liquidated` 是推断（平仓价在最后报告强平价 0.5% 以内，`backend/app/api/market.py:40`） |
| 有没有增量导出接口 / outbox | 没有（本次新增了只读 Feed API，见 03-feed-contract-v1.md） |
| API 有没有鉴权 | 没有，只靠监听 127.0.0.1:8610 |
| 交易所 API 预算 | HL 已用 1140/1200 权重/分钟/IP（`backend/app/config.py` 注释）；Newsroom 不能再调用任何交易所 |
| 服务器资源 | 6 核 / 11 GB；AlphaScope worker 上限 3 GB，生产上每天 OOM 数次。Newsroom 三个服务合计上限 1.3 GB |

## 2. 可复用能力清单

| 能力 | 源码 | 表 | 现有 API | 可信度说明 |
|---|---|---|---|---|
| 雷达持仓变化事件 | `market/observe.py`, `market/collect.py` | `market_event` | `GET /api/v1/market/events`（无游标） | 时间精度见上；`notional_change` = \|Δ仓位\| × 观测时标记价，不是成交额 |
| 当前持仓 | `market/observe.py:144` | `trader_position`（每次轮询覆盖） | `/market/contributors` 等 | 只有最新一份；`opened_at` 对 HL 永远为空 |
| 轮询新鲜度 | `market/observe.py:20-28` | `position_watch` | `/market/overview` 的 coverage | `stale_after = 下次轮询 + 2×间隔`；雷达扫描不写 `ingest_run` |
| 钱包画像 | `ingest/leaderboard_ingest.py` | `wallet` | `/api/v1/wallets/{a}` | `first_seen_at` 是上排行榜时间；`quality_flags`、`enrichment_flags`（`aggregate_account` 表示账户值不能做比率） |
| TWR / 回撤 | `quant/returns.py`（r1，Modified Dietz） | `wallet_metric`（只保留最新） | `/wallets/{a}` | 粗粒度点，带 `reliability`；无 Sharpe |
| 成交与周期重建 | `ingest/fill_sync.py`, `research/positions.py` | `fill`, `fill_sync`, `position_cycle`, `trade_decision` | `/wallets/{a}/positions` | 周期 id 每次重建都会变（`positions.py:259-260` 先删后插） |
| 确认强平 | `factors/run.py:34-42` | `fill.liquidation` | 无 | 仅成交已同步钱包 |
| 疑似强平 | `api/market.py:264-290` | `market_event` | `/market/liquidated` | 推断，不是交易所记录 |
| 强平风险 | `alerts/diff.py:54`, `api/market.py:238` | `trader_position` | `/market/at-risk` | 只用交易所报告的强平价 |
| OKX 带单历史 | `okx/positions.py` | `okx_position`, `okx_trader` | `/okx/board`, `/wallets/{okx:..}` | 只有 SWAP；整仓记录，无加减仓；收益只对方向有效（`factors/copy_validation.py:137`） |
| 风格 / 择时 / 跟单评分 | `factors/styles.py`, `research/timing.py`, `factors/copyability.py` | `style_tag`, `timing_score`, `copy_score`, `trader_health` | `/discover`, `/timing`, `/copy-board` | 每次全量替换，无历史；择时为后验评分，带 BH q 值 |
| 观测样本聚合 / 分歧 | `market/stats.py`, `market/divergence.py` | `market_snapshot` | `/market/*` | 明确标注“观测样本，不是全市场”；timing 组与全样本重叠 |
| 研究帧（BTC/ETH） | `research/frames.py` | `research_frame` | `/research/*` | 只覆盖 BTC/ETH |
| 币安数据 | `ingest/binance_*.py`, `research/exchange.py` | `market_candle`, `binance_metric`, `exchange_observation` | `/research/exchange/*` | `binance_metric` 无 API；分享档案轮询默认关闭 |

## 3. 字段语义表（Newsroom 写作规则的来源）

| 字段 | 含义 | 精度 / 新鲜度 | 允许的写法 | 禁止的写法 |
|---|---|---|---|---|
| `market_event.event_time` + `time_precision=window` | 两次轮询之间某时发生 | 窗口 = `window_start`..`observed_at` | “北京时间 X 至 Y 之间加仓” | “于 X 点开仓” |
| `market_event.kind=first_seen` | 第一次轮询时已持有 | 开仓时间未知 | “首次被观察到时已持有” | “新开 / 刚开仓” |
| `notional_change` / `notional_usd` | 仓位 × 标记价 | 观测时刻 | “名义价值约 X” | “投入 / 本金 / 保证金 X” |
| `notional / leverage` | 保证金估算（全仓时不准） | 估算 | “按杠杆估算保证金约 X” | 当作事实保证金 |
| `trader_position.upl` | 未实现盈亏 | 观测时刻 | “浮盈 / 浮亏 X” | “已实现 / 落袋 / 锁定利润” |
| `position_cycle.realized_pnl` | 已实现（扣费前） | 只在 `complete=true` 时可信 | “已完成周期的净已实现盈亏” | 用不完整周期、截断档案做战绩 |
| `/market/liquidated` | 推断强平 | 0.5% 容差 | “疑似被强平” | “被强平 / 爆仓”（无记录时） |
| `fill.liquidation` | 交易所强平记录 | 精确 | “被强平（交易所记录）” | — |
| 雷达聚合 | 观测样本 | 新鲜持仓 | “AlphaScope 观测样本中” | “全市场 / 所有交易者” |
| 钱包地址 | 公开地址 | — | 短地址、平台昵称（注明为自选昵称） | 猜测所有者、暗示内幕 |

## 4. 数据可信度问题（已核实）

1. `market_event.id` 可能乱序提交：每次观测是独立短事务（`collect.py:95-96`），按 id 游标需要滞后读取并定期回放。
2. 雷达 `clearinghouseState` 原始响应不归档，只有每周一次的增强快照归档原文。
3. HL 没有按轮询频率保存的持仓历史，只有变化事件 + 每周快照。
4. 成交覆盖有限且滞后：只有 watchlist / 波段候选 / 宏观扫描钱包，24 小时重同步，可能被截断（`fill_sync.truncated`）。
5. `wallet_metric`、`style_tag`、`timing_score`、`copy_score` 都只保留最新结果，无法回看当时的评分。
6. 文档与代码不一致：`market_snapshot` 注释说可重跑更新，实际是 `ON CONFLICT DO NOTHING`（`market/stats.py:132`）；择时文档写 1000 次平移、代码是 500 次。
7. 生产环境：worker 每天 OOM 约 5 次；约 30% 研究帧被启动补跑抢桶（2026-11-22 实测，见项目记忆）。

## 5. 新增的数据结构与接口

- AlphaScope 侧只新增 `backend/app/api/feed.py`（只读、带令牌、契约版本 `feed.v1`），不改表、不加索引、不新增采集。
- Newsroom 有自己的数据库 `newsroom`（见 04-data-model.md），通过 Feed 增量同步，不直接读 AlphaScope 的表。

## 6. 对原系统的影响

| 方面 | 影响 |
|---|---|
| 表结构 | 无 |
| 采集频率 / 交易所预算 | 无（Feed 只读数据库） |
| API 进程负载 | Feed 查询都走主键或已有索引，5 秒语句超时；trader context 每小时限 240 次（`NEWSROOM_CONTEXT_CALLS_PER_HOUR`） |
| 部署 | `ensure_env_file` 首次生成 `ALPHASCOPE_FEED_TOKEN`；设为空即关闭 Feed（返回 404） |
| 故障隔离 | Newsroom 停机不影响 AlphaScope；AlphaScope 停机时 Newsroom 标记数据源不可达、事件被可信度门阻断 |

## 7. 待核实事项

- 生产库 `app_setting.binance_profiles` 是否启用（代码默认关闭）。
- 生产库 `market_event` 每小时实际增量（决定同步页大小；当前每分钟最多 20 页 × 1000 行）。
- 第三方 AI 网关（如 aicodemirror）支持的协议与 JSON 模式：用系统设置里的“测试模型”逐个确认。

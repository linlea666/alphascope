# 03 AlphaScope 只读 Feed 契约 v1

实现：`backend/app/api/feed.py`；契约测试：`backend/tests/test_feed.py`；共享键集合：`backend/tests/fixtures/feed_v1_contract.json`（Newsroom 的 `nr/feed/contract.py` 与之逐字一致，`newsroom/backend/tests/test_sync.py` 会比对）。

## 通用规则

- 只有 GET；前缀 `/api/v1/feed`；只监听 127.0.0.1（随 AlphaScope API）。
- 鉴权：`Authorization: Bearer $ALPHASCOPE_FEED_TOKEN`，常量时间比较。令牌为空时所有路由返回 404（等于不存在）；令牌错误返回 401。
- 每个请求在只读事务中执行（`SET TRANSACTION READ ONLY`），语句超时 5 秒。
- 所有数值以十进制字符串返回，保证事实包中的数字与数据库一致；时间为 ISO 8601（UTC）。
- 交易员用 `trader_key`（HL 地址或 `okx:<authorId>`）标识，从不返回会因重建而变化的内部 id。
- 每个响应带 `schema: "feed.v1"` 和 `as_of`。只允许新增字段；删除或改名要发 v2。

## 端点

### `GET /meta`
规则常量：`settle_seconds`、`liq_tolerance`（与 `/market/liquidated` 相同的 0.005）、`stale_factor`、各层轮询间隔、雷达健康阈值。Newsroom 启动时读取，保持规则一致。

### `GET /events`
参数：`after_id`（必填）、`limit`≤1000、`scan`≤5000、`include_first_seen`、`min_notional`、`venue`。

按主键升序扫描 `market_event JOIN wallet`。**遇到第一条 `observed_at >= now - settle_seconds` 的行就停止**，之后的行即使更旧也不返回——因为观测是并发提交的，较小的 id 可能晚于较大的 id 才可见，跳过会丢数据。过滤条件会推进游标；`limit` 截断时不会跳过未返回的匹配行。

返回：`next_after_id`（下一次的游标）、`head_id`（最大 id，用于计算积压）、`has_more`、`rows[]`（`source_id, trader_key, venue, display_name, handle, coin, kind, side, szi_before, szi_after, notional_change, mark_px, entry_px, leverage, liq_px, event_time, time_precision, window_start, observed_at`）。

不需要新索引：只用主键范围扫描。客户端定期从约 60 分钟前的旧游标回放，插入按 `(source, source_event_id)` 幂等，作为兜底。

### `GET /traders/{trader_key}/context`
参数：`coin`、`cycles`≤200、`okx_closes`≤200、`event_days`≤30、`liq_days`≤60。无效 key → 422，不存在 → 404。

| 块 | 来源 | 用途 |
|---|---|---|
| `trader` | `wallet` | 账户值、质量标记、排行榜盈亏 |
| `watch` | `position_watch` | `is_fresh`（stale_after > now）→ 门禁 |
| `positions` | `trader_position` | 当前持仓，含 `opened_at_source`、估算保证金 |
| `recent_events` | `market_event`（唯一约束索引前缀） | 重建 Newsroom 上线前开始的回合 |
| `metrics` | `wallet_metric` | TWR / 回撤，带 `reliability` |
| `quality` | `style_tag`, `timing_score`(coin_group=all), `copy_score`, `trader_health` | 战绩判断 |
| `fill_coverage` | `fill_sync`；OKX 用 `okx_trader` 同样的映射 | 历史统计是否可用 |
| `cycles` | `position_cycle`（自然键 coin+direction+opened_at+version） | 完整周期统计、连胜 |
| `confirmed_liquidations` | `fill.liquidation`（`liquidatedUser` = 本地址） | 强平确认 |
| `okx` | `okx_position` 平仓、汇总、`okx_trader` | OKX 历史与强平 |

### `GET /positions/at-risk`
参数：`max_dist`≤0.2、`min_notional`、`venue`、`limit`≤500。只返回新鲜持仓、只用交易所报告的强平价（与 `/market/at-risk` 同一 SQL 逻辑）。

### `GET /health`
雷达（HL / OKX）用 `stats.coverage()` 计算 `fresh_ratio` 与 `status`（ok / lagging / stale / empty，阈值随响应返回）；另含 `market_event` 头部、各 `ingest_run` 种类最近成功、成交归档 / OKX 历史 / 跟单评分的时间戳。雷达扫描不写 `ingest_run`，所以雷达健康只能从 `position_watch` 判断。

## 研究栏目端点（v1 追加，只读）

| 端点 | 来源 | 栏目 |
|---|---|---|
| `GET /copy/board?window=7d|30d|90d` | 最新完整 OKX 榜单 + `copy_backtest`（`default-v2`），附回测假设 | N1 |
| `GET /okx/dropouts?days&max_best_rank` | `okx_presence`（7d 榜，已离榜）+ 离榜后的 `okx_position` 平仓、已实现、强平 | N2 |
| `GET /market/structure?coin` | 与 `/market/structure` 同一计算：入场区间、强平阶梯（只用交易所报告的强平价），附覆盖率 | N3 |
| `GET /research/forecasts?coin&days` | `research_forecast` 按模型 / 周期汇总：已结算、缺失、Brier 与基准、分块符号检验 p | N4 |
| `GET /research/funding?coin&hours` | 最新资金费率及其历史排位 + `research_frame` 专家组 / 匹配对照组多头占比与分歧 | N5 |
| `GET /market/groups?coin` | 最新一小时与 7 天前的 `market_snapshot`：各交易所、各 cohort 的多空人数与名义价值 | 多空对决 |
| `GET /cycles/holding?days` | 完整已平仓周期的持仓时长分位数，盈利 / 亏损分开，按级别 | N6 |
| `GET /cycles/closed?hours&min_streak` | 最近平仓的完整周期 + 之前的连胜数及其起点是否已知（自然键） | 连胜终结者 |

## 部署

`scripts/deploy-common.sh` 的 `ensure_env_file` 在 `data/alphascope.env` 没有 `ALPHASCOPE_FEED_TOKEN` 这一行时生成一个随机令牌；运维人员把它改成空值即关闭 Feed。Newsroom 安装脚本只读取这个值，从不修改 AlphaScope 的配置。

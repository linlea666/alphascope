# 06 事实包 fp.v1 与事实守卫 g1

## 1. 事实包

代码：`nr/facts/builder.py`（构建）、`nr/facts/fmt.py`（展示串）、`nr/facts/build.py`（任务与版本）。

```json
{"schema": "fp.v1", "version": 3, "event_id": 123,
 "source": {"system": "alphascope", "feed_schema": "feed.v1", "source_event_ids": [991, 1004], "context_as_of": "..."},
 "subject": {"trader_ref": "0xabcd…1234", "trader_key": "0x…", "venue": "hyperliquid", "identity": "unknown"},
 "event": {"class": "whale_add", "coin": "BTC", "direction": "long", "escalation_level": 2, "liquidation_status": "none"},
 "facts": [{"id": "position.notional_usd", "kind": "notional", "value": "12345678.9", "unit": "USD",
            "source": "context.positions.notional_usd", "display": {"zh": ["1234.57万美元", "1235万美元"], "en": ["$12.35M", "$12.3M"]}}],
 "constraints": [{"code": "PNL_UNREALIZED", "text": "..."}],
 "gate": {"status": "pass", "blocking": []}}
```

规则：
- 每个数字由代码计算，带来源路径；展示串由 `fmt.py` 确定性生成。AI 和人工都只能引用这些值。
- 覆盖不足就不出：成交档案未同步完、被截断或没有起点时，不出任何历史统计（`HISTORY_UNAVAILABLE`）；连胜只在“连胜之前有一次亏损”时才出（否则起点未知）；账户值不可靠时不出比率；只有窗口时不出精确时间。
- OKX 资产是带单账户资产，不是全部资金（`OKX_LEAD_ASSETS`）。
- **只在实质变化时出新版本**：事件类型 / 档位 / 强平状态变化、门禁变化、新增或消失的事实、文本 / 时间 / 计数变化、数值变化 ≥10%。只随标记价波动的值（标记价、未实现盈亏、强平距离、观测时间、账户值）不单独触发新版本。
- 新版本产生时：引用旧版本的未发布稿件变为“需重新核验”；引用了变化事实的已发布帖子列入 `correction_record`，由编辑处理（确认 / 已更正 / 无需处理）。

约束码：

| 代码 | 写作要求 |
|---|---|
| `OBSERVED_SAMPLE` | 只能说“AlphaScope 观测样本”，不能说全市场 |
| `NO_IDENTITY` | 不猜测、不暗示账户归属 |
| `NOTIONAL_NOT_MARGIN` | 名义价值不是本金或保证金 |
| `PNL_UNREALIZED` | 浮盈浮亏必须标明未实现 |
| `TIME_WINDOW_ONLY` | 只能写“之间”，不能写精确时刻 |
| `FIRST_SEEN_NOT_OPEN` | 不能写“新开 / 刚开仓” |
| `LIQ_SUSPECTED_ONLY` | 同一句里必须有“疑似 / suspected” |
| `PNL_ESTIMATED_FROM_MARK` | 平仓盈亏是按观测价估算 |
| `ACCOUNT_RATIO_UNAVAILABLE` | 不能把仓位和账户值相比 |
| `HISTORY_UNAVAILABLE` | 不能谈历史战绩和连胜 |
| `OKX_LEAD_ASSETS` | OKX 资产只是带单账户 |

## 2. 事实守卫（硬门，不可绕过）

代码：`nr/guard/`。每次保存版本、审核通过前（用当前事实包重跑）、记录发布时（对实际发布文本）都会运行。失败就不能通过审核和发布；**没有“强制通过”按钮**。误报的处理方式是补充事实或改写。

| 检查 | 规则 |
|---|---|
| 数字 | 识别 万/亿/K/M/B、%、倍/x、$、日期、时刻；每个数字必须匹配事实包里的值：误差不超过最后一位的半个单位，金额还要在 5% 以内（“1 亿”不能代表 1.4 亿）。币种代号、@用户名、地址、URL 不当作数字；白名单只有 24h/7d/30d/90d 这类周期 |
| 日期与时刻 | 必须是事实包里某个时间（UTC 或北京时间）的日期 / 时刻 |
| 强平 | 无强平事实时出现“爆仓 / 被强平 / liquidated”→ 失败；只有疑似时同一句没有限定词 → 失败（“爆仓风险”“强平价”不算） |
| 盈亏 | 只有未实现盈亏时出现“已实现 / 落袋 / banked”→ 失败；提到浮盈浮亏的金额却没标“浮 / 未实现 / unrealized”→ 失败 |
| 时间 | 只有窗口时出现“于 16:15 加仓”且同句无“之间 / between”→ 失败 |
| 开仓 | 首次观测的仓位写“新开 / 刚开 / opened”→ 失败（“开仓均价”不算） |
| 保证金 | “本金 / 保证金 / 投入 / staked” 与金额同句，且不是带“估算”的保证金估算值 → 失败 |
| 全市场 | “全市场 / 全网 / whole market / everyone” → 失败 |
| 身份与内幕 | “疑似某机构 / insider / belongs to” → 失败 |
| 建议 | “稳赚 / 建议买入 / guaranteed / should buy” → 失败 |
| 风格禁用语 | 账号风格的“去 AI 腔”清单 → 提醒 |
| 引用 | 引用了包里没有的事实 → 失败；稿件基于旧事实包 → 失败 |
| 长度 | X 加权长度（中日韩字符算 2、URL 算 23）超过 280 且账号不允许长帖 → 失败 |
| 重复 | 72 小时内同语种稿件 / 已发帖子：中文字 3-gram、英文词 3-gram Jaccard ≥ 0.45 失败，0.30–0.45 需审核人确认（审计） |

已知局限（正视而不是隐藏）：
- 守卫只能检查可识别的模式；“暗示性”表述（例如语气夸张但不含禁用词）仍需人工审核。
- 不同语言的本地化稿件不做跨语言相似度比较；是否构成有增量的本地化由编辑判断。
- 汉字数字只在带单位时识别（“三千万美元”“二十倍”“三个百分点”）；不带单位的汉字数字（“三千万的仓位”）仍需人工审核。

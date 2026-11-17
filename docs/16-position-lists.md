# 16 · 持仓列表与杠杆跟随（M4.1）

## 市场雷达的持仓列表（`/market?view=…`，同样按交易所和分群筛选）

| 视图 | 接口 | 口径 |
|---|---|---|
| 持仓明细排序 | `/market/contributors?sort=notional\|margin\|upl\|liq\|leverage\|opened` | 保证金 = 名义 ÷ 报告的杠杆（全仓为估算）；距强平 = \|现价 − 强平价\| ÷ 现价；ROE = 浮盈 ÷ 保证金 |
| 爆仓预警 | `/market/at-risk?max_dist&side&min_notional` | 全币种按距强平价排序；汇总 ≤2% / ≤5% 的多空名义 |
| 疑似强平 | `/market/liquidated?hours` | 平仓事件中价格已到达（0.5% 内）或越过该仓位上次报告的强平价 → 推断强平。平仓事件从本版本起保留最后的开仓价、杠杆、强平价，所以只统计更新之后的平仓 |
| 大额开仓 | `/market/events?kinds=open,flip,increase&min_notional` | 两次观测之间的开仓 / 加仓 / 反手 |
| 扛单榜 | `/market/underwater?adding_only` | 当前浮亏最大的仓位；72 小时内价格比开仓均价更差时仍加仓的次数和金额 |
| 巨鲸动向 | `/market/whales?hours` | 名义最大的仓位及其窗口内净加减（开 / 加为正，减 / 平为负），和窗口内平掉的大仓位 |

## 杠杆跟随

- 全仓模式新增「和交易员仓位比例保持一致（不限制总敞口）」：最高杠杆设为 125，按比例跟单的敞口倍数与交易员一致；账户维持保证金不足仍整体强平。
- 每次调仓记录是否被杠杆上限压缩（`copy_sim.state.capped_cycles / sized_cycles`），模拟详情显示占比。
- Hyperliquid 每个币的最大杠杆由实时监控每小时写入 `coin_limit`（迁移 0015）；历史回测按交易员所在交易所取币种上限（另一交易所补缺，最后才用默认 20 倍），逐仓强平价因此与真实上限一致。

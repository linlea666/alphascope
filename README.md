# AlphaScope — Hyperliquid Trader Intelligence

寻找**可验证、风险调整后、可复制**的交易优势，而不是按 ROI 排名。

> 当前阶段：**P0** + 大周期研究第一阶段。设计文档见 [docs/](docs/)，从 [docs/00-feasibility.md](docs/00-feasibility.md) 开始读。

## BTC / ETH 市场行为研究

首页并列展示两币种，`/zh/replay` 支持历史事件对比、逐笔来源、固定群体仓位、清算暴露和前瞻概率档案。研究页和数据健康页提供实际覆盖区间。事件归因与前瞻检验分别标记数据截止时间。[使用方法和统计口径](docs/18-market-replay.md) · [研究基线](docs/17-market-research-baseline.md)

## 已实现（P0）

- 官方排行榜全量导入（约 4.8 万钱包）、条件请求、原始响应归档、结构变化即失败
- 新钱包发现 / 消失检测，排行榜宽表快照（每 6 小时）
- 数据质量标记（零净值、微尘、小分母 ROI、极端 ROI、被动盈亏、子账户汇总）
- 资源分层 Z / C / B（规则带版本，变化留痕）
- B 层钱包按 API 权重预算增强：portfolio 曲线 + 当前持仓
- 资金流调整 TWR / MDD（Modified Dietz），每个指标附覆盖范围、点数、精度与可信度
- FastAPI 查询接口 + Next.js 中英双语前端（总览 / 排行榜 / 交易员 / 数据健康）
- K 线自记录（BTC/ETH/SOL/HYPE，1m–1d）
- **大周期研究第一阶段**：ZigZag 客观划分波段、全体钱包分段收益与运气概率初筛、候选钱包成交抢救性归档（见 [docs/08](docs/08-swing-event-research.md)）
- 币安 U 本位永续 1 分钟 K 线批量导入（data.binance.vision，SHA256 校验）与币种映射校验
- 从逐笔成交重建仓位路径、交易决策和仓位周期（按超短 / 短线 / 波段 / 大周期分级），并能与交易所实时持仓对账
- **多级别时机评分（T3）**：位置分、置换检验与多重检验校正、拐点精准率 / 召回率、顺势比例，7D / 30D / 90D / 全部滚动；节奏榜、交易员时机评分卡与节奏日历
- **波段持仓者**：开仓区间 → 平仓区间（如 BTC 63–68k 做多拿到 83–87k），以及整段行情捕获率（按小时盯市、顺势时间、捕获率）
- **K 线回放**：交易员页上把每次买卖（B/S）和局部高低点画在 K 线上
- **关键价位交易查询**：谁在指定价格区间、时间段开过多 / 空仓及其结局，可以取交集（如 BTC 6 万多做多且 8.4–8.8 万做空）
- **钱包收藏**：分组、别名、备注、标签；全站星标与别名显示，收藏的钱包优先归档成交
- **仓位提醒**：独立实时进程每 30 秒轮询被订阅钱包的持仓，识别开 / 加 / 减 / 平 / 反手 / 调杠杆 / 接近强平，经 Gmail SMTP 合并发信（设置页配置，密码加密保存）
- **模拟跟单**：按实时中间价镜像交易员的目标仓位，计入手续费、滑点、资金费、维持保证金强平、延迟与回撤止损；多个模拟互不影响；看板按 24 小时 / 7 天 / 30 天 / 全部统计盈亏、回撤、胜率、保留率、每日盈亏日历和模拟间相关性（见 [docs/10](docs/10-watchlist-alerts-copysim.md)）
- **OKX 数据源**：每日完整抓取 OKX 带单 7 天榜（版本与人数校验），自动回填每个交易员 1–2 年的历史仓位，下榜的人照样跟踪；旧采集器的 2,629 人 / 85.8 万条仓位一次性迁入。节奏评分、波段持仓者、关键价位、K 线回放、收藏、提醒、模拟跟单都共用一套，页面上一键切换交易所（见 [docs/11](docs/11-okx-source.md)）
- **市场仓位雷达**：持续轮询 Hyperliquid 钱包和 OKX 带单员的真实持仓（按重要程度分档 30 分钟 – 12 小时），给出多空人数与金额、币种多空地图、最近开仓（区分精确时间与观测区间）、观测样本潜在清算压力和每小时快照，每个数字都能下钻到具体仓位（见 [docs/12](docs/12-market-radar.md)）
- **多风格发现**：按稳健复利、大趋势、波段、抄底、摸顶做空、短线、低回撤、币种专精分别找人（另有高风险投机识别），每个标签附规则、数值、样本量和样本外检验；"只平盈利单、亏损单死扛"等账面美化有专门的否决规则（见 [docs/13](docs/13-style-discovery.md)）
- APScheduler 后台调度（共享限流预算）、一键安装 / 一键更新脚本（systemd，参考 bian）

## 本地开发

前置：Python 3.11、Node 20、PostgreSQL 16（本机运行，或用 docker compose 里的 db）。

```bash
createdb alphascope && createdb alphascope_test
cp .env.example .env            # 本地默认值即可
make setup                      # venv + 依赖 + 迁移
make ingest-leaderboard         # 真实排行榜入库（约 40 MB 下载）
make enrich LIMIT=50            # 增强 50 个 B 层钱包
make api                        # http://localhost:8000/docs
make web-dev                    # http://localhost:3000/zh
make worker                     # 可选：持续调度
make check                      # 后端测试 + 前端 lint/类型检查 + 构建
```

## 部署（本地 → GitHub → 服务器）

```bash
curl -fsSL https://raw.githubusercontent.com/linlea666/alphascope/main/install.sh -o /tmp/alphascope-install.sh
sudo bash /tmp/alphascope-install.sh     # 一键安装（Ubuntu 22.04 / 24.04）
sudo /opt/alphascope/start.sh update     # 以后一键更新
```

详见 [deploy/README.md](deploy/README.md)。

## 目录

```
backend/   FastAPI、采集、量化计算、Alembic 迁移、测试
frontend/  Next.js 15 + next-intl（zh/en）+ Tailwind + ECharts
docs/      产品、架构、数据模型、指标、标签、UX、路线图、可行性评估
deploy/    systemd 服务模板、命令行包装、反向代理示例
install.sh 一键安装入口；start.sh 运维入口（status / update / logs / doctor / backup / restore / password）
```

## 数据原则

- 不伪造历史：覆盖不足的窗口显示“数据不足”，不外推。
- 不用未来数据：所有原始响应带采集时间归档，可以回放“某个时间点系统知道什么”。
- 官方数值与自算数值分开标注；自算指标都带可信度。
- 生产路径不使用 Mock 数据；测试用的是裁剪后的真实响应（`backend/tests/fixtures`）。

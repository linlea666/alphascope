# AlphaScope — Hyperliquid Trader Intelligence

寻找**可验证、风险调整后、可复制**的交易优势，而不是按 ROI 排名。

> 当前阶段：**P0**（排行榜闭环 + portfolio 增强）。设计文档见 [docs/](docs/)，从 [docs/00-feasibility.md](docs/00-feasibility.md) 开始读。

## 已实现（P0）

- 官方排行榜全量导入（约 4.8 万钱包）、条件请求、原始响应归档、结构变化即失败
- 新钱包发现 / 消失检测，排行榜宽表快照（每 6 小时）
- 数据质量标记（零净值、微尘、小分母 ROI、极端 ROI、被动盈亏、子账户汇总）
- 资源分层 Z / C / B（规则带版本，变化留痕）
- B 层钱包按 API 权重预算增强：portfolio 曲线 + 当前持仓
- 资金流调整 TWR / MDD（Modified Dietz），每个指标附覆盖范围、点数、精度与可信度
- FastAPI 查询接口 + Next.js 中英双语前端（总览 / 排行榜 / 交易员 / 数据健康）
- APScheduler 后台调度、Docker Compose 部署

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

## 部署（本地 → git 仓库 → 服务器拉取）

见 [deploy/README.md](deploy/README.md)。

## 目录

```
backend/   FastAPI、采集、量化计算、Alembic 迁移、测试
frontend/  Next.js 15 + next-intl（zh/en）+ Tailwind + ECharts
docs/      产品、架构、数据模型、指标、标签、UX、路线图、可行性评估
deploy/    部署、备份脚本，反向代理示例
```

## 数据原则

- 不伪造历史：覆盖不足的窗口显示“数据不足”，不外推。
- 不用未来数据：所有原始响应带采集时间归档，可以回放“某个时间点系统知道什么”。
- 官方数值与自算数值分开标注；自算指标都带可信度。
- 生产路径不使用 Mock 数据；测试用的是裁剪后的真实响应（`backend/tests/fixtures`）。

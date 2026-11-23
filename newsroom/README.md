# AlphaScope Newsroom

由 AlphaScope 可核验交易数据驱动的加密财经编辑部：程序发现新闻 → 事实包锁定每个数字 → AI 总编辑解释式立项 → AI 记者按账号写不同角度的中英文草稿 → 事实守卫 → 人工审核 → 人工在 X 发布并记录 → 事实变化自动追踪更正。

它与 AlphaScope 在同一个仓库，但独立运行：自己的数据库、服务、端口和部署脚本，只通过 AlphaScope 的只读 Feed 获取数据，从不调用交易所。

## 目录

```
newsroom/
  backend/nr/      Python 后端（FastAPI + APScheduler worker）
  backend/alembic/ 数据库迁移（手写 SQL，可回滚）
  backend/tests/   pytest（真实 Postgres）
  frontend/        Next.js 后台（中 / 英）
  deploy/          systemd 单元与 CLI 包装
  scripts/         部署函数
  docs/            设计文档、运维手册、测试报告
  start.sh         运维入口：sudo newsroom/start.sh <命令>
```

## 文档

| 文档 | 内容 |
|---|---|
| [00 审计](docs/00-audit.md) | AlphaScope 实际能力、字段语义、可信度问题 |
| [01 PRD](docs/01-prd.md) | 产品范围与边界 |
| [02 架构](docs/02-architecture.md) | 进程、分层、故障隔离 |
| [03 Feed 契约](docs/03-feed-contract-v1.md) | AlphaScope 只读 Feed v1 |
| [04 数据模型](docs/04-data-model.md) | 表结构 |
| [05 检测与评分](docs/05-detection-rules.md) | 规则 d1、评分 s1、可信度门 |
| [06 事实与守卫](docs/06-facts-and-guard.md) | 事实包 fp.v1、守卫 g1 |
| [07 AI 层](docs/07-ai-layer.md) | 模型接入、调度、注入防护 |
| [08 编辑流程](docs/08-editorial-workflow.md) | 状态机、权限、发布清单、合规 |
| [09 运维](docs/09-operations.md) | 安装、配置、更新、演练、备份 |
| [10 测试](docs/10-test-plan.md) | 强制场景对照与测试报告 |
| [11 栏目与创新](docs/11-innovation-backlog.md) | 12 个栏目评估 + 12 个新创意 |
| [12 路线图](docs/12-roadmap.md) | Phase 2–4 与商业化 |
| [13 决策记录](docs/13-decisions.md) | 关键架构决策 |

## 快速开始（本机）

```bash
cd newsroom && make setup
printf 'NEWSROOM_FEED_TOKEN=dev-token\n' > backend/.env
# 另一个终端：AlphaScope API 开启 Feed（不要启动 AlphaScope worker）
ALPHASCOPE_FEED_TOKEN=dev-token backend/.venv/bin/uvicorn app.main:app --port 8610   # 在仓库根目录的 backend/ 下
make api & make worker & make web-dev
cd backend && .venv/bin/python -m nr.cli create-admin
```

打开 http://localhost:3300/zh 。服务器部署见 [09 运维](docs/09-operations.md)。

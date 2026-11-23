# 02 系统架构

## 1. 进程与数据流

```
┌──────────── AlphaScope（不改动运行方式）────────────┐
│ worker / live 采集 → Postgres: alphascope            │
│ api :8610（127.0.0.1）── 新增 /api/v1/feed/*（只读、令牌）│
└───────────────────────────┬──────────────────────────┘
                            │ HTTP（本机），feed.v1 契约
┌─────────────── Newsroom ──┴──────────────────────────┐
│ newsroom-worker                                      │
│   APScheduler：feed_sync 60s → 检测；回放 30min；健康 2min；│
│               近强平 10min；事实刷新 15min；维护 1h      │
│   队列线程：build_facts / card_render（确定性）          │
│             eic_evaluate / draft_generate（AI，独立线程）│
│ newsroom-api :8620（127.0.0.1）  会话鉴权 + RBAC        │
│ newsroom-web :3300（默认 127.0.0.1）  BFF 代理 + 页面     │
│ Postgres: newsroom（独立角色）   data/assets 图卡文件     │
└──────────────────────────────────────────────────────┘
          AI 网关（OpenAI 兼容 / Anthropic 兼容，可配置）
```

## 2. 模块分层（对应代码 `newsroom/backend/nr/`）

| 层 | 模块 | 是否用大模型 |
|---|---|---|
| 数据接入 | `feed/`（契约、客户端、同步、健康） | 否 |
| 标准化 | `normalize/episodes.py`（持仓回合） | 否 |
| 检测 | `detect/`（规则 d1、近强平） | 否 |
| 事实 | `facts/`（事实包、评分、门、格式化、构建任务） | 否 |
| 故事 | `events/service.py`（交易员连续剧） | 否 |
| 选题 | `desk/eic.py` | 是（只做判断和解释） |
| 编辑 | `desk/drafting.py`, `articles/` | 是（只写文字） |
| 审核 | `guard/` | 否 |
| 素材 | `cards/` | 否 |
| 运营 | `publish/`, `desk/assignment.py` | 否 |
| AI 基础 | `ai/`（适配器、调度、Schema、注入防护、模型测试） | — |
| 平台 | `auth/`, `audit.py`, `settings_store.py`, `jobs/`, `maintenance.py` | 否 |

确定性逻辑全部用代码；大模型只在“判断是否值得报道、怎么写”两处出现，且输出被 Schema 和守卫双重约束。

## 3. 故障隔离矩阵

| 故障 | AlphaScope | Newsroom |
|---|---|---|
| Newsroom 全部停机 | 不受影响（只多了一个没人调用的只读接口） | — |
| Newsroom 数据库故障 | 不受影响 | 停止工作 |
| AlphaScope API 停机 | — | 同步失败被记录；健康快照 `reachable=false`；新事件被 `SOURCE_STALE` 阻断；已有数据和稿件照常浏览与编辑 |
| AlphaScope 雷达过期 | — | 对应交易所的事件被 `SOURCE_STALE` 阻断 |
| AI 网关故障 / 超时 | 不受影响 | AI 任务重试后回到队列；事件回到候选池；人工撰稿、审核、发布完全可用 |
| AI 预算用尽 | 不受影响 | AI 任务延后到预算恢复；其余照常 |
| 紧急停止 | 不受影响 | 发布记录接口返回 423，AI 任务暂停；同步与检测继续 |
| 仓库被 AlphaScope 更新拉到新版本 | — | 不受影响：Newsroom 从自己的发布快照运行，直到执行 `newsroom/start.sh deploy` |

## 4. 资源

| 服务 | 内存上限 | 调度 |
|---|---|---|
| newsroom-api | 400M | 1 个 uvicorn worker |
| newsroom-worker | 500M | CPUWeight=30, Nice=10（AlphaScope 优先） |
| newsroom-web | 400M | — |

worker 不派生子进程；AI 并发由 `NEWSROOM_AI_MAX_CONCURRENCY`（默认 2）控制。

## 5. 技术选择

与 AlphaScope 保持一致，降低维护成本：Python 3.11、FastAPI、同步 SQLAlchemy 2.1 + 原生 SQL、psycopg3、手写 SQL 的 alembic 迁移、APScheduler 3；Next.js 15.5 + React 19 + next-intl + Tailwind 4。新增依赖只有 Jinja2 和 CairoSVG。不引入 Redis / 消息队列：一个 Postgres 任务表（`FOR UPDATE SKIP LOCKED` + 租约）足够 Phase 1–3 的量级。

# 04 数据模型

迁移：`newsroom/backend/alembic/versions/0001`–`0005`，全部手写 SQL，均可回滚（`tests/test_migrations.py` 做 base → head → 0002 → head 往返）。
约定：文本枚举用 CHECK；时间 `timestamptz` 且连接时区为 UTC；数值 `numeric`。
**只追加的表**（`nr_append_only()` 触发器拒绝 UPDATE / DELETE）：`audit_log`、`fact_packet`、`article_version`、`prompt_template`、`trader_context_snapshot`。

## 0001 身份与审计

| 表 | 要点 |
|---|---|
| `app_user` | scrypt 密码哈希；失败计数与锁定；首次登录强制改密 |
| `user_role` | 7 个角色 |
| `user_session` | 只存令牌 SHA-256；过期、撤销 |
| `audit_log` | 谁（user / system / ai）、做了什么、对象、账号、前后值、原因；只追加 |
| `system_setting` | 规则、预算、紧急停止等 JSON；`secret` 列 Fernet 加密 |

## 0002 数据接入

| 表 | 要点 |
|---|---|
| `source_connection` | 数据源与最近成功 / 错误 |
| `ingestion_checkpoint` (+ `_history`) | 游标；历史每 10 分钟一条，供回放 |
| `sync_run` | 每次同步：模式、页数、拉取、插入、积压、错误 |
| `source_health_snapshot` | Feed 健康快照（门禁输入） |
| `trader` | `trader_key` 唯一；`public_label` 只能由总编辑填写并注明来源 |
| `trader_context_snapshot` | 上下文原文（证据），按内容哈希去重；只追加 |
| `source_event` | `UNIQUE(source_id, source_event_id)` 保证幂等 |
| `position_episode` | 部分唯一索引：每个 (交易员, 币种) 只有一个 `open` 回合；`start_kind` 区分 open / flip / first_seen / reconstructed |

## 0003 事件与事实

| 表 | 要点 |
|---|---|
| `news_event` | `dedupe_key` 唯一（交易所+交易员+币种+回合+类型+档位的哈希）；状态；评分与拆解；门禁状态与阻断原因；当前事实包 |
| `event_state_transition` | 允许的状态转换（与 `nr/events/states.py` 一致，有测试） |
| `event_state_history` | 每次转换：谁、何时、原因 |
| `event_evidence` | 原始观测 / 上下文快照 / 健康快照的引用与哈希 |
| `fact_packet` | `UNIQUE(event, version)`；内容 JSON + SHA-256；`supersedes`；`change_summary`；只追加 |
| `news_story`, `story_event` | 交易员连续剧；章节角色（开篇 / 续集 / 结局）；`pinned_fact_packet_id` 固定发布时的事实版本 |

## 0004 编辑部

| 表 | 要点 |
|---|---|
| `editorial_style` | 版本化风格：指南、结构、禁用表达 |
| `editorial_account` | 定位、受众、时区、风格、事件类型范围、最低分、每日上限、最短间隔、长帖、授权状态与到期 |
| `account_permission` | (用户, 账号, 权限) |
| `eic_evaluation` | AI 总编辑输出原文 + 结论 + 优先级 + 调用记录 |
| `assignment` | 部分唯一：同一事件同一账号只有一个有效分派；`needs_human` 标记角度相近 |
| `article` | 状态；当前版本；需重新核验原因；重复确认人 |
| `article_version` | 不可变：来源、作者 / 模型 / 提示词版本、事实包 id 与版本、正文、引用事实、守卫报告 |
| `article_fact_reference` | 版本 ↔ 事实 id（可反查“哪些稿件引用了这个事实”） |
| `media_asset` | 模板与版本、事实包、语言、品牌、输入哈希、文件哈希 |
| `publication_plan` | 排期（Phase 2 日历） |
| `publication_record` | 必须指向版本；X 帖子 id（同账号唯一）、链接、时间、记录人、实际文本及哈希、是否与稿件一致、守卫报告、撤稿信息 |
| `correction_record` | 新旧事实包、变化项、受影响稿件与已发帖子、处理状态 |
| `performance_snapshot` | 人工 / CSV / API 录入的表现数据 |

## 0005 AI 与任务

| 表 | 要点 |
|---|---|
| `ai_model_config` | 协议、地址、模型、加密密钥、JSON 模式、超时、重试、并发、单价、最近测试 |
| `prompt_template` | (key, version) 唯一；只追加 |
| `ai_agent_profile` | (agent, version)；每个 agent 只有一个 active 版本；主 / 备用模型；单任务预算 |
| `job` | 部分唯一 `dedupe_key`（排队或运行中）；租约；重试；死信 |
| `ai_call_log` | 每次尝试：模型、提示词版本、token、费用、延迟、状态、错误、原始输出（截断） |

## 未来多租户

所有业务表都能通过增加 `workspace_id` 列和行级安全扩展；首版没有提前实现。

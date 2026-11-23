# 10 测试计划与测试报告

## 1. 运行方式

- Newsroom：`make -C newsroom check`（pytest 连接真实 Postgres `newsroom_test`，每次会话先 `alembic downgrade base` 再 `upgrade head`；eslint；tsc；shellcheck；next build）。
- AlphaScope：`make test`（含新增的 `backend/tests/test_feed.py`）。
- 外部依赖全部用替身：Feed 用 `httpx.MockTransport` 实现的内存版 AlphaScope（`tests/fakes.py::FakeFeed`），AI 用按脚本返回的 `FakeProvider`。

## 2. 强制场景对照

| # | 场景 | 测试 | 结果 |
|---|---|---|---|
| 1 | 同一持仓反复轮询不重复生成事件 | `test_sync.py::test_repeated_polling_and_replay_create_no_duplicates`，`test_detection.py::test_adds_merge_until_published_then_only_higher_tiers` | 通过 |
| 2 | 首次观测不被当作开仓时间 | `test_detection.py::test_first_seen_is_never_an_open`，`test_claims_guard.py::test_wording_constraints` | 通过 |
| 3 | 浮盈不被写成已实现 | `test_detection.py::test_unrealized_pnl_and_estimates_are_labelled`，`test_claims_guard.py` | 通过 |
| 4 | 覆盖不足不生成虚假连胜 | `test_detection.py::test_history_needs_complete_coverage`、`test_streak_needs_a_known_start` | 通过（完整连胜研究在 Phase 3） |
| 5 | 中英文稿件引用同一组事实 | `test_desk.py::test_bilingual_drafts_cite_the_same_packet` | 通过 |
| 6 | 同语种账号不因换词群发 | `test_desk.py::test_same_language_near_duplicates_are_blocked`，`test_claims_guard.py::test_rewording_is_still_a_duplicate` | 通过 |
| 7 | 拒绝报道的账号不被默认分发 | `test_desk.py::test_silent_and_rejected_accounts_get_nothing`、`test_eic_must_place_every_account` | 通过 |
| 8 | 事实更正能找出受影响稿件 | `test_desk.py::test_fact_correction_finds_affected_drafts_and_posts` | 通过 |
| 9 | 已发布内容可追溯稿件版本 | `test_desk.py::test_publication_traces_to_version_and_packet` | 通过 |
| 10 | 授权过期不能发布 | `test_desk.py::test_expired_authorization_and_emergency_stop_block_publishing`，`test_operations.py::test_authorization_expires_on_schedule` | 通过 |
| 11 | AI 输出不符合 Schema 不进入可发布状态 | `test_desk.py::test_invalid_ai_output_never_becomes_a_draft` | 通过 |
| 12 | 数据源中断时标记过期 | `test_sync.py::test_outage_is_recorded_and_cursor_kept`，`test_detection.py::test_gate_blocks_stale_sources_and_traders` | 通过 |
| 13 | 媒体任务失败不影响 AlphaScope | AlphaScope `test_feed.py`（只读事务、全部 GET、令牌关闭即 404）+ 运维演练（09 §7） | 通过（演练需在服务器上执行） |
| 14 | 迁移可升级和回滚 | `test_migrations.py::test_upgrade_downgrade_roundtrip` | 通过 |
| 15 | 没有 X API 密钥也能完成人工流程 | `test_api.py::test_full_manual_flow_without_x_keys_or_ai` | 通过 |
| 16 | 未授权编辑不能操作其他账号 | `test_api.py::test_account_permissions_are_isolated` | 通过 |
| 17 | 提示注入不能修改编辑规则 | `test_desk.py::test_prompt_injection_cannot_change_the_rules` | 通过 |
| 18 | 同一故事可以生成后续报道并保留历史事实版本 | `test_detection.py::test_adds_merge_until_published_then_only_higher_tiers`（续集）、`test_api.py`（发布时固定事实版本） | 通过（故事引擎在 Phase 3） |
| 19 | 图卡数据与事实包一致 | `test_cards.py::test_card_numbers_come_from_the_packet` | 通过 |
| 20 | AI 失效时仍可查看数据与稿件 | `test_api.py::test_reading_works_while_ai_is_down`，`test_desk.py::test_ai_outage_leaves_the_desk_usable` | 通过 |

另有：状态机与种子一致、只追加表拒绝修改、强平从疑似升级为确认、近强平分档、AI 评估必须安排每个账号、人工稿不被覆盖、AI 队列在紧急停止时暂停、模型测试不回显密钥、登录锁定、发布文本再守卫、汉字数字识别、卡在“评估中”的事件自动回到候选池。

## 3. 测试报告（2026-11-23，本机 macOS，Postgres 本地）

| 项 | 结果 |
|---|---|
| Newsroom pytest | 64 passed（含栏目、多模型对比、RPA、公开页、故事引擎、平台路由、试写、回收站与批量操作） |
| AlphaScope pytest | 234 passed（含 Feed 契约测试 8 个，覆盖 7 个研究端点） |
| eslint / tsc | 无错误 |
| shellcheck（newsroom 脚本） | 无告警 |
| next build | 成功（12 个页面 + 3 个路由处理器） |

## 4. 本机端到端联调（真实数据）

- AlphaScope API 以只读 Feed 模式启动，连接本机 `alphascope` 库（数据停在 2026-11-17，1740 条雷达事件）。
- Newsroom 同步 1740 条（1.3 秒），其中 1619 条是首次观测，检测得到 2 条新闻事件（大额加仓、浮亏加仓）。
- 两条事件都被 `SOURCE_STALE` + `TRADER_STALE` 正确阻断（数据已过期 6 天）。
- 事实包展示串、约束（首次观测、名义非保证金、历史不可用、账户比率不可用）与图卡渲染正确。
- 网页 20 个页面在登录后均返回 200；跨站写入、缺少自定义头、未登录的写入都被 BFF 拒绝；紧急停止开启后导航栏显示红色横幅。

## 5. 没有验证的部分（如实说明）

- **真实 AI 网关**：没有可用的密钥，AI 只用 `FakeProvider` 测试；两种协议的请求格式按公开文档实现，首次接入请用“测试模型”确认。
- **生产服务器安装**：`newsroom/start.sh` 通过了 shellcheck，但没有在 Ubuntu 服务器上实际执行；上线前需要在服务器上跑一次 install 和 09 §7 的四个演练。
- **生产数据量**：同步与检测的性能只在 1740 条数据上验证过；生产 `market_event` 的增量需要实测（每分钟最多拉 2 万行）。
- **浏览器交互**：页面做了服务端渲染与接口层面的冒烟测试，没有做浏览器自动化点击测试。

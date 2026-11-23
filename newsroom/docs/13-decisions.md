# 13 架构决策记录

## ADR-1 只读 Feed API，而不是直接读 AlphaScope 数据库
- 背景：AlphaScope 内部表会重建（`position_cycle` 每次先删后插）、会改结构；直接读表会让 Newsroom 悄悄出错。
- 决定：在 AlphaScope 新增版本化的只读 Feed（`feed.v1`），两边共享契约键集合文件，任何一侧破坏契约都会让测试失败。
- 代价：AlphaScope 多一个文件；Newsroom 的查询能力受 Feed 端点限制，新需求要先加端点。

## ADR-2 主键游标 + 滞后截断 + 定期回放，不加 observed_at 索引
- 背景：`market_event` 的 id 可能乱序提交；`observed_at` 没有索引，加索引要停 worker 迁移。
- 决定：按主键扫描，遇到未沉淀（120 秒内）的行即停止；每 30 分钟从约 60 分钟前的游标回放一次，插入幂等。
- 代价：事件最多延迟约 3 分钟进入 Newsroom（沉淀 2 分钟 + 同步周期 1 分钟）。

## ADR-3 一个 Postgres 任务队列，不引入 Redis / MQ
- 决定：`job` 表 + `FOR UPDATE SKIP LOCKED` + 租约 + 部分唯一的去重键。AI 任务与确定性任务分线程。
- 理由：量级小（每天几十到几百个任务）；少一个组件就少一个故障点，备份也只有一个数据库。

## ADR-4 API 内会话鉴权 + RBAC，前端只做跳转
- 决定：不沿用 AlphaScope 的 Basic Auth。登录发放随机令牌（库里只存哈希），httpOnly + SameSite=Strict cookie；BFF 代理附加令牌；所有权限在 API 判断，按账号隔离。
- 理由：多人、多角色、多账号；审计需要知道是谁。

## ADR-5 可信度门与事实守卫是硬门，没有覆盖开关
- 决定：门禁阻断的事件不能进入评估和分派；守卫失败的版本不能审核通过或记录发布。误报通过补充事实或改写解决。
- 理由：一次错误的“已确认爆仓”比漏掉十条新闻代价更大；“临时放行”按钮会成为常态。
- 代价：初期可能有误报，需要根据守卫拦截记录迭代规则（守卫有版本号 g1，规则变化可追溯）。

## ADR-6 发布快照运行，部署与 AlphaScope 解耦
- 背景：AlphaScope 直接从工作区运行，worker 会派生子进程重新导入代码。
- 决定：Newsroom 后端从 `git archive` 生成的快照运行；`update` 发现 `newsroom/` 之外有变化时拒绝，要求先更新 AlphaScope；两边共用部署锁。
- 理由：任何一方的更新都不会让另一方加载到不匹配的代码。

## ADR-7 图卡用 SVG + CairoSVG，Playwright 作为以后的可选渲染器
- 决定：模板只读事实包展示串；CairoSVG 体积小、无浏览器进程，适合 500M 内存上限的 worker。字体由 fontconfig 选择（服务器为 Noto Sans CJK）。
- 代价：排版能力有限；复杂长图在 Phase 3 再引入 Playwright，挂在同一渲染接口后面。

## ADR-8 网页默认只监听本机
- 决定：`NEWSROOM_WEB_HOST=localhost`，通过 SSH 隧道或 TLS 反代访问。不用 `127.0.0.1`：Next.js 的中间件重写会在内部请求 `localhost`，监听地址必须与之解析一致，否则页面全部 500。
- 理由：现有服务器没有 HTTPS 反代；会话 cookie 不应在明文 HTTP 上跨公网传输。

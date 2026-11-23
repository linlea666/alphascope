# 09 部署与运维手册

## 1. 前提

- 服务器已用 AlphaScope 的 `install.sh` 安装在 `/opt/alphascope`（Newsroom 位于 `/opt/alphascope/newsroom`）。
- AlphaScope 已更新到包含只读 Feed 的版本：在仓库根目录 `sudo ./start.sh update` 后，`data/alphascope.env` 里会出现 `ALPHASCOPE_FEED_TOKEN`。
  该更新会按 AlphaScope 原有流程重启它的四个服务（几秒钟）。

## 2. 安装

```bash
cd /opt/alphascope
sudo newsroom/start.sh install
```

脚本会：安装 `libcairo2`、`fonts-noto-cjk`；创建系统用户 `newsroom`；生成 `newsroom/data/newsroom.env`（从 AlphaScope 配置读取 Feed 地址和令牌，只读不改）；创建独立数据库 `newsroom`；把后端打成发布快照 `newsroom/.runtime/releases/<tree>`；迁移并写入种子数据；构建前端；安装 `newsroom-api` / `newsroom-worker` / `newsroom-web` 三个 systemd 服务和 `/usr/local/bin/newsroom`；最后询问管理员用户名并创建管理员（首次登录必须改密码）。

## 3. 访问

网页默认只监听本机 `localhost:3300`（登录 cookie 不应在明文 HTTP 上跨公网传输）。

- 推荐：本机执行 `ssh -L 3300:localhost:3300 root@服务器IP`，浏览器打开 `http://localhost:3300/zh`。
- 需要公网访问时：在前面放 Caddy / Nginx 并启用 HTTPS，然后把 `NEWSROOM_WEB_HOST=0.0.0.0`、`NEWSROOM_COOKIE_SECURE=true` 写进 `newsroom.env`，`sudo newsroom/start.sh restart`。

## 4. 首次配置（网页）

1. 系统管理 → AI 模型：选预设（DeepSeek 官方、aicodemirror Codex/Claude、OpenAI）或自定义，填密钥和单价 → 测试模型。
   aicodemirror 的 Cloudflare 目前拦截本服务器 IP（2026-11-23 实测：本机 401、服务器 403），需要服务商放行后才能用。
2. 系统管理 → AI 角色：给 `eic` 和 `reporter` 选主模型和备用模型（会生成新版本）。
3. 系统管理 → 提示词版本：按自己的编辑方针保存新版本，再在 AI 角色里指向它。
4. 账号运营：三个示例账号（中文 X、英文 X、OKX 广场）默认是暂停状态。X 账号只报道 Hyperliquid，OKX 广场账号只报道 OKX。按真实 X 账号修改用户名、定位、风格，把状态改为 active，授权状态改为 authorized 并填写到期时间。
5. 系统管理 → 用户与角色：添加成员；在账号页给成员分配账号权限。
6. 系统管理 → 规则与预算：确认每日 AI 预算、检测阈值。

## 5. 日常命令

| 命令 | 作用 |
|---|---|
| `sudo newsroom/start.sh status` | 服务、数据源、事件和任务概况 |
| `sudo newsroom/start.sh logs [api|worker|web]` | 实时日志 |
| `sudo newsroom/start.sh doctor` | 服务、Feed、字体、磁盘自检 |
| `sudo newsroom/start.sh backup` / `restore <文件>` | 备份 / 恢复 newsroom 数据库 |
| `sudo newsroom/start.sh emergency-stop "原因"` / `resume` | 紧急停止 / 解除 |
| `sudo newsroom sync` / `jobs` / `status` | 手动同步 / 执行排队任务 / 状态 |

## 6. 更新

- 只有 Newsroom 代码变化：`sudo newsroom/start.sh update`。
- AlphaScope 代码也有变化：脚本会拒绝并提示。先在仓库根目录 `sudo ./start.sh update`（会把 Newsroom 的代码一起拉下来，但不影响正在运行的 Newsroom），再 `sudo newsroom/start.sh deploy`。
- 两个项目共用部署锁，安装 / 更新不会同时进行。Newsroom 的迁移只会停 `newsroom-worker`。

## 7. 故障演练（上线前做一次）

| 演练 | 操作 | 期望 |
|---|---|---|
| Newsroom 停机 | `sudo newsroom/start.sh stop` | `sudo ./start.sh status` 显示 AlphaScope 一切正常 |
| AlphaScope API 停机 | `systemctl stop alphascope-api`（演练后立即启动） | 驾驶舱显示数据源无法连接；新事件被 `SOURCE_STALE` 阻断；稿件照常浏览和编辑 |
| AI 网关故障 | 把模型地址改成错误地址 | AI 任务失败后重试、事件回到候选；人工撰稿和发布记录正常 |
| 紧急停止 | 驾驶舱开启 | 记录发布返回 423；AI 任务不再执行 |

## 8. 备份与恢复

- 数据库：`backup` 生成 `newsroom/data/backups/newsroom-*.dump`，保留 7 份；恢复会先停服务再 `pg_restore` 和迁移。
- 图卡文件：`newsroom/data/assets`（可随时从事实包重新生成，不是唯一副本）。
- 密钥：`newsroom/data/secret.key` 丢失后已保存的 AI 密钥无法解密，需要重新填写。请和数据库备份分开保存。

## 9. 本地开发

```bash
cd newsroom
make setup          # venv、依赖、newsroom 与 newsroom_test 数据库、迁移、种子
make api            # :8620
make worker
make web-dev        # :3300（另开终端）
make check          # pytest + eslint + tsc + shellcheck + next build
```

本机联调需要 AlphaScope API 开启 Feed：`ALPHASCOPE_FEED_TOKEN=xxx backend/.venv/bin/uvicorn app.main:app --port 8610`，并在 `newsroom/backend/.env` 写 `NEWSROOM_FEED_TOKEN=xxx`。不要在本机启动 AlphaScope 的 worker（生产服务器是唯一采集端）。

## 10. 栏目

栏目在“栏目”页查看和手动生成；Worker 每小时第 7 分钟检查：每周栏目周一生成，每月栏目 1 日生成，每日 / 每小时栏目按触发条件生成；数据不足被阻断的会在当天重试。可在“系统管理 → 规则与预算 → columns.disabled”停用某个栏目。

## 11. RPA 接入（可选，默认关闭）

1. 系统管理 → RPA 接口 → 生成令牌（只显示一次）。
2. 账号设置勾选 RPA。
3. 在日历里给已审核稿件排期。
4. RPA 轮询 `GET /api/rpa/next`（Authorization: Bearer 令牌），拿到文本后在 X 发布，再 `POST /api/rpa/plans/<id>/result` 回报帖子链接；失败时回报 `ok=false`，计划回到待发布。
所有人工发布的检查（授权、预算、紧急停止、守卫）对 RPA 同样生效，并写审计日志。RPA 只能通过本机或你自建的安全通道访问 API（API 只监听 127.0.0.1）。

## 12. 公开页

`/zh/p`：已发布的交易员故事、追踪账本（结局好坏都保留）、纠错与撤稿；只展示已发布内容，无需登录。

## 13. 稿件在哪里看、怎么试写、怎么管理

| 想做的事 | 去哪里 |
|---|---|
| 看所有 AI 生成的版本（模型、守卫结果、费用、耗时）和生成失败的原因 | 导航「AI 稿件」 |
| 不走分派流程，直接看某个事件用某个模型写出来的效果 | 事件详情页「快速试写」：选账号和模型，可多选做对比；暂停的账号、被阻断的事件也可以试写 |
| 修改、比较版本、换模型重写、润色、按守卫意见修正 | 编辑室（稿件库或事件页点进去） |
| 按来源 / 守卫结果 / 模型 / 试写 / 状态筛选，批量提交、作废、删除 | 「稿件库」，勾选后批量操作 |
| 找回删除的稿件 | 「稿件库」右上角回收站 → 恢复 |
| 导出一篇稿件（正文、引用的事实、守卫意见） | 编辑室顶部「导出 Markdown」 |

规则：
- 试写稿不会改变事件状态，也不能提交审核；要正式使用，在事件页把事件分派给这个账号（试写不会占用正式分派的名额）。
- “删除”是移入回收站，可以恢复；稿件的每个版本永久保留，用于追溯。已发布的稿件不能删除，只能撤稿。
- 生成需要排队，页面会在有任务时每 5 秒自动刷新；没有配置可用模型时，「AI 稿件」页顶部会提示。

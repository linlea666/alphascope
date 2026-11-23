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

1. 系统管理 → AI 模型：添加模型（地址、模型名、密钥、协议、JSON 模式、单价）→ 测试模型。
2. 系统管理 → AI 角色：给 `eic` 和 `reporter` 选主模型和备用模型（会生成新版本）。
3. 系统管理 → 提示词版本：按自己的编辑方针保存新版本，再在 AI 角色里指向它。
4. 账号运营：两个示例账号默认是暂停状态。按真实 X 账号修改用户名、定位、风格，把状态改为 active，授权状态改为 authorized 并填写到期时间。
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

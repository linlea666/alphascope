# 部署：本地开发 → GitHub → 服务器一键安装 / 一键更新

部署方式参考 [linlea666/bian](https://github.com/linlea666/bian)：原生 systemd，不用 Docker；和同一台服务器上的其他项目完全隔离。

| 隔离项 | AlphaScope 的做法 |
|---|---|
| 目录 | `/opt/alphascope`（代码）、`/opt/alphascope/data`（配置、原始归档、备份） |
| 系统用户 | `alphascope`（无登录 shell） |
| 服务 | `alphascope-api`、`alphascope-worker`、`alphascope-web` 三个 systemd 服务，带内存上限和沙箱 |
| Python | 项目内虚拟环境 `.venv`；Ubuntu 22.04 会额外安装 python3.11（不替换系统 python3） |
| Node.js | 官方二进制装在 `.runtime/node`（SHA256 校验），不影响系统 Node |
| 数据库 | 本机 PostgreSQL 中独立的角色和数据库 `alphascope`，随机密码只保存在 `data/alphascope.env` |
| 端口 | 网页 3200（对外，需要登录），API 8610（只监听 127.0.0.1） |

## 一键安装（Ubuntu 22.04 / 24.04）

```bash
curl -fsSL https://raw.githubusercontent.com/linlea666/alphascope/main/install.sh -o /tmp/alphascope-install.sh
sudo bash /tmp/alphascope-install.sh            # 默认装到 /opt/alphascope
```

安装脚本会依次：
1. 检查系统、磁盘（≥ 5 GiB）和内存（≥ 1 GiB，构建前端建议内存 + swap ≥ 2 GiB）；
2. 克隆仓库并校验来源；
3. 安装缺少的系统包（git、PostgreSQL、Python 3.11+）；
4. 创建用户、目录、配置和数据库；
5. 安装后端依赖并执行迁移；
6. 下载 Node.js 并构建前端；
7. 安装并启动 3 个服务，然后做健康检查。

结束时会打印网页地址和**登录密码**。可以重复运行，不会删除数据。

> 云服务器安全组只需要放行 TCP 3200，建议只允许自己的 IP 访问。

## 日常运维

```bash
cd /opt/alphascope
sudo ./start.sh status          # 服务状态、最近一次采集、API 预算
sudo ./start.sh update          # 拉取最新代码并重启（快进合并；前端没变时不重新构建，几秒钟完成）
sudo ./start.sh logs worker     # 实时日志：api / worker / web
sudo ./start.sh doctor          # 自检：服务、数据库、Hyperliquid 网络、磁盘
sudo ./start.sh backup          # 备份数据库到 data/backups（保留 7 份）
sudo ./start.sh password        # 重置网页登录密码
sudo alphascope swings          # 以服务用户身份执行后端命令（market / swings / fills / enrich ...）
```

`update` 的安全措施：
- 部署加锁，防止两个更新同时进行；
- 只接受 `linlea666/alphascope` 作为来源；
- 工作区有改动时拒绝更新；
- 只做快进合并；
- 前端在独立目录构建，完成后再切换，构建期间旧版本继续服务；
- 更新后自动做健康检查。

## 配置

`/opt/alphascope/data/alphascope.env`（改完执行 `sudo ./start.sh restart`）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `ALPHASCOPE_WEB_PORT` / `ALPHASCOPE_API_PORT` | 3200 / 8610 | 端口 |
| `ALPHASCOPE_WEB_AUTH` | `admin:<随机>` | 网页登录；留空表示不需要登录（不建议） |
| `ALPHASCOPE_HL_WEIGHT_SAFETY` | 0.5 | 使用 Hyperliquid 每 IP 每分钟 1200 权重中的比例 |
| `ALPHASCOPE_DATABASE_URL` | 自动生成 | 改成外部数据库地址时，安装脚本不再创建本机数据库 |
| `ALPHASCOPE_SWING_FILL_START` | 2026-07-01 | 候选钱包成交归档的起点 |

构建前端默认使用 `https://registry.npmjs.org/`；如果服务器访问慢，可以在运行 `install.sh` / `start.sh update` 前设置 `NPM_REGISTRY=https://registry.npmmirror.com/`。pip 镜像使用标准环境变量 `PIP_INDEX_URL`。

## 把本地已采集的数据迁移到服务器

本地开发机已经积累的快照和抢救下来的成交，迁移后可以继续使用：

```bash
# 本地
pg_dump -Fc alphascope > alphascope.dump
rsync -a data/raw/ root@服务器:/opt/alphascope/data/raw/
scp alphascope.dump root@服务器:/tmp/

# 服务器
chown -R alphascope:alphascope /opt/alphascope/data/raw
sudo /opt/alphascope/start.sh restore /tmp/alphascope.dump   # 会先自动备份服务器当前数据库
```

迁移完成后，记得停掉本地的 worker（`pkill -f app.worker`），避免两边同时采集、数据分叉。

## 可选：Docker Compose

仓库里保留了 `docker-compose.yml` 和 Dockerfile，适合不想装系统依赖的环境。这条路径的配置文件校验过，但**还没有实际构建过镜像**；推荐使用上面的一键安装。

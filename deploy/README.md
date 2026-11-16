# 部署：本地 → git 仓库 → 服务器拉取

## 服务器准备（一次性）

- Linux VPS，建议 4 vCPU / 16 GB / 100 GB SSD（P0–P1 足够）
- Docker Engine 与 Compose 插件（`docker compose version`）
- 能访问 `api.hyperliquid.xyz` 与 `stats-data.hyperliquid.xyz`

```bash
git clone <你的仓库地址> /srv/alphascope && cd /srv/alphascope
cp .env.example .env
# 必改：POSTGRES_PASSWORD；可选：ALPHASCOPE_HL_WEIGHT_SAFETY（同一 IP 上还有别的程序时调低）
./deploy/deploy.sh
```

启动后：
- `web` 监听 `127.0.0.1:3000`，`api` 监听 `127.0.0.1:8000`（只绑定本机，由反向代理对外）
- `api` 容器启动时自动执行 `alembic upgrade head`
- `worker` 等 API 健康后启动：每小时检查排行榜，每 10 分钟增强一批 B 层钱包
- 首次启动后排行榜会被自动导入（约 40 MB），B 层约 1.4 万个钱包首轮增强约需 12 小时

## 日常更新

本地提交并推送后，在服务器执行：

```bash
./deploy/deploy.sh      # git pull --ff-only + docker compose up -d --build
```

## HTTPS

参考 `Caddyfile.example`，把域名指向服务器后 `caddy run --config Caddyfile`。

## 备份

```bash
crontab -e
30 3 * * * /srv/alphascope/deploy/backup.sh   # 每天 pg_dump，保留 14 天
```

原始归档在 docker 卷 `rawdata` 中（每次排行榜约 37 MB 未压缩 / 约 5 MB 压缩），建议定期同步到对象存储。

## 注意

- `frontend/package-lock.json` 的下载地址是 npmmirror。如果服务器访问它慢，在构建前执行 `npm config set registry https://registry.npmjs.org/`，或者在 `frontend/Dockerfile` 里设置 registry。
- 不要在同一 IP 上运行多个部署来“绕过”限流；预算由 `ALPHASCOPE_HL_WEIGHT_SAFETY` 控制。

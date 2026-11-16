#!/usr/bin/env bash
# AlphaScope 运维脚本（参考 bian 的 start.sh）
#   sudo ./start.sh status | logs [api|worker|web] | restart | stop | start | update | doctor | backup | restore <文件> | password
set -euo pipefail
PROJECT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export PROJECT_DIR
# shellcheck source=scripts/deploy-common.sh
source "$PROJECT_DIR/scripts/deploy-common.sh"
cd "$PROJECT_DIR"

usage() {
    cat <<'TXT'
用法：sudo ./start.sh <命令>
  status            服务状态、健康检查、最近一次采集
  logs [服务]       实时日志（api / worker / web，默认全部；Ctrl+C 退出）
  restart           重启全部服务
  stop              停止全部服务（数据保留）
  start             启动全部服务
  update            拉取最新代码并重启（只做快进合并；前端没变时不重新构建，几秒钟完成）
  doctor            自检：服务、数据库、Hyperliquid 网络、磁盘、采集新鲜度
  backup            立即备份数据库（data/backups，保留最近 7 份）
  restore <文件>    从 pg_dump -Fc 备份恢复数据库（例如从本地开发机迁移数据）
  password          重置网页登录密码
TXT
}

cmd=${1:-status}
case "$cmd" in
    help|-h|--help) usage; exit 0 ;;
esac
need_root "$@"

case "$cmd" in
    install)
        # 由 install.sh 调用；可重复运行，不会删除已有数据
        lock_deployment
        check_repo
        ensure_system_packages
        deploy_all
        for port in "$(api_port)" "$(web_port)"; do
            if port_in_use "$port" && ! systemctl is-active -q "${SERVICES[@]}"; then
                fail "端口 $port 已被其他程序占用；请修改 $(env_file) 里的端口后重试"
            fi
        done
        systemctl restart "${SERVICES[@]}"
        verify_services
        print_access
        say '后台已开始采集：排行榜每小时检查一次，B 层钱包首轮增强约需 12 小时；./start.sh status 查看进度'
        ;;
    update)
        lock_deployment
        check_repo
        branch=$(git branch --show-current)
        [[ -n "$branch" ]] || fail '当前不在任何分支上'
        say "拉取 origin/$branch"
        git fetch -q origin "$branch"
        git merge-base --is-ancestor HEAD "origin/$branch" || fail "本地 $branch 有 origin 上没有的提交；停止更新"
        if [[ "$(git rev-parse HEAD)" == "$(git rev-parse "origin/$branch")" ]]; then
            say '代码已经是最新版本，继续检查配置和服务'
        else
            git merge -q --ff-only "origin/$branch"
            say "已更新到 $(git log -1 --format='%h %s')"
        fi
        # 用更新后的脚本继续（FD 9 保留部署锁）
        exec bash "$PROJECT_DIR/start.sh" --continue-update "$(git rev-parse HEAD)"
        ;;
    --continue-update)
        [[ -e /dev/fd/9 ]] || fail '此入口只允许由 update 调用'
        [[ "$(git rev-parse HEAD)" == "${2:-}" ]] || fail '更新过程中版本发生变化'
        ensure_system_packages
        deploy_all
        systemctl restart "${SERVICES[@]}"
        verify_services
        say '更新完成，数据和设置都已保留'
        ;;
    start|restart)
        [[ -f /etc/systemd/system/alphascope-api.service ]] || fail '尚未安装，请先运行 install.sh'
        systemctl "$cmd" "${SERVICES[@]}"
        verify_services
        ;;
    stop)
        systemctl stop "${SERVICES[@]}"
        say '已停止（数据保留）。再次启动：./start.sh start'
        ;;
    status)
        systemctl --no-pager --lines=0 status "${SERVICES[@]}" | grep -E '●|Active:' || true
        if curl -fsS "http://127.0.0.1:$(api_port)/api/v1/data-health" -o /tmp/alphascope-health.json 2>/dev/null; then
            python3 - /tmp/alphascope-health.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("\n最近一次成功：")
for r in d["latest_success"]:
    print(f"  {r['kind']:<12} {r['status']:<12} {r['finished_at']}")
t = d["tables"]
print(f"\n钱包 {t['wallet']:,} · 已增强指标 {t['wallet_metric']:,} 行 · 排行榜快照 {t['leaderboard_snapshot']:,}")
b = d["api_budget"]
print(f"近 24 小时 API 权重 {b['weight_used_24h']:,.0f} / {b['weight_capacity_24h']:,.0f}")
PY
            rm -f /tmp/alphascope-health.json
        else
            warn 'API 健康检查未通过'
        fi
        ;;
    logs)
        case "${2:-all}" in
            api|worker|web) journalctl -u "alphascope-$2" -n 200 -f ;;
            all) journalctl -u alphascope-api -u alphascope-worker -u alphascope-web -n 200 -f ;;
            *) fail '用法：./start.sh logs [api|worker|web]' ;;
        esac
        ;;
    doctor)
        ok=0
        for s in "${SERVICES[@]}"; do
            if systemctl is-active -q "$s"; then say "$s 运行中"; else warn "$s 未运行"; ok=1; fi
        done
        if as_service -c 'from sqlalchemy import text; from app.db import get_engine; get_engine().connect().execute(text("select 1"))' 2>/dev/null; then
            say '数据库连接正常'
        else
            warn '数据库连接失败（检查 data/alphascope.env 的 ALPHASCOPE_DATABASE_URL）'; ok=1
        fi
        if curl -fsS --max-time 15 -X POST https://api.hyperliquid.xyz/info -H 'Content-Type: application/json' \
            -d '{"type":"exchangeStatus"}' >/dev/null; then
            say 'Hyperliquid API 可访问'
        else
            warn '无法访问 api.hyperliquid.xyz'; ok=1
        fi
        free_gb=$(df -Pk "$(data_dir)" | awk 'END {printf "%.1f", $4/1024/1024}')
        say "数据目录可用空间 ${free_gb} GiB（原始归档约每天增长 200–500 MB）"
        exit $ok
        ;;
    backup)
        install -d -m 700 "$(data_dir)/backups"
        f="$(data_dir)/backups/alphascope-$(date -u +%Y%m%dT%H%M%SZ).dump"
        runuser -u postgres -- pg_dump -Fc alphascope > "$f"
        find "$(data_dir)/backups" -maxdepth 1 -name 'alphascope-*.dump' -printf '%T@ %p\n' | sort -rn \
            | tail -n +8 | cut -d' ' -f2- | xargs -r rm -f
        say "已备份到 $f（原始归档在 $(data_dir)/raw，建议另行同步到对象存储）"
        ;;
    restore)
        f=${2:-}
        [[ -f "$f" ]] || fail '用法：./start.sh restore <pg_dump -Fc 备份文件>'
        warn "将用 $f 覆盖当前数据库 alphascope（会先自动备份当前数据库）"
        read -r -p '确认请输入 yes：' answer
        [[ "$answer" == yes ]] || fail '已取消'
        "$0" backup
        systemctl stop "${SERVICES[@]}"
        runuser -u postgres -- pg_restore --clean --if-exists --no-owner --role=alphascope -d alphascope < "$f" \
            || warn 'pg_restore 报告了警告（通常可忽略），继续'
        migrate
        systemctl start "${SERVICES[@]}"
        verify_services
        say '恢复完成'
        ;;
    password)
        user=$(env_get ALPHASCOPE_WEB_AUTH admin:x)
        env_set ALPHASCOPE_WEB_AUTH "${user%%:*}:$(random_secret 12)"
        systemctl restart alphascope-web.service
        verify_services
        print_access
        ;;
    *)
        usage
        fail "未知命令：$cmd"
        ;;
esac

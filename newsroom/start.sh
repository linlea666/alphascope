#!/usr/bin/env bash
# AlphaScope Newsroom 运维脚本（与 AlphaScope 的 ./start.sh 相互独立）
#   sudo newsroom/start.sh install | deploy | update | status | logs [api|worker|web] | restart | stop | start
#                          doctor | backup | restore <文件> | create-admin | emergency-stop [原因] | resume
set -euo pipefail
NR_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
REPO_DIR=$(dirname "$NR_DIR")
export NR_DIR REPO_DIR
# shellcheck source=scripts/nr-deploy-common.sh
source "$NR_DIR/scripts/nr-deploy-common.sh"
cd "$NR_DIR"

usage() {
    cat <<'TXT'
用法：sudo newsroom/start.sh <命令>
  install           首次安装（需要 AlphaScope 已安装并启用只读 Feed）；可重复运行，不删除数据
  deploy            用仓库当前版本重新部署 Newsroom（不拉代码、不碰 AlphaScope）
  update            拉取最新代码并部署；如果 newsroom/ 之外也有更新，会要求先更新 AlphaScope
  status            服务状态、数据源同步、事件与任务概况
  logs [服务]       实时日志（api / worker / web，默认全部）
  restart|stop|start
  doctor            自检：服务、数据库、Feed 连通、AI 模型最近测试、字体、磁盘
  backup            备份 newsroom 数据库（data/backups，保留 7 份）
  restore <文件>    从 pg_dump -Fc 备份恢复
  create-admin      创建管理员账号（首次登录需改密码）
  emergency-stop [原因]   立即停止一切发布记录与 AI 任务（同步和检测继续）
  resume            解除紧急停止
TXT
}

cmd=${1:-status}
case "$cmd" in
    help|-h|--help) usage; exit 0 ;;
esac
need_root "$@"

case "$cmd" in
    install|deploy)
        lock_deployment
        check_repo
        ensure_system_packages
        deploy_all
        for port in "$(api_port)" "$(web_port)"; do
            if port_in_use "$port" && ! systemctl is-active -q "${SERVICES[@]}"; then
                fail "端口 $port 已被其他程序占用；请修改 $(env_file) 里的端口后重试"
            fi
        done
        # 先建管理员再启动服务：即使后面的健康检查失败，也不会留下一个无法登录的系统
        if ! as_service -c 'from sqlalchemy import text; from nr.db import get_engine; import sys; sys.exit(0 if get_engine().connect().execute(text("select count(*) from app_user")).scalar() else 1)'; then
            say '创建管理员账号'
            read -r -p '管理员用户名 [admin]: ' name
            as_service -m nr.cli create-admin --username "${name:-admin}"
        fi
        systemctl restart "${SERVICES[@]}"
        verify_services
        say "网页只监听本机：在你的电脑上运行  ssh -L $(web_port):localhost:$(web_port) root@服务器IP  然后打开 http://localhost:$(web_port)/zh"
        ;;
    update)
        lock_deployment
        check_repo
        branch=$(git -C "$REPO_DIR" branch --show-current)
        [[ -n "$branch" ]] || fail '当前不在任何分支上'
        git -C "$REPO_DIR" fetch -q origin "$branch"
        git -C "$REPO_DIR" merge-base --is-ancestor HEAD "origin/$branch" || fail "本地 $branch 有 origin 上没有的提交；停止更新"
        # AlphaScope 直接从工作区运行：不能替它拉代码
        if ! git -C "$REPO_DIR" diff --quiet HEAD "origin/$branch" -- . ':(exclude)newsroom'; then
            fail "AlphaScope 的代码也有更新：请先在 $REPO_DIR 运行 sudo ./start.sh update，再运行 sudo newsroom/start.sh deploy"
        fi
        git -C "$REPO_DIR" merge -q --ff-only "origin/$branch"
        exec bash "$NR_DIR/start.sh" --continue-update "$(git -C "$REPO_DIR" rev-parse HEAD)"
        ;;
    --continue-update)
        [[ -e /dev/fd/9 ]] || fail '此入口只允许由 update 调用'
        [[ "$(git -C "$REPO_DIR" rev-parse HEAD)" == "${2:-}" ]] || fail '更新过程中版本发生变化'
        ensure_system_packages
        deploy_all
        systemctl restart "${SERVICES[@]}"
        verify_services
        say '更新完成'
        ;;
    start|restart)
        [[ -f /etc/systemd/system/newsroom-api.service ]] || fail '尚未安装，请先运行 sudo newsroom/start.sh install'
        systemctl "$cmd" "${SERVICES[@]}"
        verify_services
        ;;
    stop)
        systemctl stop "${SERVICES[@]}"
        say '已停止（数据保留；AlphaScope 不受影响）'
        ;;
    status)
        systemctl --no-pager --lines=0 status "${SERVICES[@]}" | grep -E '●|Active:' || true
        as_service -m nr.cli status || warn '无法读取状态'
        ;;
    logs)
        case "${2:-all}" in
            api|worker|web) journalctl -u "newsroom-$2" -n 200 -f ;;
            all) journalctl -u newsroom-api -u newsroom-worker -u newsroom-web -n 200 -f ;;
            *) fail '用法：newsroom/start.sh logs [api|worker|web]' ;;
        esac
        ;;
    doctor)
        ok=0
        for s in "${SERVICES[@]}"; do
            if systemctl is-active -q "$s"; then say "$s 运行中"; else warn "$s 未运行"; ok=1; fi
        done
        if as_service -m nr.cli check-feed >/dev/null 2>&1; then say 'AlphaScope Feed 可访问'; else warn 'AlphaScope Feed 不可访问（检查 AlphaScope API 与令牌）'; ok=1; fi
        if fc-list :lang=zh family | grep -q 'Noto Sans CJK SC'; then say '中文字体已安装'; else warn '缺少 Noto Sans CJK 字体，图卡中文会显示为方框'; ok=1; fi
        free_gb=$(df -Pk "$(data_dir)" | awk 'END {printf "%.1f", $4/1024/1024}')
        say "数据目录可用空间 ${free_gb} GiB"
        exit $ok
        ;;
    backup)
        f="$(data_dir)/backups/newsroom-$(date -u +%Y%m%dT%H%M%SZ).dump"
        runuser -u postgres -- pg_dump -Fc newsroom > "$f"
        find "$(data_dir)/backups" -maxdepth 1 -name 'newsroom-*.dump' -printf '%T@ %p\n' | sort -rn \
            | tail -n +8 | cut -d' ' -f2- | xargs -r rm -f
        say "已备份到 $f（图卡文件在 $(data_dir)/assets，可另行同步）"
        ;;
    restore)
        f=${2:-}
        [[ -f "$f" ]] || fail '用法：newsroom/start.sh restore <备份文件>'
        systemctl stop "${SERVICES[@]}"
        runuser -u postgres -- pg_restore --clean --if-exists --no-owner --role=newsroom -d newsroom < "$f"
        as_service -m alembic upgrade head
        systemctl start "${SERVICES[@]}"
        verify_services
        ;;
    create-admin)
        read -r -p '管理员用户名 [admin]: ' name
        as_service -m nr.cli create-admin --username "${name:-admin}"
        ;;
    emergency-stop)
        as_service -m nr.cli emergency-stop on --reason "${2:-start.sh}"
        ;;
    resume)
        as_service -m nr.cli emergency-stop off
        ;;
    *)
        usage
        exit 1
        ;;
esac

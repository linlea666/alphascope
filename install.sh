#!/usr/bin/env bash
# AlphaScope 一键安装（Ubuntu 22.04 / 24.04）。可重复运行，不会删除已有数据。
#   curl -fsSL https://raw.githubusercontent.com/linlea666/alphascope/main/install.sh -o /tmp/alphascope-install.sh
#   sudo bash /tmp/alphascope-install.sh            # 默认装到 /opt/alphascope
# 与同机其他项目（如 /opt/bian、/opt/nofx）隔离：独立目录、独立系统用户、独立 systemd 服务、
# 独立的 Node.js（装在项目目录内）和独立的 PostgreSQL 数据库/角色，不用 Docker。
set -euo pipefail
umask 022
fail() { printf '安装失败：%s\n' "$*" >&2; exit 1; }
[[ "$(id -u)" == 0 ]] || fail '请使用 sudo bash install.sh [安装目录]'
[[ -r /etc/os-release ]] || fail '无法识别操作系统'
# shellcheck disable=SC1091
source /etc/os-release
[[ "${ID:-}" == ubuntu && "${VERSION_ID:-}" =~ ^(22\.04|24\.04)$ ]] \
    || fail "目前只支持 Ubuntu 22.04 / 24.04（当前：${PRETTY_NAME:-未知}）"
TARGET=${1:-/opt/alphascope}
BRANCH=${ALPHASCOPE_BRANCH:-main}
[[ "$TARGET" == /* && "$TARGET" != / ]] || fail '安装目录必须是绝对路径，且不能是根目录'
mkdir -p "$(dirname "$TARGET")"
command -v flock >/dev/null || fail '缺少 flock（apt install util-linux）'
exec 8>"$(dirname "$TARGET")/.alphascope-install.lock"
flock -n 8 || fail '另一个安装正在进行'
available=$(df -Pk "$(dirname "$TARGET")" | awk 'END {print $4}')
(( available >= 10 * 1024 * 1024 )) || fail '至少需要 10 GiB 可用磁盘（K 线、成交与原始归档会持续增长）'
memory=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
swap=$(awk '/SwapTotal/ {print $2}' /proc/meminfo)
(( memory >= 1024 * 1024 )) || fail '至少需要 1 GiB 内存'
if (( memory + swap < 2 * 1024 * 1024 )); then
    printf '注意：内存 + swap 不足 2 GiB，构建前端时可能失败；建议先添加 2 GiB swap。\n' >&2
fi
if [[ -e "$TARGET" && ! -d "$TARGET/.git" ]]; then
    [[ -d "$TARGET" && -z "$(find "$TARGET" -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail "$TARGET 已有其他内容，不覆盖"
fi
need=()
for c in git curl python3; do command -v "$c" >/dev/null || need+=("$c"); done
if (( ${#need[@]} )); then
    apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ca-certificates "${need[@]}"
fi
if [[ ! -d "$TARGET/.git" ]]; then
    git clone -q --branch "$BRANCH" --single-branch https://github.com/linlea666/alphascope.git "$TARGET"
fi
PROJECT_DIR=$(cd "$TARGET" && pwd -P)
# 校验来源后才执行目录里的任何脚本
case "$(git -C "$PROJECT_DIR" remote get-url origin)" in
    https://github.com/linlea666/alphascope|https://github.com/linlea666/alphascope.git|git@github.com:linlea666/alphascope.git|ssh://git@github.com/linlea666/alphascope.git) ;;
    *) fail '目标目录不是 linlea666/alphascope 仓库' ;;
esac
# 已安装的目录只做依赖、配置和服务刷新；更新代码请用 ./start.sh update
exec bash "$PROJECT_DIR/start.sh" install

#!/bin/sh
# /usr/local/bin/alphascope（由 start.sh 生成）—— 以 alphascope 用户身份运行后端命令行
# 例：alphascope swings ｜ alphascope fills --limit 5 ｜ alphascope enrich --address 0x...
HOME_DIR="@PROJECT_DIR@"
[ "$(id -u)" = 0 ] || { echo "请用 root（或 sudo）执行：sudo alphascope $*" >&2; exit 1; }
cd "$HOME_DIR/backend" || exit 1
# shellcheck disable=SC2016  # $1 / $@ 由内层 sh 展开
exec runuser -u @USER@ -- sh -c 'set -a; . "$1"; set +a; shift; exec "$@"' _ "$HOME_DIR/data/alphascope.env" \
  "$HOME_DIR/.venv/bin/python" -m app.cli "$@"

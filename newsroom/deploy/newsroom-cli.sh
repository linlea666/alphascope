#!/bin/sh
# /usr/local/bin/newsroom（由 newsroom/start.sh 生成）—— 以 newsroom 用户身份运行命令行
# 例：newsroom status ｜ newsroom sync ｜ newsroom jobs ｜ newsroom emergency-stop on --reason "..."
NR_DIR="@NR_DIR@"
[ "$(id -u)" = 0 ] || { echo "请用 root（或 sudo）执行：sudo newsroom $*" >&2; exit 1; }
cd "$NR_DIR/.runtime/current" || exit 1
# shellcheck disable=SC2016  # $1 / $@ 由内层 sh 展开
exec runuser -u @USER@ -- sh -c 'set -a; . "$1"; set +a; shift; exec "$@"' _ "$NR_DIR/data/newsroom.env" \
  "$NR_DIR/.venv/bin/python" -m nr.cli "$@"

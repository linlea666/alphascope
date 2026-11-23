#!/usr/bin/env bash
# newsroom/start.sh 用的函数。Newsroom 与 AlphaScope 在同一个仓库里，但运行时完全独立：
# 自己的系统用户、数据库、端口、虚拟环境、Node 和 systemd 服务；代码从发布快照运行，git pull 不会影响正在运行的服务。
# shellcheck disable=SC2034
SVC_USER=newsroom
SERVICES=(newsroom-api.service newsroom-worker.service newsroom-web.service)
NODE_MAJOR=22
DEFAULT_API_PORT=8620
DEFAULT_WEB_PORT=3300
# 与 AlphaScope 共用部署锁：两边的安装 / 更新永远不会同时进行
DEPLOY_LOCK=.git/alphascope-deploy.lock

fail() { printf '\033[1;31m错误：\033[0m%s\n' "$*" >&2; exit 1; }
say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m注意：\033[0m%s\n' "$*"; }

need_root() { [[ "$(id -u)" == 0 ]] || fail "请用 root 运行：sudo $0 $*"; }

data_dir() { printf '%s/data' "$NR_DIR"; }
env_file() { printf '%s/newsroom.env' "$(data_dir)"; }
state_dir() { printf '%s/.state' "$(data_dir)"; }
runtime_dir() { printf '%s/.runtime' "$NR_DIR"; }
alphascope_env() { printf '%s/data/alphascope.env' "$REPO_DIR"; }

env_get() {
    local key=$1 default=${2:-} file=${3:-}
    local line
    line=$(grep -E "^${key}=" "${file:-$(env_file)}" 2>/dev/null | tail -1 || true)
    if [[ -n "$line" ]]; then printf '%s' "${line#*=}"; else printf '%s' "$default"; fi
}

env_set() {
    local key=$1 value=$2 file
    file=$(env_file)
    if grep -qE "^${key}=" "$file"; then
        sed -i "s#^${key}=.*#${key}=${value}#" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

api_port() { env_get NEWSROOM_API_PORT "$DEFAULT_API_PORT"; }
web_port() { env_get NEWSROOM_WEB_PORT "$DEFAULT_WEB_PORT"; }
random_secret() { python3 -c 'import secrets; print(secrets.token_hex(int(__import__("sys").argv[1])))' "${1:-16}"; }

port_in_use() {
    python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)
sys.exit(1)
PY
}

lock_deployment() {
    command -v flock >/dev/null || fail '需要 flock（apt install util-linux）'
    exec 9>"$REPO_DIR/$DEPLOY_LOCK"
    flock -w 900 9 || fail '另一个安装 / 更新（AlphaScope 或 Newsroom）正在进行'
}

check_repo() {
    [[ -z "$(git -C "$REPO_DIR" status --porcelain --untracked-files=no)" ]] \
        || fail "仓库里有被修改过的文件；请先处理（git -C $REPO_DIR status），脚本不会自动覆盖"
}

# ---------- 系统依赖 ----------

python_bin() {
    local p
    for p in python3.13 python3.12 python3.11 python3; do
        if command -v "$p" >/dev/null && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
            command -v "$p"
            return 0
        fi
    done
    return 1
}

ensure_system_packages() {
    # libcairo2 + fonts-noto-cjk：数据图卡渲染（SVG → PNG）和中文字体
    local want=(ca-certificates util-linux xz-utils git curl libcairo2 fonts-noto-cjk fontconfig) missing=() p
    for p in "${want[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
    if (( ${#missing[@]} )); then
        say "安装系统依赖：${missing[*]}"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
    fi
    python_bin >/dev/null || fail '需要 Python 3.11+（AlphaScope 安装时会准备好）'
    command -v psql >/dev/null || fail '需要本机 PostgreSQL（AlphaScope 安装时会准备好）'
}

ensure_node() {
    local dir arch base shasum file
    dir="$(runtime_dir)/node"
    if [[ -x "$dir/bin/node" ]] && [[ "$("$dir/bin/node" -p 'process.versions.node.split(".")[0]')" == "$NODE_MAJOR" ]]; then
        return 0
    fi
    case "$(uname -m)" in
        x86_64) arch=x64 ;;
        aarch64|arm64) arch=arm64 ;;
        *) fail "不支持的 CPU 架构：$(uname -m)" ;;
    esac
    base="https://nodejs.org/dist/latest-v${NODE_MAJOR}.x"
    say "下载 Node.js ${NODE_MAJOR}（$arch）"
    shasum=$(curl -fsSL "$base/SHASUMS256.txt") || fail '无法下载 Node.js 校验文件'
    file=$(awk -v a="linux-${arch}.tar.xz" '$2 ~ a"$" {print $2; exit}' <<<"$shasum")
    [[ -n "$file" ]] || fail '找不到对应的 Node.js 安装包'
    mkdir -p "$(runtime_dir)"
    curl -fsSL "$base/$file" -o "$(runtime_dir)/$file" || fail 'Node.js 下载失败'
    (cd "$(runtime_dir)" && grep " $file\$" <<<"$shasum" | sha256sum -c --quiet -) || fail 'Node.js 安装包校验失败'
    rm -rf "$dir.new"
    mkdir -p "$dir.new"
    tar -xJf "$(runtime_dir)/$file" -C "$dir.new" --strip-components=1
    rm -rf "${dir:?}" "$(runtime_dir)/${file:?}"
    mv "$dir.new" "$dir"
}

# ---------- 用户、目录、配置 ----------

ensure_user_and_dirs() {
    if ! id "$SVC_USER" >/dev/null 2>&1; then
        say "创建系统用户 $SVC_USER"
        useradd --system --home-dir "$(data_dir)" --no-create-home --shell /usr/sbin/nologin "$SVC_USER"
    fi
    install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$(data_dir)" "$(data_dir)/assets"
    install -d -m 700 -o root -g root "$(state_dir)" "$(data_dir)/backups"
    install -d -m 755 "$(runtime_dir)" "$(runtime_dir)/releases" "$(runtime_dir)/builds"
}

ensure_env_file() {
    local file token
    file=$(env_file)
    if [[ ! -f "$file" ]]; then
        say "生成配置 $file"
        install -m 640 -o root -g "$SVC_USER" /dev/null "$file"
        cat > "$file" <<EOF
# AlphaScope Newsroom 配置（由 newsroom/start.sh 生成；修改后执行 sudo newsroom/start.sh restart）
NEWSROOM_API_PORT=$DEFAULT_API_PORT
NEWSROOM_WEB_PORT=$DEFAULT_WEB_PORT
# 网页默认只监听本机：用 SSH 隧道访问，或在前面放带 TLS 的反向代理后改成 0.0.0.0 并设 NEWSROOM_COOKIE_SECURE=true
# 用 localhost 而不是 127.0.0.1：Next.js 内部重写请求会访问 localhost，两者必须解析到同一个地址
NEWSROOM_WEB_HOST=localhost
NEWSROOM_COOKIE_SECURE=false
EOF
    fi
    # 早期版本写的是 127.0.0.1，会让 Next.js 内部重写连不上（页面 500、进程崩溃）
    [[ "$(env_get NEWSROOM_WEB_HOST)" == 127.0.0.1 ]] && env_set NEWSROOM_WEB_HOST localhost
    env_set NEWSROOM_API_BASE "http://127.0.0.1:$(api_port)"
    env_set NEWSROOM_SECRET_KEY_PATH "$(data_dir)/secret.key"
    env_set NEWSROOM_ASSET_DIR "$(data_dir)/assets"
    # AlphaScope 的只读 Feed：地址和令牌从 AlphaScope 的配置读取；这里只读，从不修改 AlphaScope 的配置
    token=$(env_get ALPHASCOPE_FEED_TOKEN "" "$(alphascope_env)")
    [[ -n "$token" ]] || fail "AlphaScope 还没有启用只读 Feed（$(alphascope_env) 里没有 ALPHASCOPE_FEED_TOKEN）；请先在仓库根目录运行 sudo ./start.sh update"
    env_set NEWSROOM_FEED_TOKEN "$token"
    env_set NEWSROOM_FEED_BASE_URL "http://127.0.0.1:$(env_get ALPHASCOPE_API_PORT 8610 "$(alphascope_env)")"
}

ensure_database() {
    if [[ -n "$(env_get NEWSROOM_DATABASE_URL)" ]]; then
        return 0
    fi
    local pw
    pw=$(random_secret 24)
    say '创建数据库 newsroom（独立角色，不授予任何 AlphaScope 表的权限）'
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q -v pw="$pw" <<'SQL'
SELECT format('CREATE ROLE newsroom LOGIN PASSWORD %L', :'pw') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'newsroom') \gexec
SELECT format('ALTER ROLE newsroom PASSWORD %L', :'pw') \gexec
SELECT 'CREATE DATABASE newsroom OWNER newsroom' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'newsroom') \gexec
SQL
    env_set NEWSROOM_DATABASE_URL "postgresql+psycopg://newsroom:${pw}@127.0.0.1:5432/newsroom"
}

# ---------- 发布快照 ----------

file_hash() { sha256sum "$1" | awk '{print $1}'; }

release_backend() {
    # 后端代码打成快照运行：仓库被 AlphaScope 更新拉到新版本时，正在运行的 Newsroom 不受影响
    local tree dest venv req stamp
    tree=$(git -C "$REPO_DIR" rev-parse HEAD:newsroom/backend)
    dest="$(runtime_dir)/releases/${tree:0:12}"
    if [[ ! -d "$dest/newsroom/backend" ]]; then
        say "准备后端发布快照 ${tree:0:12}"
        rm -rf "$dest.tmp"
        mkdir -p "$dest.tmp"
        git -C "$REPO_DIR" archive HEAD newsroom/backend | tar -x -C "$dest.tmp"
        mv -T "$dest.tmp" "$dest"
    fi
    venv="$NR_DIR/.venv"
    req="$dest/newsroom/backend/requirements.txt"
    stamp="$(state_dir)/requirements.sha256"
    [[ -x "$venv/bin/python" ]] || { say '创建 Python 虚拟环境'; "$(python_bin)" -m venv "$venv"; }
    if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$(file_hash "$req")" ]]; then
        say '安装后端依赖'
        "$venv/bin/pip" install -q --upgrade pip
        "$venv/bin/pip" install -q -r "$req"
        file_hash "$req" > "$stamp"
    fi
    ln -sfn "$dest/newsroom/backend" "$(runtime_dir)/current.new"
    mv -T "$(runtime_dir)/current.new" "$(runtime_dir)/current"
    find "$(runtime_dir)/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n +4 \
        | cut -d' ' -f2- | xargs -r rm -rf
}

as_service() {
    # 以 newsroom 用户、加载 env 文件后在当前发布快照里运行后端命令
    # shellcheck disable=SC2016  # $1 / $@ 由内层 bash 展开
    (cd "$(runtime_dir)/current" && runuser -u "$SVC_USER" -- bash -c \
        'set -a; source "$1"; set +a; shift; exec "$@"' _ "$(env_file)" "$NR_DIR/.venv/bin/python" "$@")
}

migrate() {
    # 只停 newsroom-worker（迁移会加锁）；AlphaScope 的服务从不触碰
    if systemctl is-active -q newsroom-worker.service 2>/dev/null; then systemctl stop newsroom-worker.service; fi
    say '数据库迁移'
    as_service -m alembic upgrade head
    as_service -m nr.cli seed >/dev/null
}

build_frontend() {
    local tree current dest node registry
    tree=$(git -C "$REPO_DIR" rev-parse HEAD:newsroom/frontend)
    current="$(runtime_dir)/web"
    if [[ -f "$(state_dir)/frontend.tree" && "$(cat "$(state_dir)/frontend.tree")" == "$tree" && -e "$current/server.js" ]]; then
        return 0
    fi
    ensure_node
    node="$(runtime_dir)/node/bin"
    dest="$(runtime_dir)/builds/${tree:0:12}"
    registry=${NPM_REGISTRY:-https://registry.npmjs.org/}
    say "构建前端（${tree:0:12}，约 1–3 分钟）"
    rm -rf "$dest"
    mkdir -p "$dest"
    git -C "$REPO_DIR" archive HEAD newsroom/frontend | tar -x -C "$dest"
    (
        cd "$dest/newsroom/frontend"
        export PATH="$node:$PATH" NEXT_TELEMETRY_DISABLED=1
        npm ci --no-audit --no-fund --loglevel=error --registry="$registry" --replace-registry-host=always
        npm run build > "$dest/build.log" 2>&1 || { tail -30 "$dest/build.log"; exit 1; }
    ) || fail '前端构建失败（内存不足时可先加 swap 再重试）'
    local app="$dest/newsroom/frontend"
    cp -r "$app/.next/static" "$app/.next/standalone/.next/static"
    [[ -d "$app/public" ]] && cp -r "$app/public" "$app/.next/standalone/public"
    install -d -o "$SVC_USER" -g "$SVC_USER" "$app/.next/standalone/.next/cache"
    ln -sfn "$app/.next/standalone" "$current.new"
    mv -T "$current.new" "$current"
    printf '%s' "$tree" > "$(state_dir)/frontend.tree"
    find "$(runtime_dir)/builds" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n +3 \
        | cut -d' ' -f2- | xargs -r rm -rf
}

# ---------- systemd ----------

install_units() {
    local unit
    for unit in "${SERVICES[@]}"; do
        sed -e "s#@NR_DIR@#$NR_DIR#g" -e "s#@USER@#$SVC_USER#g" "$NR_DIR/deploy/systemd/$unit" > "/etc/systemd/system/$unit"
    done
    sed -e "s#@NR_DIR@#$NR_DIR#g" -e "s#@USER@#$SVC_USER#g" "$NR_DIR/deploy/newsroom-cli.sh" > /usr/local/bin/newsroom
    chmod 755 /usr/local/bin/newsroom
    systemctl daemon-reload
    systemctl enable -q "${SERVICES[@]}"
}

verify_services() {
    local i api web
    api=$(api_port)
    web=$(web_port)
    for i in $(seq 1 45); do
        if curl -fsS "http://127.0.0.1:$api/healthz" >/dev/null 2>&1 \
            && curl -fsS -o /dev/null "http://localhost:$web/zh/login" 2>/dev/null; then
            say "健康检查通过（API :$api，网页 :$web）"
            return 0
        fi
        sleep 1
    done
    systemctl --no-pager status "${SERVICES[@]}" | tail -30 || true
    fail '服务 45 秒内没有通过健康检查；查看日志：sudo newsroom/start.sh logs'
}

deploy_all() {
    ensure_user_and_dirs
    ensure_env_file
    ensure_database
    release_backend
    migrate
    build_frontend
    install_units
}

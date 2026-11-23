#!/usr/bin/env bash
# install.sh / start.sh 共用的函数。所有路径都以脚本所在的仓库为根（$PROJECT_DIR）。
# shellcheck disable=SC2034
REPO_RE='^(https://github\.com/linlea666/alphascope(\.git)?|git@github\.com:linlea666/alphascope(\.git)?|ssh://git@github\.com/linlea666/alphascope(\.git)?)$'
SVC_USER=alphascope
SERVICES=(alphascope-api.service alphascope-worker.service alphascope-live.service alphascope-web.service)
NODE_MAJOR=22
DEFAULT_API_PORT=8610
DEFAULT_WEB_PORT=3200

fail() { printf '\033[1;31m错误：\033[0m%s\n' "$*" >&2; exit 1; }
say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m注意：\033[0m%s\n' "$*"; }

need_root() { [[ "$(id -u)" == 0 ]] || fail "请用 root 运行：sudo $0 $*"; }

data_dir() { printf '%s/data' "$PROJECT_DIR"; }
env_file() { printf '%s/alphascope.env' "$(data_dir)"; }
state_dir() { printf '%s/.state' "$(data_dir)"; }
runtime_dir() { printf '%s/.runtime' "$PROJECT_DIR"; }

check_repo() {
    [[ "$(git -C "$PROJECT_DIR" rev-parse --show-toplevel)" == "$PROJECT_DIR" ]] || fail '目标不是独立的 alphascope 仓库'
    local remote
    remote=$(git -C "$PROJECT_DIR" remote get-url origin)
    [[ "$remote" =~ $REPO_RE ]] || fail "origin 必须是 linlea666/alphascope（当前：$remote）；不会自动修改仓库来源"
    [[ -z "$(git -C "$PROJECT_DIR" status --porcelain --untracked-files=no)" ]] \
        || fail '仓库里有被修改过的文件；请先处理（git -C '"$PROJECT_DIR"' status），脚本不会自动覆盖'
}

lock_deployment() {
    command -v flock >/dev/null || fail '需要 flock（apt install util-linux）'
    exec 9>"$PROJECT_DIR/.git/alphascope-deploy.lock"
    flock -n 9 || fail '另一个安装 / 更新正在进行'
}

# 读取 data/alphascope.env 里的一个值（不 source 整个文件，避免执行其中的内容）
env_get() {
    local key=$1 default=${2:-}
    local line
    line=$(grep -E "^${key}=" "$(env_file)" 2>/dev/null | tail -1 || true)
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

api_port() { env_get ALPHASCOPE_API_PORT "$DEFAULT_API_PORT"; }
web_port() { env_get ALPHASCOPE_WEB_PORT "$DEFAULT_WEB_PORT"; }

port_in_use() {
    python3 - "$1" <<'PY'
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # 只在真的有程序监听时才算占用（忽略 TIME_WAIT）
try:
    s.bind(("0.0.0.0", int(sys.argv[1])))
except OSError:
    sys.exit(0)
sys.exit(1)
PY
}

random_secret() { python3 -c 'import secrets; print(secrets.token_hex(int(__import__("sys").argv[1])))' "${1:-16}"; }

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
    local want=(ca-certificates util-linux xz-utils git curl) missing=() p
    command -v psql >/dev/null || want+=(postgresql)
    for p in "${want[@]}"; do dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p"); done
    if (( ${#missing[@]} )); then
        say "安装系统依赖：${missing[*]}"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${missing[@]}"
    fi
    if ! python_bin >/dev/null; then
        # Ubuntu 22.04 自带 3.10；用 deadsnakes 提供的 3.11（只新增 python3.11，不替换系统 python3）
        say '安装 Python 3.11（deadsnakes）'
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq software-properties-common
        add-apt-repository -y ppa:deadsnakes/ppa >/dev/null
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.11 python3.11-venv
    fi
    local py
    py=$(python_bin) || fail '需要 Python 3.11+'
    if ! "$py" -c 'import ensurepip' 2>/dev/null; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$(basename "$py")-venv"
    fi
}

# Node.js 只装在项目目录里（官方二进制 + SHA256 校验），不影响同机其他项目
ensure_node() {
    local dir arch os_arch base shasum file
    dir="$(runtime_dir)/node"
    if [[ -x "$dir/bin/node" ]] && [[ "$("$dir/bin/node" -p 'process.versions.node.split(".")[0]')" == "$NODE_MAJOR" ]]; then
        return 0
    fi
    os_arch=$(uname -m)
    case "$os_arch" in
        x86_64) arch=x64 ;;
        aarch64|arm64) arch=arm64 ;;
        *) fail "不支持的 CPU 架构：$os_arch" ;;
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
    install -d -m 750 -o "$SVC_USER" -g "$SVC_USER" "$(data_dir)" "$(data_dir)/raw"
    install -d -m 700 -o root -g root "$(state_dir)" "$(data_dir)/backups"
    install -d -m 755 "$(runtime_dir)"
}

ensure_env_file() {
    local file
    file=$(env_file)
    if [[ ! -f "$file" ]]; then
        say "生成配置 $file"
        install -m 640 -o root -g "$SVC_USER" /dev/null "$file"
        cat > "$file" <<EOF
# AlphaScope 服务器配置（由安装脚本生成；可以手动修改，修改后执行 ./start.sh restart）
ALPHASCOPE_API_PORT=$DEFAULT_API_PORT
ALPHASCOPE_WEB_PORT=$DEFAULT_WEB_PORT
ALPHASCOPE_RAW_STORE_DIR=$(data_dir)/raw
# 本部署可使用的 Hyperliquid 限流份额（每 IP 每分钟 1200 权重 × 这个比例）
ALPHASCOPE_HL_WEIGHT_SAFETY=0.5
# 网页访问账号:密码（留空则不需要登录，不建议）
ALPHASCOPE_WEB_AUTH=admin:$(random_secret 12)
EOF
    fi
    env_set API_BASE_URL "http://127.0.0.1:$(api_port)"
    # encryption key for secrets stored in the database (SMTP password); created by the service on first use
    [[ -n "$(env_get ALPHASCOPE_SECRET_KEY_PATH)" ]] || env_set ALPHASCOPE_SECRET_KEY_PATH "$(data_dir)/secret.key"
    # 只读 Feed（给 newsroom 用）的访问令牌；只在没有这一行时生成，手动改成空值即关闭 Feed
    grep -qE '^ALPHASCOPE_FEED_TOKEN=' "$file" || env_set ALPHASCOPE_FEED_TOKEN "$(random_secret 32)"
}

# 本机 PostgreSQL：独立的角色和数据库；密码只保存在 data/alphascope.env
ensure_database() {
    if [[ -n "$(env_get ALPHASCOPE_DATABASE_URL)" ]]; then
        return 0  # 已配置（包括手动指向外部数据库）
    fi
    command -v pg_lsclusters >/dev/null || fail '没有找到 Ubuntu 的 PostgreSQL；如需使用外部数据库，请在 data/alphascope.env 里设置 ALPHASCOPE_DATABASE_URL 后重试'
    systemctl enable -q --now postgresql
    local pw
    pw=$(random_secret 24)
    say '创建数据库 alphascope'
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -q -v pw="$pw" <<'SQL'
SELECT format('CREATE ROLE alphascope LOGIN PASSWORD %L', :'pw') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'alphascope') \gexec
SELECT format('ALTER ROLE alphascope PASSWORD %L', :'pw') \gexec
SELECT 'CREATE DATABASE alphascope OWNER alphascope' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'alphascope') \gexec
SQL
    env_set ALPHASCOPE_DATABASE_URL "postgresql+psycopg://alphascope:${pw}@127.0.0.1:5432/alphascope"
}

# ---------- 构建 ----------

file_hash() { sha256sum "$1" | awk '{print $1}'; }

build_backend() {
    local venv="$PROJECT_DIR/.venv" req="$PROJECT_DIR/backend/requirements.txt" stamp
    stamp="$(state_dir)/requirements.sha256"
    if [[ ! -x "$venv/bin/python" ]]; then
        say '创建 Python 虚拟环境'
        "$(python_bin)" -m venv "$venv"
    fi
    if [[ ! -f "$stamp" || "$(cat "$stamp")" != "$(file_hash "$req")" ]]; then
        say '安装后端依赖'
        "$venv/bin/pip" install -q --upgrade pip
        "$venv/bin/pip" install -q -r "$req"
        file_hash "$req" > "$stamp"
    fi
}

as_service() {
    # 以服务用户身份、加载 env 文件后运行后端命令（工作目录 backend/）
    # shellcheck disable=SC2016  # $1 / $@ 由内层 bash 展开
    (cd "$PROJECT_DIR/backend" && runuser -u "$SVC_USER" -- bash -c \
        'set -a; source "$1"; set +a; shift; exec "$@"' _ "$(env_file)" "$PROJECT_DIR/.venv/bin/python" "$@")
}

migrate() {
    # 迁移会给表加锁；先停 worker 和 live，避免与正在写入的任务互相等待（死锁）。随后统一重启。
    local svc
    for svc in alphascope-worker.service alphascope-live.service; do
        if systemctl is-active -q "$svc" 2>/dev/null; then systemctl stop "$svc"; fi
    done
    say '数据库迁移'
    as_service -m alembic upgrade head
}

# 前端在独立目录构建，完成后再切换 .runtime/web 链接：构建期间旧版本继续服务
build_frontend() {
    local tree current dest node npm registry
    tree=$(git -C "$PROJECT_DIR" rev-parse HEAD:frontend)
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
    git -C "$PROJECT_DIR" archive HEAD frontend | tar -x -C "$dest"
    (
        cd "$dest/frontend"
        export PATH="$node:$PATH" NEXT_TELEMETRY_DISABLED=1
        npm ci --no-audit --no-fund --loglevel=error --registry="$registry" --replace-registry-host=always
        npm run build > "$dest/build.log" 2>&1 || { tail -30 "$dest/build.log"; exit 1; }
    ) || fail '前端构建失败（内存不足时可先加 swap 再重试；完整日志见上方）'
    cp -r "$dest/frontend/.next/static" "$dest/frontend/.next/standalone/.next/static"
    [[ -d "$dest/frontend/public" ]] && cp -r "$dest/frontend/public" "$dest/frontend/.next/standalone/public"
    install -d -o "$SVC_USER" -g "$SVC_USER" "$dest/frontend/.next/standalone/.next/cache"
    ln -sfn "$dest/frontend/.next/standalone" "$current.new"
    mv -T "$current.new" "$current"
    printf '%s' "$tree" > "$(state_dir)/frontend.tree"
    # 只保留最近 2 次构建
    find "$(runtime_dir)/builds" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n +3 \
        | cut -d' ' -f2- | xargs -r rm -rf
}

# ---------- systemd ----------

install_units() {
    local unit
    for unit in "${SERVICES[@]}"; do
        sed -e "s#@PROJECT_DIR@#$PROJECT_DIR#g" -e "s#@USER@#$SVC_USER#g" \
            "$PROJECT_DIR/deploy/systemd/$unit" > "/etc/systemd/system/$unit"
    done
    sed -e "s#@PROJECT_DIR@#$PROJECT_DIR#g" -e "s#@USER@#$SVC_USER#g" "$PROJECT_DIR/deploy/alphascope-cli.sh" \
        > /usr/local/bin/alphascope
    chmod 755 /usr/local/bin/alphascope
    systemctl daemon-reload
    systemctl enable -q "${SERVICES[@]}"
}

web_get() {
    # 带上网页登录凭证访问本机网页（凭证可能为空）
    local auth
    auth=$(env_get ALPHASCOPE_WEB_AUTH)
    if [[ -n "$auth" ]]; then curl -fsS --user "$auth" -o /dev/null "$1"; else curl -fsS -o /dev/null "$1"; fi
}

verify_services() {
    local i api web
    api=$(api_port)
    web=$(web_port)
    for i in $(seq 1 45); do
        if curl -fsS "http://127.0.0.1:$api/healthz" >/dev/null 2>&1 && web_get "http://127.0.0.1:$web/zh" 2>/dev/null; then
            say "健康检查通过（API :$api，网页 :$web）"
            return 0
        fi
        sleep 1
    done
    systemctl --no-pager status "${SERVICES[@]}" | tail -30 || true
    fail '服务 45 秒内没有通过健康检查；查看日志：./start.sh logs'
}

print_access() {
    local ip auth
    ip=$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')
    auth=$(env_get ALPHASCOPE_WEB_AUTH)
    say "网页地址：http://${ip:-服务器IP}:$(web_port)/zh"
    [[ -n "$auth" ]] && say "登录账号：${auth%%:*}    密码：${auth#*:}"
    warn "请在云服务器安全组里放行 TCP $(web_port)，建议只允许你自己的 IP 访问；API 端口只监听本机，无需放行。"
}

deploy_all() {
    ensure_user_and_dirs
    ensure_env_file
    ensure_database
    build_backend
    migrate
    build_frontend
    install_units
}

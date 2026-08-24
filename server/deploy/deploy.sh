#!/usr/bin/env bash
#
# 把后端（FastAPI + Postgres + Caddy）部署到 antonycasa.weelume.com。
#
# 为什么是「本地打包再上传」而不是在服务器上 build/pull：
# 那台机器连不上 Docker Hub（registry-1.docker.io 直连 i/o timeout），
# 但 GitHub 和 Let's Encrypt 通。所以镜像一律本地准备好，docker save 后 scp 过去 load。
#
# 为什么全程只用 deploy 用户：deploy 没有免密 sudo，但已在 docker 组里，
# 建目录、装 compose 插件（用户级 ~/.docker/cli-plugins）、跑容器都不需要 root。
#
# 用法（在 WSL 里跑，Windows 侧的 Git Bash 没有 docker）：
#   wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh
#
#   --skip-build   跳过 API 镜像构建，直接部署本地已有镜像
#   --skip-web     跳过前端产物同步（只更新后端时用）
#   --dev-only     只更新开发环境，不碰生产。开发环境是**另一个目录、另一个
#                  compose 项目**（~/workspace/antony-casa-dev），镜像 tag 也另一个
#                  （antony-casa-api:dev），生产的 api 钉在 latest 上不动
#   --logs         部署完跟一段容器日志
#
# 两份前端产物：antony-web/dist 出官网（根域名），website/dist 出后台（admin 子域名）。
# 都不在这里构建：node_modules 是 Windows 侧 pnpm 装的，原生二进制不能在 WSL 里跑。
# 改了前端要先在 Windows 侧 `pnpm build`，本脚本只负责同步 dist。
#
# 首次部署要从 GitHub 下 compose 二进制，WSL 里没配代理的话先：
#   export HTTPS_PROXY=http://192.168.32.1:7078 HTTP_PROXY=http://192.168.32.1:7078

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"          # docker build 上下文
REPO_DIR="$(cd "$SERVER_DIR/.." && pwd)"
# 两份前端产物：官网出根域名，后台出 admin 子域名，见 Caddyfile
SITE_DIST="$REPO_DIR/antony-web/dist"
ADMIN_DIST="$REPO_DIR/website/dist"

DEPLOY_HOST="${DEPLOY_HOST:-deploy@antonycasa.weelume.com}"
DEPLOY_PORT="${DEPLOY_PORT:-22}"
DEPLOY_KEY="${DEPLOY_KEY:-}"
REMOTE_DIR="${REMOTE_DIR:-/home/deploy/workspace/antony-casa}"
# 开发环境是**另一个目录、另一个 compose 项目**。分开是因为混在一起时，
# 在那个目录里 `docker compose down` 会把 caddy 和 postgres 一起停掉——
# 有人只想重启开发环境，结果能把官网干下线
REMOTE_DEV_DIR="${REMOTE_DEV_DIR:-/home/deploy/workspace/antony-casa-dev}"
# 与 Caddyfile 里的三个站点块保持一致，健康检查要靠它们匹配到站点并通过证书校验
SITE_DOMAIN="${SITE_DOMAIN:-antonycasa.weelume.com}"
ADMIN_DOMAIN="${ADMIN_DOMAIN:-admin.antonycasa.weelume.com}"
# 开发环境的两个域名：接口（小程序预览打这个）与后台（给开发库配数据）
DEV_DOMAIN="${DEV_DOMAIN:-dev.antonycasa.weelume.com}"
DEV_ADMIN_DOMAIN="${DEV_ADMIN_DOMAIN:-dev.admin.antonycasa.weelume.com}"
IMAGE="${IMAGE:-antony-casa-api}"
TAG="${TAG:-latest}"
BUILD_PROXY="${BUILD_PROXY:-}"
# 版本写死而不是查 GitHub latest：部署要可重复，接口限流时也不该悄悄换一个版本
COMPOSE_VERSION="${COMPOSE_VERSION:-v5.4.0}"
BASE_IMAGES=(postgres:18-alpine caddy:2-alpine)

SKIP_BUILD="false"
SKIP_WEB="false"
SHOW_LOGS="false"
DEV_ONLY="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -H|--host)     DEPLOY_HOST="$2"; shift 2 ;;
    -p|--port)     DEPLOY_PORT="$2"; shift 2 ;;
    -i|--identity) DEPLOY_KEY="$2";  shift 2 ;;
    --proxy)       BUILD_PROXY="$2"; shift 2 ;;
    --skip-build)  SKIP_BUILD="true"; shift ;;
    --skip-web)    SKIP_WEB="true";   shift ;;
    --dev-only)    DEV_ONLY="true";   shift ;;
    --logs)        SHOW_LOGS="true";  shift ;;
    -h|--help)     sed -n '2,34p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

# --dev-only：只更新开发环境，一个字节都不碰生产。三层都分开：
#   镜像 tag  推成 antony-casa-api:dev，生产的 api 钉在 latest 上
#   compose   另一个目录、另一个项目（~/workspace/antony-casa-dev）
#   前端产物  发到开发目录自己的 site/dist、website/dist
# 这是 --skip-build/--skip-web 做不到的事——那两个开关不阻止换容器，
# 本地镜像照样会覆盖 latest 并替换生产 api（见 README）。
#
# 前端**不再强制跳过**：早先 dev 站点复用生产那份 dist，于是这里只能 SKIP_WEB，
# 结果是开发环境永远拿不到新前端（除非跑一次会替换生产 api 的全量部署）。
# 现在两边各有一份产物，dev 也该跟着更新
if [[ "$DEV_ONLY" == "true" ]]; then
  TAG="${DEV_TAG:-dev}"
fi

IMAGE_REF="$IMAGE:$TAG"

SSH_OPTS=(-p "$DEPLOY_PORT" -o BatchMode=yes)
SCP_OPTS=(-P "$DEPLOY_PORT" -o BatchMode=yes)
if [[ -n "$DEPLOY_KEY" ]]; then SSH_OPTS+=(-i "$DEPLOY_KEY"); SCP_OPTS+=(-i "$DEPLOY_KEY"); fi
ssh_cmd() { ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "$@"; }

# 生产项目（caddy / postgres / api）
remote_compose() { ssh_cmd "cd '$REMOTE_DIR' && docker compose $*"; }
# 开发项目（只有一个 api 容器）
remote_dev_compose() { ssh_cmd "cd '$REMOTE_DEV_DIR' && docker compose $*"; }

step() { echo; echo "==> $*"; }

# 本次要往哪个目录发。--dev-only 走开发项目，其余走生产
TARGET_DIR="$REMOTE_DIR"
[[ "$DEV_ONLY" == "true" ]] && TARGET_DIR="$REMOTE_DEV_DIR"

echo "镜像:   $IMAGE_REF"
echo "目标:   $DEPLOY_HOST:$TARGET_DIR"
echo "上下文: $SERVER_DIR"

# ---------------------------------------------------------------- 前置检查
step "[1/8] 前置检查"
command -v docker >/dev/null || { echo "本机没有 docker（这个脚本要在 WSL 里跑）" >&2; exit 1; }
if [[ "$SKIP_WEB" != "true" ]]; then
  for d in "$SITE_DIST" "$ADMIN_DIST"; do
    [[ -f "$d/index.html" ]] && continue
    echo "找不到 $d/index.html" >&2
    echo "先在 Windows 侧构建前端：cd antony-web && pnpm build；cd website && pnpm build" >&2
    echo "（或加 --skip-web 只更新后端）" >&2
    exit 1
  done
fi
ssh_cmd "true" || { echo "SSH 连不上 $DEPLOY_HOST" >&2; exit 1; }

# 超管账号必须能进到容器里，而且必须在动手之前就查出来。
#
# Dockerfile 只 COPY 了 app/，容器里没有 .env 文件，ADMIN_SUPER_USERNAME /
# ADMIN_SUPER_PASSWORD 只能经 docker-compose.yml 的 environment 从远端 .env 进去。
# 少任何一项，app/admin_auth.py 在**模块导入期**就 raise——不是启动后报错，是
# import app.main 直接炸，容器起不来、restart: unless-stopped 无限重启。
# 而 Caddyfile 的 (shared) 片段把两个域名的 /api/* 都反代到同一个 api 服务，
# 所以那不只是后台挂，是官网预约、服务申请、小程序接口一起挂。
# 等到第 8 步 up -d 之后才发现就晚了：那时旧容器已经被替换掉，现网是整体不可用
#
# 开发环境同理，只是炸的范围小一圈：它挂了只有两个 dev 域名不可用，生产不受影响。
# 两个环境现在各有一份 .env、key 名相同（环境靠目录区分，不靠变量名前缀），
# 所以这里按本次的目标目录去查那一份。
step_env_file="$TARGET_DIR/.env"
missing_keys=""
if ssh_cmd "test -f '$TARGET_DIR/.env'"; then
  # 缺了会让容器起不来的那几项。开发环境多查两个库相关的：它连的是
  # 生产项目里的 postgres，密码填错就是启动后连不上库
  keys=(ADMIN_SUPER_USERNAME ADMIN_SUPER_PASSWORD)
  [[ "$DEV_ONLY" == "true" ]] && keys+=(POSTGRES_PASSWORD POSTGRES_DB WX_APPID)
  for key in "${keys[@]}"; do
    ssh_cmd "grep -Eq '^[[:space:]]*$key=[^[:space:]]' '$TARGET_DIR/.env'" \
      || missing_keys="$missing_keys $key"
  done
elif [[ "$DEV_ONLY" == "true" ]]; then
  # 开发环境的 .env 不自动生成：它要填的是「和生产不同的超管账号」，
  # 没有任何地方能替你想出这个值来。照 deploy/dev/.env.example 手写一份
  echo "$TARGET_DIR/.env 不存在。" >&2
  echo "" >&2
  echo "开发环境的 .env 要手写一份（不会自动生成——它要填的超管账号必须和生产不同）：" >&2
  echo "  ssh $DEPLOY_HOST 'mkdir -p $TARGET_DIR'" >&2
  echo "  scp $SCRIPT_DIR/dev/.env.example $DEPLOY_HOST:$TARGET_DIR/.env" >&2
  echo "  ssh $DEPLOY_HOST 'chmod 600 $TARGET_DIR/.env && vi $TARGET_DIR/.env'" >&2
  exit 1
else
  # 远端还没有生产 .env，第 6 步会用本地 server/.env 生成一份，所以查的是本地这份
  step_env_file="$SERVER_DIR/.env"
  for key in ADMIN_SUPER_USERNAME ADMIN_SUPER_PASSWORD; do
    grep -Eq "^[[:space:]]*$key=[^[:space:]]" "$SERVER_DIR/.env" 2>/dev/null \
      || missing_keys="$missing_keys $key"
  done
fi
if [[ -n "$missing_keys" ]]; then
  echo "$step_env_file 缺少（或值为空）:$missing_keys" >&2
  echo "" >&2
  echo "ADMIN_SUPER_* 是后台超级管理员的账号密码，容器只能从这里拿到它们——" >&2
  echo "缺了的话 app/admin_auth.py 在模块导入期就 raise，容器起不来并无限重启。" >&2
  if [[ "$DEV_ONLY" == "true" ]]; then
    echo "这是**开发环境**那一份，缺了只有两个 dev 域名不可用，生产不受影响。" >&2
  else
    echo "这是**生产**那一份，缺了官网预约、小程序接口、后台会一起挂。" >&2
  fi
  echo "" >&2
  echo "补上再重跑（用户名别用 root / admin 这类能猜到的名字，密码要强且只能用 ASCII；" >&2
  echo "开发环境的超管**不要**和生产填成同一套——dev 域名同样公网可达）：" >&2
  if [[ "$step_env_file" == "$SERVER_DIR/.env" ]]; then
    echo "  编辑 $SERVER_DIR/.env，格式见 $SERVER_DIR/.env.example" >&2
  else
    echo "  ssh $DEPLOY_HOST" >&2
    echo "  vi $step_env_file" >&2
    if [[ "$DEV_ONLY" == "true" ]]; then
      echo "  格式见 server/deploy/dev/.env.example" >&2
    else
      echo "  格式见 server/deploy/.env.example" >&2
    fi
  fi
  exit 1
fi

echo "    OK"

# ------------------------------------------------------- 远端目录与 compose
step "[2/8] 远端目录与 docker compose 插件"
# data/ 下是库数据和证书，bind mount 的宿主目录先建好，
# 让它们属于 deploy 而不是被 docker 以 root 自动创建
# 所有 bind mount 的宿主目录都要先建好，包括开发环境那两份 dist——
# 目录不存在时 docker 会以 root 自动建一个空的：站点变 404 而容器状态一切正常，
# 密钥则是读不到还不报错，两种都很难查
ssh_cmd "mkdir -p '$REMOTE_DIR/site/dist' '$REMOTE_DIR/website/dist' '$REMOTE_DIR/data/postgres' \
  '$REMOTE_DIR/data/caddy/data' '$REMOTE_DIR/data/caddy/config' '$REMOTE_DIR/secrets' \
  '$REMOTE_DEV_DIR' '$REMOTE_DEV_DIR/secrets' \
  '$REMOTE_DEV_DIR/site/dist' '$REMOTE_DEV_DIR/website/dist' \
  ~/.docker/cli-plugins"
if ssh_cmd "docker compose version >/dev/null 2>&1"; then
  echo "    compose 已就绪: $(ssh_cmd 'docker compose version')"
else
  # 二进制从本地下载再上传，不在服务器上 curl：那台机器直连 GitHub 只有 ~12KB/s
  # （48MB 要下一个多小时），而本地到服务器的 scp 有 ~5MB/s
  echo "    远端没有 compose 插件，本地下载 $COMPOSE_VERSION 后上传 ..."
  tmp_compose="$(mktemp -t docker-compose.XXXXXX)"
  curl -fL --retry 3 --connect-timeout 20 \
    "https://github.com/docker/compose/releases/download/$COMPOSE_VERSION/docker-compose-linux-x86_64" \
    -o "$tmp_compose"
  scp "${SCP_OPTS[@]}" "$tmp_compose" "$DEPLOY_HOST:/tmp/docker-compose"
  rm -f "$tmp_compose"
  ssh_cmd "mv /tmp/docker-compose ~/.docker/cli-plugins/docker-compose && chmod +x ~/.docker/cli-plugins/docker-compose"
  echo "    已安装: $(ssh_cmd 'docker compose version')"
fi

# --------------------------------------------------------------- 基础镜像
# postgres / caddy 不随代码变，远端已有就跳过，不必每次重传上百 MB
step "[3/8] 基础镜像"
for img in "${BASE_IMAGES[@]}"; do
  if ssh_cmd "docker image inspect '$img' >/dev/null 2>&1"; then
    echo "    远端已有 $img，跳过"
    continue
  fi
  # 本地有就直接用，不 pull：pull 即使命中本地也要连 registry 查有没有新版本，
  # 而这条链路会卡（实测 postgres 拉完所有层后挂住十几分钟不收尾）。
  # 基础镜像是钉死的 tag，不需要追更新
  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "    本地已有 $img"
  else
    echo "    本地拉取 $img ..."
    # 超时兜底：卡住时明确失败，好过让整个部署无限期挂着
    timeout 900 docker pull "$img" || { echo "拉取 $img 失败或超时" >&2; exit 1; }
  fi
  echo "    打包上传 $img ..."
  tmp="$(mktemp -t base-img.XXXXXX.tar.gz)"
  # -1 而不是默认的 -6：上传有 ~5MB/s，省下的传输时间远抵不上多花的压缩时间
  docker save "$img" | gzip -1 -c > "$tmp"
  echo "        包大小 $(du -h "$tmp" | cut -f1)"
  scp "${SCP_OPTS[@]}" "$tmp" "$DEPLOY_HOST:/tmp/base-img.tar.gz"
  rm -f "$tmp"
  ssh_cmd "gunzip -c /tmp/base-img.tar.gz | docker load && rm -f /tmp/base-img.tar.gz"
done

# ----------------------------------------------------------- 构建 API 镜像
step "[4/8] 构建 API 镜像"
if [[ "$SKIP_BUILD" == "true" ]]; then
  echo "    跳过（--skip-build）"
  docker image inspect "$IMAGE_REF" >/dev/null 2>&1 || { echo "本地没有 $IMAGE_REF" >&2; exit 1; }
else
  BUILD_ARGS=(build -t "$IMAGE_REF")
  [[ -n "$BUILD_PROXY" ]] && BUILD_ARGS+=(--build-arg "HTTPS_PROXY=$BUILD_PROXY" --build-arg "HTTP_PROXY=$BUILD_PROXY")
  BUILD_ARGS+=("$SERVER_DIR")
  docker "${BUILD_ARGS[@]}"
fi

# ------------------------------------------------------------- 上传 API 镜像
step "[5/8] 上传 API 镜像"
LOCAL_TGZ="$(mktemp -t antony-casa-api.XXXXXX.tar.gz)"
cleanup() { rm -f "$LOCAL_TGZ"; }
trap cleanup EXIT
docker save "$IMAGE_REF" | gzip -1 -c > "$LOCAL_TGZ"
echo "    包大小 $(du -h "$LOCAL_TGZ" | cut -f1)"
scp "${SCP_OPTS[@]}" "$LOCAL_TGZ" "$DEPLOY_HOST:/tmp/antony-casa-api.tar.gz"
ssh_cmd "gunzip -c /tmp/antony-casa-api.tar.gz | docker load && rm -f /tmp/antony-casa-api.tar.gz"

# ------------------------------------------------------------- 同步配置
step "[6/8] 同步编排文件与建表脚本"
if [[ "$DEV_ONLY" == "true" ]]; then
  # 开发项目只有一个 compose 文件。**Caddyfile 仍然发到生产目录**：
  # caddy 只有一个容器、由生产项目管，它是两个环境共用的基础设施
  scp "${SCP_OPTS[@]}" "$SCRIPT_DIR/dev/docker-compose.yml" "$DEPLOY_HOST:$REMOTE_DEV_DIR/"
  scp "${SCP_OPTS[@]}" "$SCRIPT_DIR/Caddyfile" \
    "$SERVER_DIR/schema.sql" "$SERVER_DIR/schema.anjia.sql" \
    "$DEPLOY_HOST:$REMOTE_DIR/"
else
  scp "${SCP_OPTS[@]}" \
    "$SCRIPT_DIR/docker-compose.yml" \
    "$SCRIPT_DIR/Caddyfile" \
    "$SERVER_DIR/schema.sql" \
    "$SERVER_DIR/schema.anjia.sql" \
    "$DEPLOY_HOST:$REMOTE_DIR/"
fi
# 两份 schema 都只是**传上去**，不执行——建表和改表结构一律手动，
# 理由见 deploy/README.md「改表结构」。安家立业那一份还多一条顺序要求：
# 必须先建库跑完表，才能往 .env 里填 DATABASE_URL_ANJIA

# .env 只在远端不存在时生成一次：里面有随库一起生成的 Postgres 密码，
# 每次部署都重写会让密码和已初始化的库对不上，API 直接连不上。
# 开发环境那份不自动生成（第 1 步已经挡下了），它要填的超管账号必须和生产不同
if [[ "$DEV_ONLY" == "true" ]]; then
  echo "    开发环境 .env 保留不动"
elif ssh_cmd "test -f '$REMOTE_DIR/.env'"; then
  echo "    远端 .env 已存在，保留不动"
else
  echo "    远端没有 .env，用本地 server/.env 的微信/COS 配置生成一份 ..."
  [[ -f "$SERVER_DIR/.env" ]] || { echo "本地 $SERVER_DIR/.env 不存在，没法取微信和 COS 配置" >&2; exit 1; }
  # shellcheck disable=SC1091
  set -a; source "$SERVER_DIR/.env"; set +a
  # 纯十六进制，不含 URI 保留字符——DATABASE_URL 是拼字符串出来的，见 docker-compose.yml
  PG_PASSWORD="$(openssl rand -hex 24)"
  REMOTE_ENV="$(cat <<EOF
# 由 deploy.sh 首次部署时生成。改完执行 docker compose up -d 生效。
# POSTGRES_PASSWORD 与 pgdata 卷里已初始化的库绑定，改它必须同时重建库
POSTGRES_USER=antony
POSTGRES_PASSWORD=$PG_PASSWORD
POSTGRES_DB=antony_casa

WX_APPID=${WX_APPID:-}
WX_SECRET=${WX_SECRET:-}

LOG_LEVEL=INFO
WORKER_ID=0

COS_SECRET_ID=${COS_SECRET_ID:-}
COS_SECRET_KEY=${COS_SECRET_KEY:-}
COS_BUCKET=${COS_BUCKET:-}
COS_REGION=${COS_REGION:-ap-shanghai}

# 管理后台的超级管理员。这一个账号定义在配置里、不入库，是后台唯一的初始钥匙，
# 其余管理员由它登录后在「账号管理」页创建。两项缺一 API 容器直接起不来
# （而两个域名的 /api/* 都打到这个容器，等于官网和小程序也一起挂）。
#
# ⚠️ 用户名也必须换掉，不要用 root / admin 这类一猜就中的名字：用户名一旦被知道，
#    对着它连发 5 次错密码就能把超管锁 15 分钟，每 15 分钟发一轮就是永久锁死，
#    而超管是唯一能建号、重置密码、启停账号的角色。密码要强，且只能用 ASCII 字符。
# 改这两项 = 改本文件 + docker compose up -d api（超管在系统内改不了自己的密码）
ADMIN_SUPER_USERNAME=${ADMIN_SUPER_USERNAME:-}
ADMIN_SUPER_PASSWORD=${ADMIN_SUPER_PASSWORD:-}

# ---- 开发环境（api-dev，出在 $DEV_DOMAIN） ----
# 和生产在同一个 postgres 容器里，只是另一个库。这个库要手动建，见 README「开发环境」
DEV_POSTGRES_DB=antony_casa_dev
DEV_API_TAG=dev
DEV_LOG_LEVEL=DEBUG
# 与生产的 0 错开，避免将来两边数据合并或改指同一个库时发出重复的雪花 id
DEV_WORKER_ID=1
DEV_COS_BUCKET=${COS_BUCKET:-}
DEV_ADMIN_SUPER_USERNAME=${DEV_ADMIN_SUPER_USERNAME:-}
DEV_ADMIN_SUPER_PASSWORD=${DEV_ADMIN_SUPER_PASSWORD:-}
EOF
)"
  # 经 stdin 写入，避免密钥出现在远端的进程命令行里（ps 能看到）
  printf '%s\n' "$REMOTE_ENV" | ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" \
    "umask 077 && cat > '$REMOTE_DIR/.env'"
  echo "    已生成 $REMOTE_DIR/.env（权限 600）"
fi

# ------------------------------------------------------------- 同步前端产物
step "[7/8] 同步前端产物（官网 + 后台）"
if [[ "$SKIP_WEB" == "true" ]]; then
  echo "    跳过（--skip-web）"
else
  # 打成 tar 传：dist 是几百个小文件，scp -r 一个个建连接会很慢。
  #
  # ⚠️ 清空目录内容（find -mindepth 1 -delete），绝不能 rm -rf 掉 dist 目录本身：
  # bind mount 绑的是 inode，删掉再重建就是另一个 inode 了，容器里挂的还是那个
  # 已删除的旧目录，于是站点整个变 404——而且容器状态、健康检查全是正常的，很难查。
  # 清内容是为了不让上一版的 hash 文件越堆越多（访问不到，但一直占盘）。
  # 发到哪个目录由 TARGET_DIR 决定：生产发生产的，--dev-only 发开发的。
  # 两边各一份 dist，所以更新开发环境的后台不会动到生产的后台
  sync_dist() {
    local src="$1" dest="$2"
    tar -C "$(dirname "$src")" -czf - dist \
      | ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" \
        "mkdir -p '$TARGET_DIR/$dest/dist' \
         && find '$TARGET_DIR/$dest/dist' -mindepth 1 -delete \
         && tar -C '$TARGET_DIR/$dest' -xzf -"
    echo "    $dest: $(ssh_cmd "find '$TARGET_DIR/$dest/dist' -type f | wc -l") 个文件"
  }
  sync_dist "$SITE_DIST" site
  sync_dist "$ADMIN_DIST" website
fi

# ------------------------------------------------------------------- 启动
step "[8/8] 启动容器"
if [[ "$DEV_ONLY" == "true" ]]; then
  # 开发是独立的 compose 项目，`up -d` 只会动它自己那一个容器。
  # --remove-orphans 在这里是安全的：它按项目标签判断，碰不到生产那三个
  remote_dev_compose "up -d --remove-orphans"
else
  remote_compose "up -d --remove-orphans"
fi

# Caddyfile 是 bind mount 进去的，改了内容 compose 认为服务定义没变、容器不重建，
# Caddy 自己也不会重读——不显式 reload 的话配置改动永远不生效。
# 用 reload 而不是 restart：不断连接，而且配置有错时 reload 失败、旧配置继续跑
echo "    reload Caddy 配置 ..."
remote_compose "exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile"

echo
echo "--- 容器状态 ---"
echo "  生产项目 antony-casa:"
remote_compose "ps"
echo "  开发项目 antony-casa-dev:"
remote_dev_compose "ps" 2>/dev/null || echo "    （还没建）"

# 健康检查：Caddy 起来到证书签下来有几秒，重试几次再判失败。
# 必须带 --resolve 走域名而不是直接打 http://127.0.0.1：Caddy 对匹配不到站点块的
# Host 一律 308 跳 HTTPS，用 IP 请求永远拿不到 200，看着像挂了其实是好的。
# 走域名 + 443 才是真链路：TLS 握手、证书、SNI、反代到 api 一次全验了
echo
echo "--- 健康检查 ---"

# 在服务器上打真实域名。--resolve 让它走 127.0.0.1 但保留 SNI 和 Host，
# 这样 TLS 握手、证书、站点匹配、反代/静态托管一次全验了
probe() { ssh_cmd "curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
  --resolve '$1:443:127.0.0.1' 'https://$1$2'" 2>/dev/null || echo 000; }

# 生产也要验：--dev-only 理论上碰不到它，但「理论上碰不到」正是要验一下的理由——
# compose 文件是整份同步过去的，改错一行就可能连带重建 api
for i in $(seq 1 20); do
  code="$(probe "$SITE_DOMAIN" /health)"
  if [[ "$code" == "200" ]]; then echo "    api   /health -> 200"; break; fi
  [[ $i -eq 20 ]] && { echo "    /health 20 次都没到 200（最后一次 $code）" >&2; remote_compose "logs --tail=50 api"; exit 1; }
  sleep 3
done

# 开发环境的两个域名。它是另一个 compose 项目里的容器，和生产 api 是两条独立链路，
# 生产 200 完全不能说明它也活着。
#
# 两条都要轮询而不是探一次：新域名首次签证书要几十秒（Let's Encrypt 的 HTTP-01
# 多点校验有一个节点超时的话，Caddy 还会降级到 TLS-ALPN-01 重来一轮）。
# 探一次就判失败会把「还在签」误报成「部署失败」——这条脚本自己踩过。
dev_probe() { # $1 域名  $2 路径  $3 名字
  for i in $(seq 1 25); do
    code="$(probe "$1" "$2")"
    if [[ "$code" == "200" ]]; then echo "    $3 https://$1$2 -> 200"; return 0; fi
    [[ $i -eq 25 ]] && {
      echo "    $3 https://$1$2 25 次都没到 200（最后一次 $code）" >&2
      echo "    这不影响生产（官网/后台/正式版小程序走的是另一个容器和另一个项目）。" >&2
      echo "    常见原因：A 记录没生效（证书签不下来）、$REMOTE_DEV_DIR/.env 没配全、" >&2
      echo "    或者容器连不上 postgres。" >&2
      remote_dev_compose "logs --tail=50 api"
      return 1
    }
    sleep 4
  done
}
dev_probe "$DEV_DOMAIN" /health "api-dev" || exit 1
# 开发的两个站点各有自己的一份 dist，挂空了的话接口照样 200、页面却 404
dev_probe "$DEV_DOMAIN" / "dev-site" || exit 1
dev_probe "$DEV_ADMIN_DOMAIN" / "dev-admin" || exit 1

# 生产的静态站点。只测 /health 的话，dist 目录挂空了也照样「健康」——
# 前端整个 404 而容器状态一切正常，这种情况必须由脚本自己发现。
# --dev-only 时也验：它不该碰生产，而「不该碰」正是要验一下的理由
if [[ "$SKIP_WEB" != "true" || "$DEV_ONLY" == "true" ]]; then
  for pair in "$SITE_DOMAIN:官网" "$ADMIN_DOMAIN:后台"; do
    domain="${pair%%:*}"; name="${pair##*:}"
    code="$(probe "$domain" /)"
    if [[ "$code" == "200" ]]; then
      echo "    $name  https://$domain/ -> 200"
    else
      echo "    $name https://$domain/ 返回 $code（期望 200）" >&2
      echo "    dist 挂载可能是空的，检查 $REMOTE_DIR 下的 site/dist 与 website/dist" >&2
      exit 1
    fi
  done
fi

echo
echo "部署完成。核验："
echo "  curl -sS https://$SITE_DOMAIN/health"
echo "  curl -sS https://$DEV_DOMAIN/health          # 开发环境（小程序预览走这条）"
echo "  ssh $DEPLOY_HOST 'cd $REMOTE_DIR && docker compose logs -f --tail=50'          # 生产"
echo "  ssh $DEPLOY_HOST 'cd $REMOTE_DEV_DIR && docker compose logs -f --tail=50'      # 开发"

if [[ "$SHOW_LOGS" == "true" ]]; then
  echo
  remote_compose "logs --tail=60"
fi

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
# 与 Caddyfile 里的两个站点块保持一致，健康检查要靠它们匹配到站点并通过证书校验
SITE_DOMAIN="${SITE_DOMAIN:-antonycasa.weelume.com}"
ADMIN_DOMAIN="${ADMIN_DOMAIN:-admin.antonycasa.weelume.com}"
IMAGE="${IMAGE:-antony-casa-api}"
TAG="${TAG:-latest}"
BUILD_PROXY="${BUILD_PROXY:-}"
# 版本写死而不是查 GitHub latest：部署要可重复，接口限流时也不该悄悄换一个版本
COMPOSE_VERSION="${COMPOSE_VERSION:-v5.4.0}"
BASE_IMAGES=(postgres:18-alpine caddy:2-alpine)

SKIP_BUILD="false"
SKIP_WEB="false"
SHOW_LOGS="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -H|--host)     DEPLOY_HOST="$2"; shift 2 ;;
    -p|--port)     DEPLOY_PORT="$2"; shift 2 ;;
    -i|--identity) DEPLOY_KEY="$2";  shift 2 ;;
    --proxy)       BUILD_PROXY="$2"; shift 2 ;;
    --skip-build)  SKIP_BUILD="true"; shift ;;
    --skip-web)    SKIP_WEB="true";   shift ;;
    --logs)        SHOW_LOGS="true";  shift ;;
    -h|--help)     sed -n '2,30p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

IMAGE_REF="$IMAGE:$TAG"

SSH_OPTS=(-p "$DEPLOY_PORT" -o BatchMode=yes)
SCP_OPTS=(-P "$DEPLOY_PORT" -o BatchMode=yes)
if [[ -n "$DEPLOY_KEY" ]]; then SSH_OPTS+=(-i "$DEPLOY_KEY"); SCP_OPTS+=(-i "$DEPLOY_KEY"); fi
ssh_cmd() { ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" "$@"; }

remote_compose() { ssh_cmd "cd '$REMOTE_DIR' && docker compose $*"; }

step() { echo; echo "==> $*"; }

echo "镜像:   $IMAGE_REF"
echo "目标:   $DEPLOY_HOST:$REMOTE_DIR"
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
echo "    OK"

# ------------------------------------------------------- 远端目录与 compose
step "[2/8] 远端目录与 docker compose 插件"
# data/ 下是库数据和证书，bind mount 的宿主目录先建好，
# 让它们属于 deploy 而不是被 docker 以 root 自动创建
ssh_cmd "mkdir -p '$REMOTE_DIR/site' '$REMOTE_DIR/website' '$REMOTE_DIR/data/postgres' \
  '$REMOTE_DIR/data/caddy/data' '$REMOTE_DIR/data/caddy/config' ~/.docker/cli-plugins"
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
scp "${SCP_OPTS[@]}" \
  "$SCRIPT_DIR/docker-compose.yml" \
  "$SCRIPT_DIR/Caddyfile" \
  "$SERVER_DIR/schema.sql" \
  "$DEPLOY_HOST:$REMOTE_DIR/"

# .env 只在远端不存在时生成一次：里面有随库一起生成的 Postgres 密码，
# 每次部署都重写会让密码和已初始化的库对不上，API 直接连不上
if ssh_cmd "test -f '$REMOTE_DIR/.env'"; then
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
  sync_dist() {
    local src="$1" dest="$2"
    tar -C "$(dirname "$src")" -czf - dist \
      | ssh "${SSH_OPTS[@]}" "$DEPLOY_HOST" \
        "mkdir -p '$REMOTE_DIR/$dest/dist' \
         && find '$REMOTE_DIR/$dest/dist' -mindepth 1 -delete \
         && tar -C '$REMOTE_DIR/$dest' -xzf -"
    echo "    $dest: $(ssh_cmd "find '$REMOTE_DIR/$dest/dist' -type f | wc -l") 个文件"
  }
  sync_dist "$SITE_DIST" site
  sync_dist "$ADMIN_DIST" website
fi

# ------------------------------------------------------------------- 启动
step "[8/8] 启动容器"
remote_compose "up -d --remove-orphans"

# Caddyfile 是 bind mount 进去的，改了内容 compose 认为服务定义没变、容器不重建，
# Caddy 自己也不会重读——不显式 reload 的话配置改动永远不生效。
# 用 reload 而不是 restart：不断连接，而且配置有错时 reload 失败、旧配置继续跑
echo "    reload Caddy 配置 ..."
remote_compose "exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile"

echo
echo "--- 容器状态 ---"
remote_compose "ps"

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

for i in $(seq 1 20); do
  code="$(probe "$SITE_DOMAIN" /health)"
  if [[ "$code" == "200" ]]; then echo "    api   /health -> 200"; break; fi
  [[ $i -eq 20 ]] && { echo "    /health 20 次都没到 200（最后一次 $code）" >&2; remote_compose "logs --tail=50"; exit 1; }
  sleep 3
done

# 静态站点单独验：只测 /health 的话，dist 目录挂空了也照样「健康」——
# 前端整个 404 而容器状态一切正常，这种情况必须由脚本自己发现
if [[ "$SKIP_WEB" != "true" ]]; then
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
echo "  curl -sS https://antonycasa.weelume.com/health"
echo "  ssh $DEPLOY_HOST 'cd $REMOTE_DIR && docker compose logs -f --tail=50'"

if [[ "$SHOW_LOGS" == "true" ]]; then
  echo
  remote_compose "logs --tail=60"
fi

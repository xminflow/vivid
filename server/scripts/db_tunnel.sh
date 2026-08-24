#!/usr/bin/env bash
# 把服务器上那个 postgres 容器映到本机 127.0.0.1:15432，给本地开发连安家立业的
# 开发库 anjia_dev 用。
#
# 为什么要隧道：容器只在 docker 网络里可达（`docker ps` 看到的是 `5432/tcp`，
# 没有宿主端口映射）。而 ssh -L 的目标地址是**在服务器上**解析和连接的，
# 宿主机路由得到 docker 网桥，所以转发到容器 IP 就通了——不用改 compose、
# 不用加端口映射、不用重启 postgres（重启它生产 API 会断）。
#
# 容器 IP 会在容器被重建（docker compose up 重新创建服务）时变，所以每次现查，
# 不写死。查不到就直接失败，不猜一个上次的值。
#
# 用法：
#   scripts/db_tunnel.sh          前台跑，Ctrl-C 关掉
#   scripts/db_tunnel.sh -f       后台跑（ssh -f），关的时候自己 kill
#
# 隧道没开时的症状是「本地什么都读不到」而不是报错——见 app/db.py 里那段注释，
# 同一类问题。先怀疑这个。
set -euo pipefail

DEPLOY_HOST="${DEPLOY_HOST:-deploy@antonycasa.weelume.com}"
CONTAINER="${CONTAINER:-antony-casa-postgres}"
LOCAL_PORT="${LOCAL_PORT:-15432}"

ip=$(ssh -o BatchMode=yes "$DEPLOY_HOST" \
  "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' $CONTAINER")
[[ -n "$ip" ]] || { echo "拿不到 $CONTAINER 的容器 IP，先确认它在跑" >&2; exit 1; }

echo "127.0.0.1:$LOCAL_PORT -> $ip:5432 ($DEPLOY_HOST)"
# ExitOnForwardFailure：本地端口被占时立刻失败，不要留一个连不通的 ssh 会话在那
exec ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  "$@" -N -L "$LOCAL_PORT:$ip:5432" "$DEPLOY_HOST"

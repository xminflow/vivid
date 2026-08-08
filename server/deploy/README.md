# 部署：antonycasa.weelume.com

后端（FastAPI）、自建 Postgres、Caddy 三个容器跑在同一台腾讯云机器上，
用 docker compose 编排。两份前端静态产物由 Caddy 直接托管，不单独打镜像。

```
                      ┌─ antonycasa.weelume.com ──────┬─ /api/* ─┐
公网 :443 ──► caddy ──┤   （官网 antony-web）          └─ 其余 ──► /srv/site
                      │                                           │
                      └─ admin.antonycasa.weelume.com ─┬─ /api/* ─┤► api:3000 ──► postgres:5432
                          （后台 website）              └─ 其余 ──► /srv/admin
```

两个域名都要解析到这台机器（`43.137.6.100`），Caddy 分别自动签证书。

## 这台机器的约束

部署方式是被这几条约束逼出来的，改脚本前先看一眼：

- **连不上 Docker Hub**：`registry-1.docker.io` 直连 i/o timeout，GitHub 和
  Let's Encrypt 通。所以三个镜像全部在本地准备好，`docker save` 后 scp 过去 `docker load`，
  compose 里一律 `pull_policy: never`
- **deploy 没有免密 sudo**，但在 docker 组里。建目录、装 compose 插件、跑容器都不需要 root，
  compose 插件装在用户级的 `~/.docker/cli-plugins/`
- **Windows 侧没有 docker**，构建要在 WSL 里做；而前端的 `node_modules` 是 Windows 侧
  pnpm 装的，原生二进制不能在 WSL 里跑，所以前端构建留在 Windows 侧

## 首次部署

两份前端产物先在 **Windows** 侧构建（node_modules 是 Windows 侧装的，
原生二进制在 WSL 里跑不了）：

```powershell
cd D:\code\vivid\antony-web; pnpm build   # 官网
cd D:\code\vivid\website;    pnpm build   # 后台管理
```

再在 **WSL** 里跑部署脚本：

```bash
wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh
```

脚本是幂等的，做这些事：

1. 建远端目录、装 docker compose 插件（已装则跳过）
2. `postgres:18-alpine` / `caddy:2-alpine`：远端没有才本地拉取并上传
3. 构建 `antony-casa-api:latest`，save → scp → load
4. 同步 `docker-compose.yml`、`Caddyfile`、`schema.sql`
5. **远端 `.env` 不存在时**才生成：Postgres 密码用 `openssl rand -hex 24` 现随机一个，
   微信和 COS 配置从本地 `server/.env` 抄过去。已存在就原样保留——密码是和
   `pgdata` 卷里已初始化的库绑定的，重写会让 API 连不上库
6. 同步两份前端产物：`antony-web/dist` → `site/`，`website/dist` → `website/`
7. `docker compose up -d`，reload Caddy 配置，再轮询 `/health`

第 7 步的 reload 不能省：Caddyfile 是 bind mount 进容器的，改了内容 compose
认为服务定义没变、容器不重建，Caddy 自己也不会重读，不显式 reload 配置就永远不生效。

## 日常部署

```bash
# 改了后端
wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh --skip-web

# 只改了前端（先在 Windows 侧 pnpm build）
wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh --skip-build
```

其他参数：`--proxy <url>` 让 docker build 走代理，`--logs` 部署完跟一段日志，
`-H/-p/-i` 改目标主机、端口、私钥。

## 看日志

```bash
ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa
docker compose logs -f --tail=100          # 三个服务一起
docker compose logs -f api                 # 只看后端
```

日志走 docker 的 json-file driver，单文件 10M、留 3 份——50G 的系统盘，不封顶迟早写满。

临时开 DEBUG 级别：改远端 `.env` 的 `LOG_LEVEL=DEBUG`，然后 `docker compose up -d api`。

## 改表结构

`schema.sql` 挂在 postgres 的 `/docker-entrypoint-initdb.d/`，**只在首次建库时自动执行**。
之后改表要手动跑：

```bash
# 本地改完 schema.sql，重新部署一次把文件同步上去（up -d 不会重跑 initdb）
wsl -d Ubuntu -- bash .../deploy.sh --skip-build --skip-web

ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa
source .env
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < schema.sql
```

`schema.sql` 全是 `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`，可以重复执行。
破坏性变更（删列、改语义、收紧约束）不要写进这个文件，按 CLAUDE.md 的数据升级规范
单独出迁移脚本。

## 连库

```bash
ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa && source .env
docker compose exec postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

Postgres **没有映射宿主端口**，只在 compose 内网可见——公网暴露 5432 等于把库直接摆出去。
要从外面连就走 SSH 隧道：

```bash
ssh -L 15432:localhost:5432 deploy@antonycasa.weelume.com \
    'docker compose -f /home/deploy/workspace/antony-casa/docker-compose.yml exec ...'
# 或更简单：在服务器上 docker compose exec postgres pg_dump ... 再 scp 回来
```

## 持久化

有状态的东西全部 bind mount 在 `/home/deploy/workspace/antony-casa/data/` 下，
不用 named volume——目录看得见、tar 一下就是备份，也不会被 `docker compose down -v` 顺手删掉：

| 宿主路径 | 容器内 | 内容 |
|---|---|---|
| `data/postgres` | `/var/lib/postgresql` | 库数据 |
| `data/caddy/data` | `/data` | TLS 证书、ACME 账号 |
| `data/caddy/config` | `/config` | Caddy 自动持久化的配置 |
| `site/dist` | `/srv/site` | 官网（antony-web）产物 |
| `website/dist` | `/srv/admin` | 后台管理（website）产物 |

⚠️ postgres 的挂载点是 `/var/lib/postgresql`，**不是** `/var/lib/postgresql/data`。
`postgres:18` 起 `PGDATA` 改成了 `/var/lib/postgresql/18/docker`，镜像声明的 VOLUME 也是
`/var/lib/postgresql`；挂老路径的话真实数据会落进 docker 自动建的匿名卷，
`docker inspect` 看着有卷、实际上容器一重建就没了。改这行前先确认镜像的 `PGDATA`：

```bash
docker image inspect postgres:18-alpine --format '{{json .Config.Env}}' | tr ',' '\n' | grep PGDATA
```

`data/postgres` 会被 postgres 的 entrypoint chown 成容器内的 postgres 用户，
宿主上 deploy 直接读不了，属正常；要看数据走 `docker compose exec`。

## 备份

**目前没有配自动备份。** 手动备份（逻辑备份，跨版本可用，推荐）：

```bash
ssh deploy@antonycasa.weelume.com \
  'cd /home/deploy/workspace/antony-casa && set -a && . ./.env && set +a &&
   docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' \
  | gzip > antony_casa_$(date +%F).sql.gz
```

恢复：

```bash
gunzip -c antony_casa_2026-08-06.sql.gz | ssh deploy@antonycasa.weelume.com \
  'cd /home/deploy/workspace/antony-casa && set -a && . ./.env && set +a &&
   docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

整个 `data/` 目录也可以直接打包，但**必须先停容器**——热拷贝 Postgres 的数据目录
拿到的是撕裂状态，恢复出来大概率起不来：

```bash
ssh deploy@antonycasa.weelume.com 'cd /home/deploy/workspace/antony-casa &&
  docker compose stop postgres && tar -czf ~/pgdata-$(date +%F).tar.gz data/postgres &&
  docker compose start postgres'
```

## 证书

Caddy 给两个域名各自自动申请和续期，证书都在 `data/caddy/data/` 下。

- **别删这个目录**：Let's Encrypt 对同一域名有每周 5 次的签发限制，反复重来会被锁
- HTTP-01 校验走 80 端口，安全组必须放行 80；只放 443 签不下来
- 加新域名前先确认 DNS 已生效，否则 Caddy 会一直重试签发并刷日志
- 首次启动到证书就绪有十几秒，这期间 https 会失败，属正常

看证书状态：`docker compose logs caddy | grep -i certificate`

## 回滚

镜像用的是 `latest` 标签，没有留历史版本，回滚要重新构建旧代码再部署：

```bash
git checkout <旧 commit>
wsl -d Ubuntu -- bash .../deploy.sh --skip-web
```

配置文件（compose / Caddyfile）改坏了可以只回滚文件：改回本地版本，
`deploy.sh --skip-build --skip-web` 同步上去即可。

## 已知遗留

- ~~`/api/admin/*` 没有鉴权~~ 已完成：需要登录才能访问（`app/admin.py` 的 router
  挂了 `dependencies=[Depends(current_admin)]`），见 `server/README.md`「上线前
  要做的」。两个域名都能打到它这件事本身没变（Caddyfile 的 `shared` 片段里
  `/api/*` 是共用的），但现在打过去拿不到数据了
- 后台管理打开就是数据，没有登录页；换到独立子域名只是不容易被撞见，不等于有防护
- **COS 桶实测是「公有读私有写」**：对象拿到 URL 就能直接下载，签名在读这一侧
  没有拦截作用。桶权限收紧成私有读之后不用改代码，但要给 `static/` 前缀单独设
  公有读 ACL，否则小程序首页的品牌实拍图会全挂
- 库没有自动备份，也没有异地副本
- `.env` 里用的还是开发环境的 `WX_SECRET` 和 COS 开发桶密钥

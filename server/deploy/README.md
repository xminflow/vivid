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

## 发布「管理端登录鉴权」这一支

这一支同时改了配置、表结构和前后端契约，**顺序错了会有肉眼可见的故障**，
按下面四步走，不要跳、不要拆成两次发。

### ① 远端 `.env` 补上超管两项

```bash
ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa
vi .env
```

追加（用户名**不要**用 `root` / `admin` 这类能猜到的名字，见下面「超管被锁住了」；
密码要强，且只能用 ASCII 字符）：

```
ADMIN_SUPER_USERNAME=<不容易猜的用户名>
ADMIN_SUPER_PASSWORD=<强密码>
```

远端 `.env` 是首次部署时生成的，之后 `deploy.sh` 只会「已存在就保留不动」，
不会自己长出新 key，所以这一步必须手动做一次。

漏了会怎样：容器里没有 `.env` 文件（`Dockerfile` 只 `COPY app ./app`），这两项只能
经 `docker-compose.yml` 的 `environment` 进去；缺了的话 `app/admin_auth.py` 在模块
导入期就 `raise`，API 容器起不来并被 `restart: unless-stopped` 无限重启。而
`Caddyfile` 的 `(shared)` 片段把**两个域名**的 `/api/*` 都反代到同一个 `api:3000`，
所以那不只是后台挂，是官网预约、服务申请、小程序接口一起挂。

`deploy.sh` 第 1 步已经会替你查这两个 key，缺了就在动手之前直接失败——
但那是兜底，不是让你省掉这一步。

### ② 建三张表

`schema.sql` 只在**首次建库**时自动执行，`deploy.sh` 把它 scp 上去但**不执行**。
`admin_users` / `admin_sessions` / `admin_login_attempts` 这三张表要手动建。

> ⚠️ **这一步不要调 `deploy.sh`**，哪怕加了 `--skip-build --skip-web`。
> 这两个开关**不阻止换容器**：`--skip-build` 只跳过 `[4/8]` 的 `docker build`，
> `--skip-web` 只跳过 `[7/8]` 的前端同步；`[5/8]` 上传镜像 + `docker load` 和
> `[8/8]` 的 `up -d --remove-orphans` 照常执行。本地的 `antony-casa-api:latest`
> 在这个流程里几乎必然已经是新代码，于是**表还没建、前端还是旧的，新后端就上线了**
> ——正好同时命中下面两种故障。而且 `--skip-web` 会跳过静态站点校验、`/health`
> 又不碰 admin 表，脚本会安安静静地报成功，没人会察觉容器已经被换掉。

直接 scp 文件 + ssh 跑 psql，全程不碰 api 容器：

```bash
# 1. 只把 schema.sql 传上去（在 WSL 里跑；这条命令不会启动或替换任何容器）
scp /mnt/d/code/vivid/server/schema.sql \
    deploy@antonycasa.weelume.com:/home/deploy/workspace/antony-casa/schema.sql

# 2. 在服务器上执行它。ON_ERROR_STOP=1 让任何一条语句出错就整体失败，
#    不要出现「前半截建了、后半截没建」还返回成功的情况
ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa && source .env
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" < schema.sql

# 3. 核对三张表都在（应当列出 admin_login_attempts / admin_sessions / admin_users）
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\dt admin_*'
```

`schema.sql` 全是 `IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS`，对已有的库重复执行
是安全的。`docker compose exec` 要求 postgres 容器正在跑——它是常驻的；万一没跑，
用 `docker compose up -d postgres` 单独把它拉起来，这样也不会动到 api。

漏了会怎样：新代码起来后每一条 `/api/admin/*` 和登录接口都 500
（`relation "admin_sessions" does not exist`）。

### ③ 前端和后端一起发

```bash
# Windows 侧先构建两份产物
cd D:\code\vivid\antony-web; pnpm build
cd D:\code\vivid\website;    pnpm build

# WSL 里全量部署，⚠️ 这一支禁止加 --skip-web / --skip-build
wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh
```

**这一支不能用 `--skip-web`**：旧后台产物没有登录页，而新后端全线要鉴权，
结果是打开后台满屏 401 且没地方登录。反过来只发前端不换后端也不行：
新后台会去调 `POST /api/admin/auth/login`，旧后端没有这条路由，直接 404。

全量部署里前端产物是先于 api 容器换的，中间仍有几十秒的新前端配旧后端——
这段窗口没法消除（除非停机），但它只有几十秒，且只影响后台域名，可以接受。

### ④ 用超管登一次

```
https://admin.antonycasa.weelume.com/
```

用 ① 里配的账号密码登进去，确认能看到预约列表、且「账号管理」页打得开。
再确认官网和小程序的提交没受影响：

```bash
curl -sS https://antonycasa.weelume.com/health
```

登不进去先看日志：`docker compose logs --tail=50 api`。

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
# 本地改完 schema.sql，把文件传上去。只 scp，不调 deploy.sh——
# --skip-build / --skip-web 都**不阻止换容器**（见上面「发布」一节那个提示框），
# 而建表必须发生在新代码上线之前
scp /mnt/d/code/vivid/server/schema.sql \
    deploy@antonycasa.weelume.com:/home/deploy/workspace/antony-casa/schema.sql

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

## 超管被锁住了 / token 泄漏了

两件事后台里都做不了，只能连库手动处理（连法见上面「连库」）。

**超管被锁在门外。** 登录失败计数锁的是**用户名**：同一个用户名连错 5 次锁 15 分钟。
锁定期内的请求不再累加计数、也不延长锁，但只要每 15 分钟再发 5 个错密码，
就能把超管一直锁着。超管是唯一能建号、重置密码、启停账号的角色，锁住它等于
冻结全部账号运维。真被这么打了，先解锁、再把 `.env` 里的用户名换成不容易猜的值：

```sql
-- 解锁（<超管用户名> 就是 .env 里 ADMIN_SUPER_USERNAME 的值）
DELETE FROM admin_login_attempts WHERE username = '<超管用户名>';

-- 看一眼现在谁被锁着、锁到什么时候
SELECT username, fail_count, locked_until FROM admin_login_attempts
 WHERE locked_until > now();
```

换用户名之后要 `docker compose up -d api` 让它生效。
换名字本身就是最有效的止损：攻击者不知道用户名就发不起这种锁定。

**超管 token 泄漏了。** 会话有效期 12 小时、滑动续期，从签发起满 7 天绝对失效。
但超管不入库，没有任何后台操作会清掉它的会话（普通管理员靠改密码 / 停用 / 删号
就能吊销），所以只能删表行：

```sql
-- 吊销超管的全部登录态，下一次请求立刻 401
DELETE FROM admin_sessions WHERE is_super;

-- 看一眼现有的超管会话
SELECT token, created_at, expires_at FROM admin_sessions WHERE is_super ORDER BY created_at;
```

泄漏的如果不只是 token 还有密码，删完会话还要改 `.env` 的 `ADMIN_SUPER_PASSWORD`
并 `docker compose up -d api`。

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

配置文件（compose / Caddyfile）改坏了可以只回滚文件：改回本地版本后直接
scp + ssh 生效，**不要调 `deploy.sh`**，哪怕加 `--skip-build --skip-web`。
原因同上面「建三张表」一节的提示框：这两个开关不阻止 `[5/8]` 换镜像和
`[8/8]` 换容器，本地 `antony-casa-api:latest` 在这个时间点几乎必然已经是
新代码——回滚本来是最不该出意外的时刻，结果连带把 api 也换了。

```bash
# Caddyfile 改坏了：改回本地版本后
scp /mnt/d/code/vivid/server/deploy/Caddyfile \
    deploy@antonycasa.weelume.com:/home/deploy/workspace/antony-casa/Caddyfile
ssh deploy@antonycasa.weelume.com \
  'cd /home/deploy/workspace/antony-casa && \
   docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile'

# docker-compose.yml 改坏了：改回本地版本后
scp /mnt/d/code/vivid/server/deploy/docker-compose.yml \
    deploy@antonycasa.weelume.com:/home/deploy/workspace/antony-casa/docker-compose.yml
ssh deploy@antonycasa.weelume.com \
  'cd /home/deploy/workspace/antony-casa && docker compose up -d --remove-orphans'
```

`docker-compose.yml` 里 api 是 `pull_policy: never`，`up -d` 只会用远端已经
`docker load` 过的镜像重建容器、不会去拉取或用到本地 Windows 侧的镜像，
所以这条命令只应用配置改动，镜像不变。

## 已知遗留

- ~~`/api/admin/*` 没有鉴权~~ 已完成：需要登录才能访问（`app/admin.py` 的 router
  挂了 `dependencies=[Depends(current_admin)]`），见 `server/README.md`「上线前
  要做的」。两个域名都能打到它这件事本身没变（Caddyfile 的 `shared` 片段里
  `/api/*` 是共用的），但现在打过去拿不到数据了
- **COS 桶实测是「公有读私有写」**：对象拿到 URL 就能直接下载，签名在读这一侧
  没有拦截作用。桶权限收紧成私有读之后不用改代码，但要给 `static/` 前缀单独设
  公有读 ACL，否则小程序首页的品牌实拍图会全挂
- 库没有自动备份，也没有异地副本
- `.env` 里用的还是开发环境的 `WX_SECRET` 和 COS 开发桶密钥

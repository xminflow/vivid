# 部署：antonycasa.weelume.com

后端（FastAPI）、自建 Postgres、Caddy 三个容器跑在同一台腾讯云机器上，
用 docker compose 编排。两份前端静态产物由 Caddy 直接托管，不单独打镜像。
另有一个 `api-dev` 容器跑开发环境的接口，见下面「开发环境」。

```
                      ┌─ antonycasa.weelume.com ──────┬─ /api/* ─┐
公网 :443 ──► caddy ──┤   （官网 antony-web）          └─ 其余 ──► /srv/site
                      │                                           │
                      ├─ admin.antonycasa.weelume.com ─┬─ /api/* ─┤► api:3000 ──► postgres:5432
                      │   （后台 website）              └─ 其余 ──► /srv/admin
                      │
                      └─ dev.antonycasa.weelume.com ──── /api/* ──► api-dev:3000 ──► 腾讯云开发库
                          （开发环境，只出接口）
```

三个域名都要解析到这台机器（`43.137.6.100`），Caddy 分别自动签证书。

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

> ⚠️ 手动更新 Caddyfile 一律**就地写**（`scp` 覆盖、或 `cat 新的 > Caddyfile`），
> **不要 `mv 新的 Caddyfile`**。这里 bind mount 绑的是**文件的 inode**（与 dist
> 目录那条同一个道理）：`mv` 换掉 inode 之后，容器里那份就永远停在旧内容，
> 而 `caddy reload --config /etc/caddy/Caddyfile` 读的正是它——于是 reload
> 报成功、配置纹丝不动，宿主机上 `cat Caddyfile` 看到的又是新的，很难往这上面想。
> 已经 `mv` 过了只能重建容器（`docker compose up -d --force-recreate --no-deps caddy`，
> 80/443 断几秒；证书在 `data/caddy/data` 的 bind mount 上，不会重签）。
> 判断方法：
>
> ```bash
> diff <(cat Caddyfile) <(docker compose exec -T caddy cat /etc/caddy/Caddyfile </dev/null)
> ```

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

## 开发环境（小程序预览 / 真机调试 / 体验版）

`dev.antonycasa.weelume.com` 是**另一套后端**：容器叫 `api-dev`，跑
`antony-casa-api:dev` 镜像，连**同一个 postgres 容器里的另一个库**
`antony_casa_dev`，COS 用开发桶 `antony-casa-dev-1327365963`。

为什么需要它：手机连不到开发机的 `127.0.0.1`，而微信只收备案域名下的 https，
内网穿透绕不过这一条。所以预览、真机调试、体验版必须有一个公网可达的开发域名，
小程序侧由 `antony-casa/utils/config.js` 的 `DEV_BASE` 指过来。

三个隔离维度，缺一不可：

| | 生产 `api` | 开发 `api-dev` |
|---|---|---|
| 镜像 tag | `latest` | `dev` |
| 库 | `antony_casa` | `antony_casa_dev`（同一个 pg 实例） |
| COS 桶 | `antonycasa-pro-…` | `antony-casa-dev-…` |
| 超管账号 | `ADMIN_SUPER_*` | `DEV_ADMIN_SUPER_*`（另一套） |
| `WORKER_ID` | 0 | 1 |
| 日志级别 | INFO | DEBUG |

tag 分开是关键的一条：常规 `deploy.sh` 会把本地构建的镜像推成 `latest` 并替换
生产 `api`，而本地分支上通常有还没发布的代码。分开之后 `--dev-only` 只动 `api-dev`。

**共用一个 postgres 实例**，只靠库隔离。这台机器就这点量，为开发环境再起一个 pg
容器不值当；代价是开发环境跑飞了（大查询、锁表）会拖到生产，真出现就
`docker compose stop api-dev`——停了不影响任何线上功能。

### 当前状态

`dev.antonycasa.weelume.com` **已经通了**（2026-08-12）：证书已签，公网
`/health`、`/api/home`、`/api/shop/*` 全 200，返回的是开发库的空数据；
小程序的 `DEV_BASE` 已指过去。

还剩两件事：

1. **`dev.admin.antonycasa.weelume.com` 的 A 记录还没建**（查 223.5.5.5 / 8.8.8.8
   都没有）。所以下发到服务器的 `Caddyfile` 是**裁掉这个站点块**的版本，
   与仓库里这份不一致——域名写进去但解析不过来，Caddy 会反复申请证书并失败，
   刷满共享的容器日志。A 记录建好后跑一次就同步了：

   ```bash
   wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh --dev-only
   ```

2. **公众平台加 request 合法域名**：「开发管理 - 开发设置 - 服务器域名」加上
   `https://dev.antonycasa.weelume.com` 和开发桶
   `https://antony-casa-dev-1327365963.cos.ap-shanghai.myqcloud.com`
   （头像和申请图是客户端直传 COS 的，域名没加真机上传会被微信拦下，
   errMsg 是 `url not in domain list`）。**预览版在真机上强制校验域名**，
   开发者工具里「不校验合法域名」那个勾在手机上不作数

### 怎么做到不影响生产的

这套东西是加在**跑着生产的那台机器**上的，所以每一步都按「不碰已有服务」设计：

- **另一个库**：`antony_casa_dev`，同一个 postgres 实例。`CREATE DATABASE` 是纯新增
- **另一个容器 + 另一个镜像 tag**：`api-dev` / `antony-casa-api:dev`，
  生产的 `api` 钉在 `latest` 上。`--dev-only` 只 `up -d api-dev`
- **caddy 只 reload 不 restart**：先在容器里 `caddy validate`，再 `caddy reload`。
  就算 reload 失败，Caddy 也会继续跑旧配置，连接不断
- **caddy 的服务定义一个字都没改**：特意**没有**给它加 `depends_on: api-dev`
  ——depends_on 属于服务定义，改了下一次全量 `up -d` 就会重建 caddy，
  也就是 80/443 断几秒。caddy 是按请求解析上游的，不需要这个依赖
- 部署脚本会在动手前后各记一次 `api` / `postgres` / `caddy` 的 `StartedAt` 和
  `RestartCount` 做对照。上一次部署的结果是三者逐字节相同、`restarts=0`

### 数据从哪来

这个开发库和本地开发**不是同一个**：本地 `server/.env` 指的是腾讯云那个开发库，
服务器上这个在容器内网、本地连不到（postgres 没映射宿主端口）。所以在电脑上造的
数据，扫码预览时手机上看不到。

要给预览环境造数据（上架商品、配首页图），打开
**`https://dev.admin.antonycasa.weelume.com/`** —— 它出的是**同一份**后台管理产物
（和 admin 域名是同一个 bind mount）。后台前端一律用相对路径请求 `/api/admin/*`，
所以同一份 dist 在哪个域名下打开，管的就是那个域名背后的库。
登录用 `.env` 里的 `DEV_ADMIN_SUPER_*`，和生产后台是两套账号。

### 库是怎么建的

`postgres` 镜像的 `POSTGRES_DB` 只建一个库，`schema.sql` 也只在首次建库时自动执行，
所以开发库是手动建的（已经建好，这里留作换机器时的记录）：

```bash
ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa && set -a && . ./.env && set +a

# 建库（CREATE DATABASE 没有 IF NOT EXISTS，先查再建）
docker compose exec -T postgres psql -tAqX -U "$POSTGRES_USER" -d postgres \
  -c "SELECT 1 FROM pg_database WHERE datname='$DEV_POSTGRES_DB'" </dev/null
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "CREATE DATABASE \"$DEV_POSTGRES_DB\" OWNER \"$POSTGRES_USER\"" </dev/null

# 建表。schema.sql 是全量的（含 shop 三张表），新库只跑它一份就够，不用再跑 migrations/
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" \
  -d "$DEV_POSTGRES_DB" < schema.sql
```

> ⚠️ 每条 `docker compose exec -T` 都要显式喂 stdin（文件或 `</dev/null`）。
> `-T` 会把当前 stdin 透传进容器，在 `ssh host 'bash -s' <<EOF` 这类场景里漏一条，
> 它就会把剩下的脚本当成 psql 的输入吃掉——表现是「跑到一半悄悄结束、退出码还是 0」。

之后改表结构和生产一样是手动的，见上面「改表结构」，只是 `-d` 换成开发库。

### 安家立业的库

一库一个小程序（`docs/adr/0001`），所以安家立业还要**再建一个库**，基线是
`schema.anjia.sql`。生产是 `anjia`、开发是 `anjia_dev`，都在同一个 postgres 容器里。

**顺序不能反：先建库、跑完表，再往 `.env` 里填 `DATABASE_URL_ANJIA`。** 反了的话
容器在启动期开池就失败，而 `api` 是官网、小程序、后台共用的一个容器——那等于把
安东尼之家一起带下线。没填这一项时服务照常起，只是 `/api/anjia/*` 与
`/api/admin/anjia/*` 不注册（启动日志里有一行 warning），后台的「安家立业」两页
会 404。

```bash
ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa && set -a && . ./.env && set +a

# 1. 建库（CREATE DATABASE 没有 IF NOT EXISTS，先查再建）
docker compose exec -T postgres psql -tAqX -U "$POSTGRES_USER" -d postgres \
  -c "SELECT 1 FROM pg_database WHERE datname='anjia'" </dev/null
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres \
  -c "CREATE DATABASE \"anjia\" OWNER \"$POSTGRES_USER\"" </dev/null

# 2. 建表。schema.anjia.sql 可重复执行
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" \
  -d anjia < schema.anjia.sql

# 3. 这一步做完，才把 DATABASE_URL_ANJIA / WX_APPID_ANJIA / WX_SECRET_ANJIA
#    填进 .env，然后 docker compose up -d api
```

`schema.anjia.sql` 和 `schema.sql` 一样由 `deploy.sh` scp 上去但**不执行**，
改表结构同样是手动的（见上面「改表结构」，`-d` 换成 `anjia`）。

开发环境同理，在 `~/workspace/antony-casa-dev/.env` 里配，库名用 `anjia_dev`，
连接串的主机名是 `antony-casa-postgres`。

### 日常

```bash
# 只更新开发环境，不碰生产（在 WSL 里）
wsl -d Ubuntu -- bash /mnt/d/code/vivid/server/deploy/deploy.sh --dev-only

docker compose logs -f api-dev        # 开发环境日志，默认 DEBUG
docker compose up -d api-dev          # 只改了 DEV_* 配置时重启它
docker compose stop api-dev           # 出问题时停掉，线上无感
```

部署脚本最后会分别探生产和开发两个 `/health`。两条都验是有意的：`--dev-only`
理论上碰不到生产，而「理论上碰不到」正是要验一下的理由——compose 文件是整份同步
过去的，改错一行就可能连带重建 `api`。

**两边互不影响**：官网、后台、正式版小程序走 `api`；预览、体验版走 `api-dev`。
排查生产问题时别看 `api-dev` 的日志，反之亦然。

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

**迁移脚本同理**，把 `schema.sql` 换成 `migrations/xxx.sql` 即可。两个环境**各跑一次**
（生产 `-d "$POSTGRES_DB"`，开发 `-d antony_casa_dev`）——两个库在同一个 postgres
容器里，但迁移不会自动传染。

### 发「支付加固」这一支（012）

```bash
scp /mnt/d/code/vivid/server/migrations/012_payment_hardening.sql \
    deploy@antonycasa.weelume.com:/home/deploy/workspace/antony-casa/

ssh deploy@antonycasa.weelume.com
cd /home/deploy/workspace/antony-casa && source .env
# ① 生产库
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  < 012_payment_hardening.sql
# ② 开发库（同一个容器，另一个库）
docker compose exec -T postgres psql -U "$POSTGRES_USER" -d antony_casa_dev \
  < 012_payment_hardening.sql
```

**跑完、发代码之前**，两份 `.env` 各补一行 `ORDER_NO_PREFIX`：

```bash
# 生产
echo 'ORDER_NO_PREFIX=AX' >> /home/deploy/workspace/antony-casa/.env
# 开发
echo 'ORDER_NO_PREFIX=AD' >> /home/deploy/workspace/antony-casa-dev/.env
```

⚠️ **开发那份漏了就等于没改**：不配会退回默认的 `AX`，两个环境继续发同名的商户
订单号，开发环境的超时关单会去关生产上真实客户的订单。理由见
[`../README.md`](../README.md) 的「支付与对账」。

开发库里已有的 `AX` 开头待付款单（如果有）新代码不再扫，跑一次收掉：

```sql
UPDATE shop_orders SET status='closed', closed_at=now(), close_reason='never_submitted'
 WHERE status='pending_pay' AND order_no LIKE 'AX%';   -- 只在开发库跑
```

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

Caddy 给三个域名各自自动申请和续期，证书都在 `data/caddy/data/` 下。

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
- ~~COS 用的是开发桶~~ 已完成：`.env` 的 `COS_BUCKET` 是 `antonycasa-pro-1327365963`。
  改这一项只需要改 `.env` 再 `docker compose up -d api`，不用重新构建镜像；
  改之前要先把库里已引用的对象搬到新桶，否则首页配图、头像、服务申请图当场全挂
- `.env` 里用的还是开发环境的 `WX_SECRET`；`COS_SECRET_ID` / `COS_SECRET_KEY` 是
  账号级密钥，对账号下所有桶都有读写权限，应换成只授权生产桶的子账号密钥

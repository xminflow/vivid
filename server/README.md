# 小程序矩阵后端

小程序不能直连 Postgres，这里是中间的一层。目前只有展厅预约登记一个业务。

放在 `vivid/` 下而不是某个小程序里——矩阵里几个小程序共用这一套后端。

## 环境

- Python 3.12，包管理用 [uv](https://docs.astral.sh/uv/)
- 开发库：腾讯云 PostgreSQL 17，库名 `antony_casa_dev`
- 生产：自建，跑在 `antonycasa.weelume.com`（Caddy + FastAPI + PostgreSQL 18 三个容器），
  部署方式见 [`deploy/README.md`](deploy/README.md)
- 对象存储（COS）分环境，`ap-shanghai`，同一个腾讯云账号（APPID `1327365963`）：

  | | 桶 | 谁在用 |
  |---|---|---|
  | 开发 | `antony-casa-dev-1327365963` | 本地 / 开发库那一套 |
  | 生产 | `antonycasa-pro-1327365963` | 服务器上的 `.env` |

  两个桶都是「公有读私有写」。**唯一跨环境共用的是品牌实拍图**（`static/home`、
  `static/service`）：它是仓库里的固定文件、客户端写死地址直读，只放在生产桶一份，
  见下面「静态素材」。

## 配置

连接串放在 `.env` 里，**不进版本库**。首次拉代码后：

```bash
cp .env.example .env
# 填入真实连接串
```

密码含特殊字符时要 percent-encode（`!` → `%21`），否则 URI 解析会出错。

## 日志

级别由 `LOG_LEVEL` 控制，默认 `INFO`，**开发时设成 `DEBUG`**。取值不合法直接启动失败，
不会悄悄退回默认级别——那会让人以为开了 DEBUG 却看不到日志。

`app/logging_setup.py` 在建 app 之前配好 root logger。不配这一下的话，uvicorn 只给
自己的 `uvicorn.*` logger 装 handler，业务模块的日志会落到 Python 的兜底 handler：
没有时间戳和 logger 名，而且 INFO 直接被丢掉。

## 起服务

```bash
# 在 WSL 中（推荐，也是线上容器的跑法）
cd /mnt/d/code/vivid/server
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 3000
```

开发时加 `--reload` 改代码自动重启。

Windows 侧（微信开发者工具所在环境）直接访问 `http://127.0.0.1:3000`，WSL2 会自动转发。

### 原生 Windows（不进 WSL）

**不能直接用 `uvicorn app.main:app`**，要走这个脚本：

```powershell
cd D:\code\vivid\server
uv run python scripts/dev_server.py
```

原因：psycopg 的异步模式要求事件循环支持 `add_reader`，Windows 默认的
`ProactorEventLoop` 没有。直接用 uvicorn 起的话，启动就会
`PoolTimeout: pool initialization incomplete after 10 sec` + `Application startup failed`，
看着像连不上库，实际是循环选错了。而 uvicorn 从 0.36 起不再读 asyncio 的
event loop policy（改成了 `Config.get_loop_factory()`），所以在 `app/main.py` 里
`set_event_loop_policy` 是没用的——只能像 `scripts/dev_server.py` 那样自己建好
循环再把 uvicorn 跑进去。细节见那个脚本的 docstring。

该脚本不支持 `--reload`（reload 的活干在 uvicorn spawn 的子进程里，绕不过去）。
要 reload 就回 WSL。

端口 3000 是全链路统一的：本地 uvicorn、容器内监听（`Dockerfile` 的 `PORT`）、
小程序 `utils/config.js` 的 `API_BASE` 都用它，容器部署时宿主机也映射成 `-p 3000:3000`。

## 建表

```bash
uv run python -c "
import psycopg
from app.db import DATABASE_URL
with psycopg.connect(DATABASE_URL) as c:
    c.execute(open('schema.sql', encoding='utf-8').read()); c.commit()
"
```

`schema.sql` 可重复执行（都是 `IF NOT EXISTS`）。库已经建好了，这步只在换库时需要。

安家立业是**另一个库**（`anjia_dev`），基线在 `schema.anjia.sql`，用同一条命令、
把 `DATABASE_URL` 换成 `DATABASE_URL_ANJIA`、文件名换成 `schema.anjia.sql`。
一个进程装整个矩阵、一个小程序一个库，见 `docs/adr/0001-单进程多库承载小程序矩阵.md`。

已经建好的库要跟上新增的表和约束，走 `migrations/` 下的脚本，同样用上面那条命令、
把文件名换掉即可。每个脚本开头都写了前置条件、幂等性、回滚方式和验证 SQL。

最近一条是 `012_payment_hardening.sql`（支付加固：订单号约束放宽到两位字母前缀、
`shop_orders` 加 `sweep_attempts` / `last_pay_at` 两列、新增 `payment_anomalies` 表）。
**必须在发布新代码之前跑**——新代码引用这几列，先发代码会让下单和超时扫描直接报错。
跑完记得给这个环境配上 `ORDER_NO_PREFIX`（生产 `AX`、开发 `AD`，
见「支付与对账」一节，不配会退回默认的 `AX`，两个环境就撞号了）。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活检查 |
| POST | `/api/appointments` | 提交预约，成功返回 201 |
| POST | `/api/service-applications` | 提交服务申请，成功返回 201 |
| POST | `/api/upload-url` | 换一个 COS 直传地址 |
| POST | `/api/auth/login` | 用 `wx.login` 的 code 换登录态 |
| GET | `/api/users/me` | 读当前用户资料，要带 token |
| PUT | `/api/users/me` | 整份保存「我的信息」，要带 token |
| PUT | `/api/users/me/avatar` | 换头像，传 COS 对象键，要带 token |
| GET | `/api/users/me/appointments` | 当前用户自己的预约记录 |
| GET | `/api/home` | 首页三组配图，见下面「首页配图」 |
| GET | `/api/shop/categories` | 安玺·集 启用中的分类 |
| GET | `/api/shop/products` | 安玺·集 在售商品，分页 + `categoryId` / `keyword` |
| GET | `/api/shop/products/{id}` | 安玺·集 商品详情，下架的回 404 |

安玺·集那三条**不要求登录**：逛商品不该依赖登录态，真出现登录失败用户也该还能看商品。
详见下面「安玺·集」。

后台管理接口（`app/admin.py`，前端是 `website/`）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/appointments` | 展厅预约列表，分页 + 筛选 |
| DELETE | `/api/admin/appointments/{id}` | 删掉一条预约，不可撤销 |
| GET | `/api/admin/service-applications` | 服务申请列表，分页 + 筛选 |
| DELETE | `/api/admin/service-applications/{id}` | 删掉一条申请，不可撤销 |
| GET | `/api/admin/home-media` | 首页三组配图的当前配置 |
| PUT | `/api/admin/home-media/{slot}` | 整组替换某个位置的图 |
| POST | `/api/admin/home-media/upload` | 传一张首页图，返回 COS 对象键 |

安玺·集的维护接口在 `app/shop_admin.py`，另挂在 `/api/admin/shop` 下：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET / POST | `/api/admin/shop/categories` | 分类列表（带商品数）/ 新建 |
| PUT | `/api/admin/shop/categories/{id}` | 改分类名与排序值 |
| PUT | `/api/admin/shop/categories/{id}/status` | 停用 / 启用，**停用会连带下架其下在售商品** |
| POST | `/api/admin/shop/categories/{id}/activate-products` | 批量上架该分类下已下架的商品 |
| GET / POST | `/api/admin/shop/products` | 商品列表（分页 + 筛选）/ 新建 |
| GET / PUT / DELETE | `/api/admin/shop/products/{id}` | 商品详情 / 整份覆盖 / 硬删 |
| PUT | `/api/admin/shop/products/{id}/status` | 上架 / 下架 |
| POST | `/api/admin/shop/products/upload` | 传一张商品图，返回 COS 对象键 |

商品列表的查询参数：`page` / `pageSize` / `keyword`（只搜标题）/ `categoryId` / `status`。
没有删除分类的接口——分类只能停用，原因见下面「安玺·集」。

两个列表的公共查询参数：`page`（默认 1）、`pageSize`（默认 20，上限 100）、
`keyword`（姓名或手机号，`%` `_` 按字面量处理）。
预约另有 `visitorType`、`purpose`、`visitDateFrom` / `visitDateTo`（筛的是**到访日期**）；
服务申请另有 `serviceId`、`createdFrom` / `createdTo`（筛的是提交日期，含当天）。
枚举值传错直接 400，不会带着脏值查库。

两条 DELETE 是**硬删除**，没有软删除也没有回收站，记录不存在时回 404。二次确认由
后台页面负责。服务申请连着的 COS 图片**不删**（对象键随机，留在桶里猜不到，误删记录时
还能捞回来），只把对象键写进日志，方便日后按 `uploads/` 前缀对账。
这两张表原先有一个 `status`（跟进状态）列，只有默认值没有写入口，已经删掉，
运营改用「删除记录」清理线索——升级脚本见 `migrations/007_drop_status.sql`。

返回 `{ok, total, page, pageSize, items}`，`total` 是筛选后的总条数，与当前页无关。
服务申请的 `images` 出的是 `{组 id: [{key, url}]}`，`url` 是现签的一小时地址；
没配 COS 时 `url` 为 `null`——前端据此显示「地址签发失败」，而不是当成客户没传图。

以上接口全部要求登录：请求带 `Authorization: Bearer <token>`，token 从
`POST /api/admin/auth/login` 换来，登录与账号管理见 `app/admin_auth.py` 和
`app/admin_accounts.py`，运维说明见下面「上线前要做的」。

### 吊销登录态

会话是库里的一行（`admin_sessions`），有效期 12 小时、每次使用滑动续期，
但从签发起满 7 天绝对失效（`MAX_LIFETIME`）。

普通管理员有三条现成的吊销路径，全部在后台点得出来：改密码会删掉这个人在别处的
会话、停用账号会删掉它的全部会话、删号靠外键级联删会话。

**超管一条都没有**——它不入库，没有任何账号操作会牵连到它的会话，改配置重启也不清
（会话在库里，不在内存）。所以超管 token 一旦泄漏（截图、日志、共用电脑），
在它自己过期前只能手动删表行：

```sql
-- 吊销超管的全部登录态，下一次请求立刻 401
DELETE FROM admin_sessions WHERE is_super;

-- 只吊销某一条（知道具体 token 时，比如从日志里认出来的那条）
DELETE FROM admin_sessions WHERE token = '<token>';

-- 顺带看一眼现在有哪些超管会话、什么时候签发的
SELECT token, created_at, expires_at FROM admin_sessions WHERE is_super ORDER BY created_at;
```

生产库怎么连见 [`deploy/README.md`](deploy/README.md) 的「连库」。
删完最好再把 `.env` 里的 `ADMIN_SUPER_PASSWORD` 换一个并重启 api，
否则泄漏的如果是密码而不只是 token，删会话只是治标。

FastAPI 自带文档在 `/docs`。

POST 请求体（小程序传驼峰，模型两边都收）：

```json
{
  "name": "陈女士",
  "phone": "13612345678",
  "visitorType": "酒店民宿圈",
  "visitDate": "2026-10-01",
  "partySize": 4,
  "purpose": "展厅参观",
  "note": "",
  "spaceId": "bagno"
}
```

返回码：`201` 成功 / `400` 校验不过 / `409` 同号同日重复 / `500` 服务端错误。

校验失败返回的是中文提示，可直接弹给用户：

```json
{"ok": false, "message": "电话格式不正确", "errors": ["电话格式不正确"]}
```

## 用户与登录

`users` 表的业务主键是**雪花 ID**（`app/snowflake.py` 生成，不是 bigserial）：自增 id
会把注册量暴露给任何拿到 id 的人，将来跟别的库合并数据也容易撞主键。雪花 ID 有 17-18 位，
**超出 JS 的 `Number.MAX_SAFE_INTEGER`**，所以出接口一律转成字符串，小程序侧也当字符串用。

登录链路：

```
小程序 wx.login() → code
  → POST /api/auth/login {code, nickname?, avatarUrl?}
  → 服务端拿 code 调微信 code2session 换 openid（+ unionid、session_key）
  → 按 openid upsert 进 users：没有就发一个雪花 id 建号，有就复用原来的 id
  → 返回 {token, expiresAt, user}
之后请求带 Authorization: Bearer <token>
```

- **openid** 是这张表的自然键，微信给的、本小程序内唯一，认人全靠它
- **token** 是我们自己发的登录态，30 天过期。不拿 openid 当凭证：openid 泄了换不掉，token 能吊销
- **session_key**（解密手机号等加密数据用）和 openid 一样只留在库里，任何接口都不返回
- 昵称头像只有用户真授权了才覆盖，没授权不会把已有的洗成空串

需要在 `.env` 里配 `WX_APPID` / `WX_SECRET`，没配时登录接口返回「登录服务未配置」。
雪花 ID 的机器号（0-1023）必须逐实例不同：两个实例拿同一个机器号，会在同一毫秒
各自从序列 0 开始，算出完全相同的 id。id 是主键，重复不会污染数据，但插入会失败。

- 本地、单实例：`.env` 里配 `WORKER_ID` 即可
- 云托管等自动扩缩容的平台：**别配**。平台没法给每个实例注入不同的值，全用默认 0
  必冲突；留空时 `app/snowflake.py` 会按容器主机名（Pod 名带唯一后缀）推导
- 推导的代价：主机名压进 10 位只有 1024 个槽，N 个实例碰撞概率约 N²/2048
  （10 个实例约 5%）。实例数多或要求确定性时，仍应显式配

进程启动时会打一条日志说明机器号从哪来、值是多少，排查时先看它。

未登录也能提交预约（`user_id` 留空）；带了 token 提交就会记上归属，`/api/users/me/appointments`
才拉得到自己的记录。

## 头像

微信在 2022-10-25 之后收回了 `wx.getUserProfile` 的头像昵称（现在只返回灰色默认头像和
「微信用户」），所以头像不可能在登录时静默拿到，只能由用户在「我的」页点一下
`open-type="chooseAvatar"` 的按钮主动选。链路：

```
小程序 button open-type="chooseAvatar" → 微信给一个本机临时路径（wxfile://，重启即失效）
  → POST /api/upload-url 换直传地址 → 客户端 PUT 到 COS
  → PUT /api/users/me/avatar {avatarKey} → 落 users.avatar_key
  → 之后任何返回 user 的接口，avatarUrl 都是按这个键现签的临时地址
```

- 库里存的是**对象键**不是 URL：桶和地域会变，存 URL 会整批失效
- 头像地址签 7 天（`cos.AVATAR_EXPIRE_SECONDS`），比后台看图的 1 小时长得多——小程序把
  资料快照存在本机，下次冷启动先拿快照渲染，签太短用户第一眼就是一张拉不出来的图
- `avatar_key` 和 `avatar_url` 是两列：后者是微信授权给的外链，来源和生命周期都不同。
  出接口时 `avatar_key` 优先，没有才回落到 `avatar_url`，都没有就是空串，页面显示会员名首字
- 换头像不走 `PUT /api/users/me`：那个接口是整份覆盖 + 前端防抖，头像混进去会被反复重传，
  且表单里任一字段校验不过会连头像一起失败

## 静态素材

小程序主包上限 2MB，品牌实拍图占了 1.1MB。这些图不参与业务逻辑，换图也不该走发版，
所以放在 COS 的 `static/` 前缀下由小程序按固定 URL 加载：

```bash
uv run python scripts/upload_static.py          # 上传 + 回读校验
uv run python scripts/upload_static.py --check  # 只校验线上可访问
```

⚠️ 这个脚本**不跟 `.env` 的 `COS_BUCKET` 走**，默认写死推生产桶。因为引用这批图的地址
在客户端是硬编码的（小程序 `utils/config.js` 与官网 `src/shared/config.ts` 的
`STATIC_BASE`，外加官网 `index.html` 里三处），全都指着生产桶；跟着 `.env` 走的话，
本地换图会传进开发桶、脚本还报成功，线上纹丝不动。要传别处用 `--bucket`。
换桶时上面四处和 `scripts/upload_static.py` 的 `STATIC_BUCKET` 要一起改。

源文件仍在 `antony-casa/assets/home/`，靠 `project.config.json` 的 `packOptions.ignore`
排除出包——留在仓库里是为了版本追溯和换图有源。换实拍图的流程：替换同名文件 → 跑上传
脚本 → 生效，小程序不用发版。

两个前缀的区别：

| | `uploads/` | `static/` |
|---|---|---|
| 谁传的 | 用户（房屋照片、头像） | 我们（品牌素材、后台配的首页图） |
| 对象键 | 服务端随机生成 | 脚本传的是固定路径；后台传的是随机键 |
| 出接口 | 现签的临时地址 | 公开地址 |
| 桶收私有读后 | 不受影响 | 要单独设公有读 ACL |

## 首页配图

小程序首页的三组图由后台维护，不用发版也不用跑上传脚本：

| slot | 首页上的位置 | 张数上限 |
|---|---|---|
| `hero` | 最上方自动轮播的整屏实拍 | 10 |
| `showroom` | 「展厅预约」下面左右滑动的一组 | 12 |
| `activity` | 底部整张铺开的活动海报 | 1（库上有唯一索引） |

链路：后台选图 → `POST /api/admin/home-media/upload`（**裸的图片字节**，不是 multipart，
省一个 `python-multipart` 依赖；服务端按文件头认 JPG/PNG/WebP，不看文件名）→ 落到
`static/home-media/` 下 → 后台点保存 → `PUT /api/admin/home-media/{slot}` 整组替换 →
小程序 `GET /api/home` 读到公开直链。

出图地址不现签：首页图本来就是给所有访客看的，签名没有保护作用，还会让地址每次都变、
微信的图片缓存全部落空。

**「某个位置没配 = 用小程序里写死的兜底图」**（`antony-casa/mock/home.js`），不是
「没配 = 不显示」。这条约定让三件事可以分开做，中间任何时刻首页都不会开天窗：

1. 跑 `migrations/005_home_media.sql` 建表（表是空的，接口返回三个空数组）
2. 发布服务端 + 小程序（小程序拉到空配置，显示的还是原来那几张图）
3. 运营在后台配图（这时才真正换掉）

小程序侧还有一层本机缓存：首屏先用上一次成功拉到的配置渲染，同时请求接口覆盖；
接口失败就继续用缓存并打 `error` 日志（`antony-casa/utils/homeMedia.js`）。
代价是断网时用户可能看到上一版的图。

⚠️ **换下来的图不会从 COS 删除**。对象键是随机的，留在桶里不会被谁猜到，且删了就没法
回退到上一版配置。需要清理时按 `static/home-media/` 前缀列出对象，和
`SELECT image_key FROM home_media` 对账，差集才是可以删的。

## 安玺·集（商品目录）

`antony-casa` 小程序里的购物板块。**当前只做到阶段一：纯陈列**——运营能上架商品、
用户能浏览，但还**没有购物车、地址、订单和支付**（那些在阶段二至四，见
`docs/superpowers/specs/2026-08-10-anxi-ji-design.md`）。

两张表：`shop_categories`（分类）、`shop_products`（商品）。接口分两组——
`/api/shop/*` 是小程序只读、不鉴权；`/api/admin/shop/*` 是后台维护、挂 `current_admin`
（`app/shop.py` 与 `app/shop_admin.py`）。

几条容易踩的规则，改代码前先看清楚：

- **`shop_products.status` 是「商品能否被购买」的唯一判据**。没有第二个开关，
  也没有草稿态——新建出来就是 `off`，运营填完点上架。
- **分类只能停用，不能删除**。商品的分类是必填的，真删掉就得回答「这些商品归谁」。
  停用会把该分类下的在售商品**一并改写成下架**，而且**重新启用不会自动恢复**——
  恢复要显式点 `POST /api/admin/shop/categories/{id}/activate-products`。
  这么设计正是为了守住上一条：不让分类状态隐式决定商品的可购性，
  否则后台会显示「在售」而用户点不进去。
- **上架有两个前置条件**：至少一张商品图（第一张兼作列表页封面）、所属分类是启用的。
  下架无条件放行——出了问题要能立刻停售。
- **金额一律是整数分**。库里是 `int`、接口传的是 `int`，只有显示给人看时才换算成元
  （`website/src/lib/format.ts` 的 `formatYuan`、`antony-casa/utils/money.js` 的 `yuan`）。
  阶段三接微信支付后，对账用的也是分。
- **商品图走 `static/shop/` + 公开直链**，与首页配图同一套（运营素材，不是用户上传），
  上传接口同样收**裸的图片字节**。换下来的图不从 COS 删，清理方式见上一节。
- **删除商品是硬删**，没有软删除。阶段二起 `shop_order_items.product_id` 会以
  `ON DELETE RESTRICT` 引用它，卖过的商品会被数据库挡下来、只能下架——
  接口里已经写好了把外键违约翻译成 409 的分支，届时不用再改。

数量上限（`app/models.py` 的 `MAX_PRODUCT_*`，`schema.sql` 里有一份对应的 CHECK，
改的时候两处一起改）：图集 10 张、详情图 20 张、参数 20 条、单价上限一百万元
（这不是业务上限，是防手滑多打两个零——阶段三之后那会变成一笔真的收款）。

## 支付与对账

微信支付 JSAPI。协议那一层在 `app/wxpay.py`（只支持「微信支付公钥」模式，
不支持平台证书），业务编排在 `app/shop_pay.py`，退款与发货在 `app/shop_orders_admin.py`。

### ⚠️ 两个环境共用一个商户号

**微信支付没有沙箱**——开发环境（`dev.antonycasa.weelume.com` + `antony_casa_dev` 库）
和生产用的是同一个商户号、同一套密钥，在开发环境里付的也是真钱。

因此有**两项配置必须按环境分开**，少配任何一项都会串到另一边：

| 配置 | 生产 | 开发 | 不分开的后果 |
| --- | --- | --- | --- |
| `WXPAY_NOTIFY_URL` | `antonycasa.weelume.com/...` | `dev.antonycasa.weelume.com/...` | 开发环境的支付结果被推到生产容器 |
| `ORDER_NO_PREFIX` | `AX` | `AD` | 两边发出同名的商户订单号，见下 |

订单号是 `前缀 + YYYYMMDD + 6 位序号`，序号取自各自库里的 `shop_order_seq`，
两个库都从 1 开始发号。前缀不分开的话，同一天必然发出同名的 `out_trade_no`，
而它在**商户维度**必须唯一。撞上之后有三条自动发生、无人能拦的破坏路径：

1. **关单串台** — 超时扫描按单号调 `close_order`，关掉的是另一个环境里
   客户正在支付的那一单
2. **退款串台** — 每日对账下载的是**商户级**账单，开发环境退一笔测试单，
   生产上同号的订单第二天会被自动置成「已退款」
3. **下单串台** — 用已被对方占用的号下单，微信要么报单号重复，
   要么返回绑定到对方那笔订单的 prepay

代码里有三道前缀过滤（取号、账单解析、扫描取数），但**配置对了才谈得上防线**。
细节见 `app/order_no.py`。

### 支付异常台账

有三种情况是「微信说钱收了，但我们没法把它落到某笔订单上」：金额与订单不符、
单号在库里不存在、微信没给支付单号。这三种最终都要向微信返回 SUCCESS
（重投一百次结果一样），所以它们全部落进 `payment_anomalies` 表，
后台在 `/api/admin/shop/payment-anomalies` 能查、能标记已处理。

**台账里有一条就意味着有一笔钱没走通，要有人去看。** 定期跑：

```sql
SELECT kind, source, order_no, paid_cents, expected_cents, created_at
  FROM payment_anomalies WHERE resolved_at IS NULL ORDER BY created_at DESC;
```

### 其他要定期看的

```sql
-- 超时关单扫描已经放弃的单（连续处理失败到上限）。正常应为 0
SELECT order_no, sweep_attempts, created_at FROM shop_orders
 WHERE status = 'pending_pay' AND sweep_attempts >= 5 ORDER BY created_at;

-- 已发货但物流信息没传给微信的单。微信对实物交易有发货时限，超时影响交易权限
SELECT order_no, shipped_at FROM shop_orders
 WHERE shipped_at IS NOT NULL AND shipping_uploaded_at IS NULL ORDER BY shipped_at;
```

### 退款

退款只有超管能做（`POST /api/admin/shop/orders/{id}/refund`），只做整退，
金额取订单的 `total_cents`，请求里没有金额字段。

**同一笔订单永远只用一个 `out_refund_no`**（存在 `shop_orders.refund_id`）。
这是防重复退款的全部依据：微信对相同的退款单号 + 相同金额是幂等的，
所以并发点击、失败重试都只会退出去一笔。失败时 `refund_id` 会保留着，重试时沿用。

**商户平台上的退款不会回写本系统。** 兜底有两层：发货前查一次微信
（挡住「把已退款的单发出去」这个唯一会造成实物损失的场景），以及每日退款对账
（`app/shop_reconcile.py`，每次覆盖最近三天，管账面准确）。

### 回调

`POST /api/shop/pay/notify`，**无鉴权**——微信不会带我们的 token，身份完全靠验签。
`app/wxpay.py` 的 `verify_callback` 分三段：进 SDK 之前的头部前置校验（序列号、
签名类型、5 分钟时间窗口）、SDK 验签解密、商户号与 appid 比对。

那段前置校验不是「省事」：SDK 在序列号对不上时会去下载平台证书，也就是任何人
带一个乱写的 `Wechatpay-Serial` 打过来，都能让我们主动向微信发一次 HTTPS 请求。
细节写在 `_precheck_callback` 的注释里，改那个函数之前先读。

## 安家立业

第二个小程序，接口全在 `/api/anjia/` 下，数据在**另一个库**（`DATABASE_URL_ANJIA`），
见 `docs/adr/0001-单进程多库承载小程序矩阵.md`。没配那个连接串时整组路由不注册。

小程序侧：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/anjia/auth/login` | 用 `wx.login` 的 code 换登录态（用安家立业自己的 appid）|
| GET | `/api/anjia/users/me` | 当前用户的完整身份：登录态 + 会员 + 企业认证 |
| POST | `/api/anjia/users/me/membership` | 开通会员，**幂等** |
| POST | `/api/anjia/certifications` | 提交企业认证申请 |
| GET | `/api/anjia/cases` | 首页内容流，**游标**分页（`cursor` / `size`），只出已发布的 |
| GET | `/api/anjia/cases/{id}` | 案例详情，顺带记一次浏览 |
| POST | `/api/anjia/cases/images` | 传一张案例图（**裸字节**，非 multipart），回 `key/url/w/h` |
| POST | `/api/anjia/cases` | 发布案例 → 待审队列 |

后两条要求当前用户是**企业认证账号**（`cases.company_user`）。首页内容流只有企业
认证账号能发（需求文档 4.2），上传口挂的是同一道闸——只在发布时校验的话，任何登录
用户都能往公开可读的 `static/` 前缀里塞东西。

后台侧（`app/anjia/admin.py` 与 `app/anjia/cases_admin.py`，前端是 `website/` 的
「安家立业」分组）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/anjia/certifications` | 认证申请列表，`status` / `keyword` / `userId` 筛选 |
| POST | `/api/admin/anjia/certifications/{id}/approve` | 通过（只对待审的） |
| POST | `/api/admin/anjia/certifications/{id}/reject` | 驳回，`reason` 必填（只对待审的） |
| POST | `/api/admin/anjia/certifications/{id}/revoke` | 撤销（只对已通过的） |
| GET | `/api/admin/anjia/users` | 注册用户列表，只读 |
| GET | `/api/admin/anjia/cases` | 案例列表，`status` / `keyword` / `userId` 筛选，响应带四个状态的 `counts` |
| GET | `/api/admin/anjia/cases/{id}` | 审核详情，**不限状态**（软删的除外） |
| POST | `/api/admin/anjia/cases/{id}/approve` | 通过并发布（只对待审的） |
| POST | `/api/admin/anjia/cases/{id}/reject` | 驳回，`reason` 必填、`note` 选填（只对待审的） |

后台这几条挂的是 `current_admin` 而不是 `current_super`：认证是日常、高频、可撤销的
运营动作，卡在超管身上只会让审核积压。一个请求会同时用到两个池——会话在安东尼之家
的库，业务数据在安家立业的库。

### 案例（首页内容流）

一条案例 = 标题 + 图集（1~9 张）+ 正文，人工先审后发。几处反直觉的地方：

- **案例是一行会流转状态的实体，不是一次提交的记录**——与隔壁 `company_certifications`
  刻意相反，理由见 `docs/adr/0004-案例是状态实体而非提交记录.md`。
- **图片宽高焊在 COS 对象键里**（`static/anjia-case/<日期>/<uuid>_1200x1600.jpg`）。
  瀑布流要在图片加载之前就知道封面多高，而客户端自报的宽高不可信；键由服务端生成，
  改一个字符就指向一个不存在的对象，于是「键存在」本身担保了宽高没被改过。宽高由
  `cos.image_size` 读文件头解析（手写，没引 Pillow）。
- **落 `static/` 而不是 `uploads/`**：前者公开可读、地址稳定、能命中微信的图片缓存。
  代价是未过审的图只要键泄漏也能打开——键是 uuid，且未过审的案例不出现在任何列表里。
- **上传走服务端中转，不是预签名直传**：直传给不了可信的宽高，而且往公开可读的前缀
  签 PUT 地址等于在那里开一个可写位。
- **首页按发布时间倒序，不按 `views`**——这与需求文档 AJ-02 不一致，是有意偏离；
  `views` 照常在记（永久去重、作者不计），是二期做热度排序唯一的历史基准。
- 没被引用的案例图不做实时清理，按 `static/anjia-case/` 前缀由运维对账，与首页配图一致。

### 三层身份

**个人账号 / 企业认证账号 / 会员**，三者正交，不是等级：

- 「是不是企业认证账号」是**查出来的**——`company_certifications` 里有一条 `approved`
  就是。用户表上没有冗余列，所以撤销认证时不存在「忘了改第二个地方」。
- 认证是**记录**不是状态：一个用户可以有多条（驳回后重交、撤销后再申请各一条），
  最新一条决定他在小程序里看到什么。历史记录只出后台，是运营判断「这个人改过几版
  公司名」的依据。
- 「一个账号最多一条生效认证」「同时最多一条待审」都由**部分唯一索引**保证，不由
  应用层的先查再写保证——后者在并发下会漏。
- 认证不附带会员，会员也不是申请认证的前提。撤销认证不影响会员状态。

### 会员到期时间只写不读

开通会员时写入一年后的到期时间，但**一期任何地方都不校验它**，判断是不是会员只看
`is_member`。这不是漏掉的校验，理由见 `docs/adr/0002-会员到期时间只写不读.md`——
它是二期收费唯一的历史基准线，加上过期判断会让一批会员在一年后集体失效。

## 测试

```bash
uv run pytest -v
```

会连 `.env` 里配的那个库，用 `1355` 开头的手机号、`test_openid_` 开头的 openid 造数据，
跑完自己清干净，不碰真实预约和真实用户。登录测试不连微信，`code2session` 是打桩的。

注意：现在 `.env` 指向的是**远程开发库**，跑测试会真的往云上写数据再删掉。

## 改选项时注意

`来者身份` 和 `预约需求` 的选项值在**五处**出现，必须逐字一致：

1. `schema.sql` 的 CHECK 约束
2. `app/models.py` 的 `VISITOR_TYPES` / `PURPOSES`
3. `antony-casa/pages/booking/booking.js` 的同名常量（小程序）
4. `antony-web/src/features/booking/content.ts` 的同名常量（官网）
5. `website/src/features/antony/constants.ts` 的同名常量（后台管理的筛选下拉）

改一处就要改五处。前四处不一致的后果是表单能选、库里存不进去，且要到用户点提交才暴露；
第 5 处不一致则是后台的筛选项对不上库里的值，筛出来永远是空的。

**改选项值本身是数据库变更**（`visitor_type` 上有 CHECK 约束），不能只改代码：
要按 `migrations/` 下的两步走——先放宽约束到新旧并集，发布代码，再回填并收紧。
参考 `migrations/001_visitor_type_widen.sql` 和 `002_visitor_type_narrow.sql`。

服务申请的文案与表单定义同理，在 `antony-casa/mock/service.js` 和
`antony-web/src/features/services/content.ts` 各有一份。

安家立业的**企业认证状态**（`pending` / `approved` / `rejected` / `revoked`）在四处：
`schema.anjia.sql` 的 CHECK、`app/anjia/certifications.py` 的四个常量、
`website/src/features/anjia/types.ts` 的 `CertificationStatus`、
`anjia/pages/certification/certification.js` 的同名常量。前两处不一致的后果是接口写不进库，
后两处不一致的后果是界面把某个状态渲染成空白——不报错，只是那一行什么都不显示。

安家立业的**案例状态**（`pending` / `published` / `rejected` / `delisted`）同理在三处：
`schema.anjia.sql` 的 CHECK、`app/anjia/cases.py` 的四个常量与 `STATUS_LABELS`、
`website/src/features/anjia/types.ts` 的 `CaseStatus`（配 `constants.ts` 的 `CASE_STATUS`
与 `CASE_TABS`）。小程序侧目前只读不写状态，等「我的发布」做出来会多出第四处。

`性别` 同理，在三处：`schema.sql` 的 `gender` CHECK、`app/models.py` 的 `GENDERS`、
`antony-casa/mock/mine.js` 的 `genders`。小程序侧存的是下标，传给服务端前要转成文字
（库里存文字，不存下标——下标一改顺序，历史数据全错位）。

## 部署

生产环境是自建服务器，不是微信云托管。一条命令（在 WSL 里）：

```bash
bash deploy/deploy.sh              # 全量
bash deploy/deploy.sh --skip-web   # 只更新后端
```

镜像本地构建后 scp 过去 load——那台机器连不上 Docker Hub。详见 [`deploy/README.md`](deploy/README.md)。

同一台机器上还跑着一个 `api-dev` 容器，出在 `dev.antonycasa.weelume.com`，
给小程序的**预览 / 真机调试 / 体验版**用。它连的是同一个 postgres 容器里的另一个库
`antony_casa_dev`（已建好、表全）加开发桶，和生产互不影响。

```bash
bash deploy/deploy.sh --dev-only    # 只更新开发环境，不碰生产
```

`--dev-only` 靠**换镜像 tag** 做到这一点：镜像推成 `antony-casa-api:dev`，
而生产的 `api` 钉在 `latest` 上。`--skip-build` / `--skip-web` 做不到——
那两个开关不阻止换容器。

四个域名两两对应，只在「后端容器」和「库」两层分开：

| 域名 | 出什么 | 后端 | 库 |
|---|---|---|---|
| `antonycasa.weelume.com` | 官网 + 接口 | `api` | `antony_casa` |
| `admin.antonycasa.weelume.com` | 后台管理 | `api` | `antony_casa` |
| `dev.antonycasa.weelume.com` | 官网 + 接口 | `api-dev` | `antony_casa_dev` |
| `dev.admin.antonycasa.weelume.com` | 后台管理 | `api-dev` | `antony_casa_dev` |

⚠️ 这个开发库和本地开发**不是同一个**：本地这份 `.env` 指的是腾讯云的开发库，
服务器上那个在容器内网、本地连不到。要给预览环境造数据，去
`https://dev.admin.antonycasa.weelume.com/` 打开后台，账号是 `.env` 的
`DEV_ADMIN_SUPER_*`。三处 `WORKER_ID` 分别是本地 0、生产 0、开发 1，
详见 [`deploy/README.md`](deploy/README.md) 的「开发环境」。

## 上线前要做的

- ~~换成备案的 https 域名~~：已部署在 `https://antonycasa.weelume.com`（域名已备案，
  证书 Let's Encrypt 自动续期，TLS 1.2/1.3 都通），小程序也已切到 `API_MODE = 'server'`。
  **只差最后一步**：小程序后台「开发管理 - 开发设置 - 服务器域名」把它加进 request 合法域名。
  开发域名 `https://dev.antonycasa.weelume.com` 要一起加，预览和体验版打的是它
- ~~后台接口 `/api/admin/*` 没有鉴权~~ 已完成：需要登录才能访问。
  超级管理员由 `.env` 的 `ADMIN_SUPER_USERNAME` / `ADMIN_SUPER_PASSWORD` 指定，
  **上线前务必换成强密码，用户名也要一起换掉**——`.env.example` 里那个是占位符，
  别用 `root` / `admin` 这类能猜到的名字：失败计数锁的是用户名，对着一个已知的
  用户名每 15 分钟发 5 个错密码就能把超管永久锁死，而超管是唯一能建号、重置密码、
  启停账号的角色。其余管理员账号由超管登录后台后在「账号管理」页创建，
  系统没有注册入口。

  超管的密码在配置里，系统内改不了——改密码 = 改 `.env` + 重启服务。
  换来的是超管既停用不了也删不掉，后台不会被锁死在门外。

  超管被锁 / token 泄漏的处置 SQL 见 [`deploy/README.md`](deploy/README.md)
  「超管被锁住了 / token 泄漏了」一节。
- ~~CORS 现在是 `allow_origins=["*"]`~~ 已完成：默认只放行本地开发用的
  `localhost:5180 / 5190`（vite）。线上本来就用不到跨域——Caddy 把 `/api/*` 和前端
  产物放在同一个域名下，后台和官网请求接口都是同源的；小程序的 `wx.request`
  不走同源策略。要放开别的来源在 `.env` 配 `CORS_ORIGINS`（逗号分隔的完整来源）。
- **`ORDER_NO_PREFIX` 必须逐环境配**：生产 `AX`、开发 `AD`。两个环境共用同一个
  微信商户号，不分前缀会发出同名的商户订单号，后果见「支付与对账」一节。
  不配会退回默认的 `AX`，也就是**开发环境不配 = 直接撞生产**。
- ~~生产用的还是开发桶~~ 已完成：生产 `.env` 的 `COS_BUCKET` 已换成
  `antonycasa-pro-1327365963`，库里已有的 9 个对象（首页配图、头像、服务申请图）
  和品牌实拍图都已搬到生产桶并回读校验过。开发桶的原对象**没有删**——已发布的
  那一版小程序在重新发版之前，`STATIC_BASE` 还指着开发桶，删了首页就开天窗。
  等小程序发版并确认线上读的是生产桶之后，开发桶里的 `static/home`、
  `static/service`、以及已迁走的那 9 个键才可以清理
- **把 COS 桶权限收成「私有读写」**——实测两个桶都是「公有读私有写」，对象键虽然是 uuid
  且不能列举，但只要 URL 泄漏（转发、日志、截图），客户上传的房屋照片和头像谁都能长期访问。
  代码这边读写一律走预签名，改成私有读后不用动代码。
  **但 `static/` 前缀是例外**：小程序的品牌实拍图靠公开地址直接加载，桶收紧后要给
  `static/` 下的对象单独设公有读 ACL，否则首页图全挂（见下面「静态素材」）。
  注意后台配的首页图落在 `static/home-media/` 下、且是**持续新增**的，
  所以要的是「前缀级别的公有读策略」，不是一次性给现有对象打 ACL
- `WORKER_ID`：自建服务器是单实例，固定 `0` 即可（云托管那种自动扩缩容的平台才要留空
  走主机名推导）。将来加实例必须逐实例不同，否则雪花 ID 会撞主键
- 服务器上的 `.env` 里 `WX_SECRET` 还是开发环境那套，要换成生产的；
  secret 一旦外泄要去后台重置。COS 的**桶**已经分开了，但 `COS_SECRET_ID` /
  `COS_SECRET_KEY` 两边仍是同一把、且是账号级的密钥（能读写账号下所有桶），
  应当换成只对生产桶有读写权限的子账号密钥——否则「桶分开了」只挡得住手滑，
  挡不住密钥泄漏
- 小程序后台的 **request 合法域名**要把生产桶域名
  `https://antonycasa-pro-1327365963.cos.ap-shanghai.myqcloud.com` 加进去。
  头像和服务申请图是客户端直传 COS 的（`utils/upload.js`），域名没加的话真机上传
  会被微信拦下，errMsg 是 `url not in domain list`。开发桶那条也留着，
  开发版打到开发后端时签出来的是开发桶地址
- 生产库没有配自动备份，见 `deploy/README.md` 的「备份」一节

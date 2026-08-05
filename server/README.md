# 小程序矩阵后端

小程序不能直连 Postgres，这里是中间的一层。目前只有展厅预约登记一个业务。

放在 `vivid/` 下而不是某个小程序里——矩阵里几个小程序共用这一套后端。

## 环境

- Python 3.12，包管理用 [uv](https://docs.astral.sh/uv/)
- 数据库：腾讯云 PostgreSQL 17，库名 `antony_casa_dev`，表 `appointments`

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
# 在 WSL 中
cd /mnt/d/code/vivid/server
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 3000
```

开发时加 `--reload` 改代码自动重启。

Windows 侧（微信开发者工具所在环境）直接访问 `http://127.0.0.1:3000`，WSL2 会自动转发。

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

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活检查 |
| POST | `/api/appointments` | 提交预约，成功返回 201 |
| GET | `/api/appointments` | 最近 100 条，给后台临时看数据用 |
| POST | `/api/auth/login` | 用 `wx.login` 的 code 换登录态 |
| GET | `/api/users/me` | 读当前用户资料，要带 token |
| PUT | `/api/users/me` | 整份保存「我的信息」，要带 token |
| PUT | `/api/users/me/avatar` | 换头像，传 COS 对象键，要带 token |
| GET | `/api/users/me/appointments` | 当前用户自己的预约记录 |

FastAPI 自带文档在 `/docs`。

POST 请求体（小程序传驼峰，模型两边都收）：

```json
{
  "name": "陈女士",
  "phone": "13612345678",
  "visitorType": "酒店民宿业主",
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

源文件仍在 `antony-casa/assets/home/`，靠 `project.config.json` 的 `packOptions.ignore`
排除出包——留在仓库里是为了版本追溯和换图有源。换实拍图的流程：替换同名文件 → 跑上传
脚本 → 生效，小程序不用发版。

两个前缀的区别：

| | `uploads/` | `static/` |
|---|---|---|
| 谁传的 | 用户（房屋照片、头像） | 我们（品牌素材） |
| 对象键 | 服务端随机生成 | 固定路径，可预测 |
| 出接口 | 现签的临时地址 | 公开地址，写死在小程序里 |
| 桶收私有读后 | 不受影响 | 要单独设公有读 ACL |

## 测试

```bash
uv run pytest -v
```

会连 `.env` 里配的那个库，用 `1355` 开头的手机号、`test_openid_` 开头的 openid 造数据，
跑完自己清干净，不碰真实预约和真实用户。登录测试不连微信，`code2session` 是打桩的。

注意：现在 `.env` 指向的是**远程开发库**，跑测试会真的往云上写数据再删掉。

## 改选项时注意

`来者身份` 和 `预约需求` 的选项值在三处出现，必须逐字一致：

1. `schema.sql` 的 CHECK 约束
2. `app/models.py` 的 `VISITOR_TYPES` / `PURPOSES`
3. `antony-casa/pages/booking/booking.js` 的同名常量

改一处就要改三处，否则表单能选、库里存不进去。

`性别` 同理，在三处：`schema.sql` 的 `gender` CHECK、`app/models.py` 的 `GENDERS`、
`antony-casa/mock/mine.js` 的 `genders`。小程序侧存的是下标，传给服务端前要转成文字
（库里存文字，不存下标——下标一改顺序，历史数据全错位）。

## 上线前要做的

- 换成备案的 https 域名，在小程序后台配 request 合法域名（改 `antony-casa/utils/config.js` 的 `API_BASE`）
- **给 `GET /api/appointments` 加鉴权**——现在是裸奔的，任何人都能拉走全部客户姓名和手机号
- **把 COS 桶权限收成「私有读写」**——实测当前开发桶是「公有读私有写」，对象键虽然是 uuid
  且不能列举，但只要 URL 泄漏（转发、日志、截图），客户上传的房屋照片和头像谁都能长期访问。
  代码这边读写一律走预签名，改成私有读后不用动代码。
  **但 `static/` 前缀是例外**：小程序的品牌实拍图靠公开地址直接加载，桶收紧后要给
  `static/` 下的对象单独设公有读 ACL，否则首页图全挂（见下面「静态素材」）
- CORS 从 `*` 收窄到具体域名
- 云托管上不要配 `WORKER_ID`，留空走主机名推导；实例数多时再改成显式分配
- `.env` 里的 `WX_SECRET` 换成生产小程序的；secret 一旦外泄要去后台重置
- 换一套生产库账号：`app_dev` 是开发账号，不该用于线上
- 云库如果开了 IP 白名单，部署机器的出口 IP 要加进去

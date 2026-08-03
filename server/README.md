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

## 起服务

```bash
# 在 WSL 中
cd /mnt/d/code/vivid/server
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 3001
```

开发时加 `--reload` 改代码自动重启。

Windows 侧（微信开发者工具所在环境）直接访问 `http://127.0.0.1:3001`，WSL2 会自动转发。

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
多机部署时每台机器的 `WORKER_ID` 必须不同（0-1023），否则会发出重复的用户 id。

未登录也能提交预约（`user_id` 留空）；带了 token 提交就会记上归属，`/api/users/me/appointments`
才拉得到自己的记录。

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
- CORS 从 `*` 收窄到具体域名
- 小程序侧接上 `wx.login`：`utils/profile.js` 现在还是写本机 storage，要换成调
  `/api/users/me`（服务端这条链路已经通了）
- 生产环境的 `WORKER_ID` 按机器分配，别几台都用默认的 0
- 换一套生产库账号：`app_dev` 是开发账号，不该用于线上
- 云库如果开了 IP 白名单，部署机器的出口 IP 要加进去

# 管理端登录鉴权设计

日期：2026-08-07
涉及：`server/`（FastAPI）、`website/`（React 19 + shadcn，管理后台前端）

> `web/` 目录已废弃，本设计不涉及。

## 背景

`server/app/admin.py` 当前**没有任何鉴权**。它出的是全量客户姓名和手机号，一条 URL
泄漏就是全部客户资料泄漏，只能在内网或本机开发时用（见该文件开头的注释与
`server/README.md`）。本设计给它补上登录鉴权。

明确不做**注册**：管理端不对外开放注册入口。超级管理员由服务器配置文件指定，
其余管理员账号一律由超管在系统内创建。

## 一、角色模型与超管落地

两级角色：**超管** 与 **普通管理员**。

- 普通管理员：能进全部业务模块（展厅预约、服务申请、首页图片），看不到也进不去「账号管理」。
- 超管：额外能建号、重置密码、启用/停用、删号。

超管**完全不入库**，由配置文件定义：

```
ADMIN_SUPER_USERNAME=root
ADMIN_SUPER_PASSWORD=<强密码>
```

两项缺一 → **启动直接失败**（与 `DATABASE_URL` 的处理一致）。不给默认值，
否则某次部署忘了配就会让后台悄悄裸奔。

由此推出一个化简：`admin_users` 表里**所有人都是普通管理员**，
所以该表**不需要 `role` 列**。「是不是超管」只存在于会话记录里，
来源是登录时用户名是否命中 `ADMIN_SUPER_USERNAME`。

### 这个选择的代价（已确认接受）

1. 超管**无法在系统内修改自己的密码**——改密码 = 改 `.env` + 重启服务。
   前端「修改我的密码」对超管禁用，服务端 `PUT /api/admin/auth/password`
   收到超管会话时返回 400「超管密码在服务器配置里维护」。
2. 超管**无法被停用或删除**。这是好事：后台不会被锁死在门外。
3. 将来若要「多个超管」，需要改表加 `role` 列 + 改登录判定。本期不做。

## 二、数据模型

新增 `server/migrations/006_admin_auth.sql`，同时同步进 `server/schema.sql`
（该文件可重复执行，新表用 `CREATE TABLE IF NOT EXISTS`）。
配套 `006_rollback.sql` 按既有惯例提供。

### `admin_users`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `bigint` PK | 雪花 ID，`app/snowflake.py` 生成，与 `users` 表一致 |
| `username` | `text` NOT NULL UNIQUE | `CHECK (username ~ '^[a-zA-Z0-9_.-]{3,32}$')` |
| `display_name` | `text` NOT NULL DEFAULT `''` | `CHECK (length(display_name) <= 40)` |
| `password_hash` | `text` NOT NULL | 格式见第三节 |
| `status` | `text` NOT NULL DEFAULT `'active'` | `CHECK (status IN ('active','disabled'))` |
| `last_login_at` | `timestamptz` | |
| `login_count` | `integer` NOT NULL DEFAULT 0 | |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` 复用既有的 `touch_updated_at()` 触发器 |

不设 `created_by`：只有超管能建号，这一列恒为超管，没有信息量。

### `admin_sessions`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `token` | `text` PK | `secrets.token_urlsafe(32)` |
| `admin_id` | `bigint` NULL | `REFERENCES admin_users(id) ON DELETE CASCADE` |
| `is_super` | `boolean` NOT NULL DEFAULT false | |
| `expires_at` | `timestamptz` NOT NULL | |
| `created_at` | `timestamptz` NOT NULL DEFAULT `now()` | |

约束：

```sql
CONSTRAINT admin_sessions_subject CHECK (
  (is_super AND admin_id IS NULL) OR (NOT is_super AND admin_id IS NOT NULL)
)
```

超管会话 `admin_id IS NULL`（超管不入库，没有可引用的行）。

`ON DELETE CASCADE` 是关键：超管删掉一个账号，那个人的下一个请求就掉线，
不需要在删除接口里另写「顺手清会话」的代码，也就不会漏。

索引：`CREATE INDEX admin_sessions_admin_idx ON admin_sessions (admin_id)` ——
停用账号时要按 `admin_id` 批量删会话。

### `admin_login_attempts`

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `username` | `text` PK | |
| `fail_count` | `integer` NOT NULL DEFAULT 0 | |
| `locked_until` | `timestamptz` | NULL 表示未锁定 |

规则：连续失败 5 次 → 锁 15 分钟；登录成功清零。按 `username` 记，
超管和普通管理员一视同仁——超管那把钥匙尤其需要这层保护。

**为什么放库不放内存**：云托管会多实例，进程内计数等于没计数。
超管密码是整个后台唯一的钥匙，这条防护不能省。

## 三、密码哈希

用标准库 `hashlib.scrypt`，**不引入 bcrypt / passlib**。
这个项目为了少一个依赖连 `python-multipart` 都没装（见 `admin.py` 的图片上传注释），
scrypt 是标准库里的密码哈希函数，够用。

存储格式（单字符串，自带参数，便于将来调参而不破坏存量）：

```
scrypt$<n>$<r>$<p>$<salt-base64>$<hash-base64>
```

默认参数 `n=16384, r=8, p=1`，16 字节随机 salt，64 字节输出。
校验用 `hmac.compare_digest` 做常量时间比对。

超管密码在配置里是明文，登录时同样用 `compare_digest` 比对，不走哈希。

新增 `server/app/security.py`，只放 `hash_password()` / `verify_password()` 两个函数。

## 四、会话

- TTL **12 小时**。后台出的是全量客户手机号，不能像小程序 `users.token` 那样给 30 天。
- **滑动续期**：每次鉴权通过时，若剩余不足 TTL 的一半，顺手 `UPDATE` 延到 `now() + 12h`，
  避免运营干到一半被踢。
- 前端把 token 存 `localStorage`，请求走 `Authorization: Bearer <token>`。
- 过期会话不主动清理：`expires_at > now()` 已经挡住了，表很小，
  留给将来的定时任务或手工清理。

## 五、后端接口

新增 `server/app/admin_auth.py`（登录与自身密码）和 `server/app/admin_accounts.py`（账号管理）。

```
POST   /api/admin/auth/login                无需鉴权
POST   /api/admin/auth/logout               需登录
GET    /api/admin/auth/me                   需登录
PUT    /api/admin/auth/password             需登录；超管调用 → 400

GET    /api/admin/accounts                  超管
POST   /api/admin/accounts                  超管
PUT    /api/admin/accounts/{id}/password    超管；重置他人密码
PUT    /api/admin/accounts/{id}/status      超管；停用时同步清空该人全部会话
DELETE /api/admin/accounts/{id}             超管；会话随外键级联删除
```

停用既删会话、`current_admin()` 又查一次 `status`，两道看着重复，都要留：
删会话是立刻回收（下一个请求就掉线，不等 12 小时），查 `status` 是兜底
（将来若有别处改了状态却忘了删会话，鉴权层仍然挡得住）。

### 挂载方式（本设计的重点）

正好接上 `admin.py` 开头注释里预留的做法：

- 既有 `admin_router` 加 `dependencies=[Depends(require_admin)]`，
  **一行覆盖全部后台接口，包括将来新加的**，不会漏。
- `auth_router` 是独立的 router、单独 `include_router`，**不带**那个依赖，
  否则登录接口自己要求登录。
- `accounts_router` 挂 `dependencies=[Depends(require_super)]`，
  权限判定集中在 router 层，不散落在每个函数体里。

### 依赖函数

放在 `admin_auth.py`：

- `current_admin()` — 从 `Authorization` 头解 token，查 `admin_sessions` join `admin_users`，
  校验未过期、账号未停用；顺手做滑动续期。返回 `{admin_id, username, display_name, is_super}`，
  超管返回配置里的用户名 + `is_super=True`。
- `require_admin` = `Depends(current_admin)`
- `require_super` — 在 `current_admin` 基础上判 `is_super`，否则 403。

### 错误语义

- 未带 token / token 无效或过期 → **401**「登录已过期，请重新登录」
- 账号已停用 → **403**「账号已被停用，请联系超级管理员」
- 普通管理员访问账号管理 → **403**「没有权限」
- 登录失败一律回「用户名或密码不正确」，**不区分**「用户不存在」和「密码错误」，
  不给爆破者做用户名枚举。
- 被锁定 → 429「密码错误次数过多，请 N 分钟后再试」

响应体沿用既有约定：成功 `{ok: true, ...}`，失败由 `main.py` 的
`HTTPException` 处理器统一成 `{ok: false, message}`。

### 建号与改密的输入约束

- 用户名：`^[a-zA-Z0-9_.-]{3,32}$`，与库上的 CHECK 一致
- 密码：长度 8–64。不强制大小写数字符号组合——这类规则实际会把人逼去写便签
- 用户名重复 → 409「这个用户名已被占用」
- 建号时若用户名等于 `ADMIN_SUPER_USERNAME` → 400「这个用户名被保留」，
  否则会出现库里一个同名普通管理员、登录时永远被超管判定抢先命中的鬼影账号

## 六、前端（`website/`）

### 新增文件

- `src/features/auth/api.ts` — 登录、登出、me、改密
- `src/features/auth/session-context.tsx` — token + 当前用户的 Context，token 落 `localStorage`
- `src/features/auth/login-page.tsx` — 用户名 / 密码 / 提交，错误就地显示
- `src/features/auth/change-password-dialog.tsx` — 旧密码 + 新密码 + 确认；超管进来是禁用态加说明
- `src/features/accounts/api.ts`
- `src/features/accounts/accounts-page.tsx` — 列表 + 新建 + 重置密码 + 启用/停用 + 删除

### 改动

- `src/features/antony/api.ts` 的 `call()` 统一注入 `Authorization: Bearer`，
  并在 **401 时清 token 跳登录页**。这是唯一的处理点，各业务页不用各自判断。
  （`accounts/api.ts` 与 `auth/api.ts` 复用同一个 `call()`，请求头逻辑只写一遍——
  把它抽到 `src/lib/api.ts`，`features/antony/api.ts` 改为引用。）
- `src/main.tsx` — 加 `/login` 路由；用 `RequireAuth` 包住 `AppShell` 那组路由，
  未登录一律重定向到 `/login`。
- `src/app-shell.tsx` — 侧栏底部加账号区（显示名 / 修改密码 / 退出登录）；
  `NAV` 里增加「账号管理」分组，**仅当 `isSuper` 时渲染**。
  服务端已经挡了，前端隐藏只是不给运营看见点不动的入口。

### 会话恢复

应用启动时若 `localStorage` 里有 token，先调 `GET /api/admin/auth/me` 验一次再放行，
期间显示骨架屏。不直接信任本地缓存的用户信息：账号可能已被停用或降权。

## 七、测试

新增 `server/tests/test_admin_auth.py`：

- 超管用配置里的账号密码能登录，拿到 token
- 密码错误 → 401，且响应里不泄漏「用户是否存在」
- 连错 5 次后第 6 次 → 429
- 无 token 访问 `GET /api/admin/appointments` → 401（验证 router 级依赖真的生效）
- 超管建号 → 新账号能登录
- 普通管理员访问 `GET /api/admin/accounts` → 403
- 普通管理员改自己密码成功；旧密码错 → 400
- 超管调 `PUT /api/admin/auth/password` → 400
- 停用账号后，该账号已有的 token 立刻失效
- 删除账号后，其会话随级联删除，token 失效
- 建号时用保留用户名 → 400；重复用户名 → 409

### 对既有测试的破坏性改动

`tests/test_admin.py` 和 `tests/test_home_media.py` 打的接口现在**全部需要鉴权**，
两个文件的全部请求都要带 token。在 `tests/conftest.py` 增加一个 session 级
`admin_token` fixture（用配置里的超管登录一次），两个文件引用它。

这是既有测试的改动，不是纯新增，实施时要一并完成，否则测试会整片变红。

## 八、上线注意（写进 `server/README.md`）

- `main.py` 的 CORS 是 `allow_origins=["*"]`。token 走 header 不走 cookie，
  `*` 不构成漏洞，但后台上线时应收窄到具体域名。**本轮不改行为**，只补文档。
- `.env.example` 增加 `ADMIN_SUPER_USERNAME` / `ADMIN_SUPER_PASSWORD` 两项及说明。
- README 里「上线前要做的」中关于「后台接口无鉴权」的条目改为已完成，
  并补上「超管密码要改成强密码、改密码需重启服务」的说明。

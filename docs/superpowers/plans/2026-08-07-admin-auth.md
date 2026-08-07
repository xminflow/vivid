# 管理端登录鉴权 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 `server/app/admin.py` 那组「出全量客户手机号」的后台接口补上登录鉴权，超管由配置文件指定，其余管理员由超管在系统内创建，不做注册。

**Architecture:** 后端 FastAPI 侧新增三张表（管理员、会话、登录失败计数）与两个 router；鉴权依赖 `current_admin` 挂在既有 `admin_router` 上，一次覆盖全部后台接口。超管**不入库**，登录时拿配置比对，会话行以 `admin_id IS NULL` + `is_super = true` 表示。前端 `website/` 把请求出入口收敛到 `src/lib/api.ts`，统一注入 `Authorization` 并在 401 时送回登录页。

**Tech Stack:** Python 3.12 / FastAPI / psycopg3(async) / PostgreSQL / 标准库 `hashlib.scrypt`；React 19 / TypeScript / react-router-dom 7 / Tailwind 4 / shadcn(radix-ui) / sonner。

**Spec:** `docs/superpowers/specs/2026-08-07-admin-auth-design.md`

## Global Constraints

- **不新增任何后端依赖。** 密码哈希用标准库 `hashlib.scrypt`，不装 bcrypt / passlib / pyjwt。这个项目为了少一个依赖连 `python-multipart` 都没装。
- **不新增任何前端依赖。** 需要的 shadcn Dialog 组件在本计划里手写进 `components/ui/`，用已装的 `radix-ui` 包。
- 所有注释和用户可见文案用**中文**，与既有代码一致。
- 接口出参一律 **camelCase**；成功 `{"ok": true, ...}`，失败由 `main.py` 的 `HTTPException` 处理器统一成 `{"ok": false, "message": ...}`。
- **雪花 ID 出接口必须转字符串**（18 位，超过 JS 的 `Number.MAX_SAFE_INTEGER`）。
- SQL 一律用参数化占位符 `%s`，不做字符串拼接。
- 库上的 CHECK 约束取值必须与 `app/models.py` 里的 `Literal` **逐字一致**。
- `website/` 没有测试框架（只有 `oxlint`）。前端任务的验证方式是 `pnpm build`（含 `tsc -b`）+ `pnpm lint` + 明确的手工验证步骤。**不要为此引入测试框架**。
- 后端测试需要**真实数据库可连**，跑法：`cd server && uv run pytest`。

## File Structure

**新建：**

| 文件 | 职责 |
| --- | --- |
| `server/app/security.py` | 密码哈希与校验，两个纯函数，不碰数据库 |
| `server/app/admin_auth.py` | 超管配置、登录/登出/me/改密接口、`current_admin` / `current_super` 依赖 |
| `server/app/admin_accounts.py` | 账号管理接口（仅超管） |
| `server/migrations/006_admin_auth.sql` | 三张新表 |
| `server/migrations/006_rollback.sql` | 回滚 |
| `server/tests/test_security.py` | 哈希函数单元测试 |
| `server/tests/test_admin_auth.py` | 登录、限流、权限、会话失效 |
| `website/src/lib/api.ts` | 后台请求统一出入口：注入 token、401 处理 |
| `website/src/lib/format.ts` | 时间格式化，从 `features/antony/use-paged-list.ts` 挪出来给两个 feature 共用 |
| `website/src/components/ui/dialog.tsx` | 居中弹窗，改密码和新建账号用 |
| `website/src/features/auth/api.ts` | 登录相关接口调用 |
| `website/src/features/auth/session-context.tsx` | 当前登录态的 Context |
| `website/src/features/auth/require-auth.tsx` | 路由守卫 |
| `website/src/features/auth/login-page.tsx` | 登录页 |
| `website/src/features/auth/change-password-dialog.tsx` | 修改我的密码 |
| `website/src/features/accounts/api.ts` | 账号管理接口调用 |
| `website/src/features/accounts/accounts-page.tsx` | 账号管理页 |

**修改：**

| 文件 | 改动 |
| --- | --- |
| `server/app/models.py` | 追加管理端登录相关的 pydantic 模型 |
| `server/app/admin.py:36` | router 加 `dependencies=[Depends(current_admin)]`；改开头的「当前没有鉴权」注释 |
| `server/app/main.py` | include 两个新 router；`FIELD_MESSAGES` 补字段文案 |
| `server/schema.sql` | 同步三张新表 |
| `server/tests/conftest.py` | 加 `auth_headers` fixture |
| `server/tests/test_admin.py:57-64` | `client` fixture 带上 `auth_headers` |
| `server/tests/test_home_media.py` | 同上 |
| `server/.env.example` | 加两个超管配置项 |
| `server/README.md` | 「上线前要做的」里鉴权那条改为已完成，补运维说明 |
| `website/src/features/antony/api.ts` | `call`/`get` 改为从 `@/lib/api` 引入 |
| `website/src/features/antony/use-paged-list.ts` | 挪走 `formatTime` |
| `website/src/features/antony/appointments-page.tsx:29` | `formatTime` 改从 `@/lib/format` 引入 |
| `website/src/features/antony/service-applications-page.tsx:37` | 同上 |
| `website/src/main.tsx` | 加 `/login` 路由，`RequireAuth` 包住 AppShell |
| `website/src/app-shell.tsx` | 侧栏账号区；「账号管理」菜单仅超管可见 |

---

### Task 1: 密码哈希

**Files:**
- Create: `server/app/security.py`
- Test: `server/tests/test_security.py`

**Interfaces:**
- Consumes: 无（纯标准库）
- Produces:
  - `hash_password(password: str) -> str`
  - `verify_password(password: str, stored: str) -> bool`
  - 常量 `PASSWORD_MIN_LEN = 8`、`PASSWORD_MAX_LEN = 64`

- [ ] **Step 1: 写失败的测试**

创建 `server/tests/test_security.py`：

```python
"""密码哈希的单元测试。纯函数，不需要数据库。"""

from app.security import hash_password, verify_password


def test_same_password_hashes_differently_but_both_verify():
    a = hash_password("correct horse")
    b = hash_password("correct horse")
    # 每次盐不同，两条哈希不能一样——一样就说明没加盐，
    # 拖库的人一眼能看出哪些人用了同一个密码
    assert a != b
    assert verify_password("correct horse", a)
    assert verify_password("correct horse", b)


def test_wrong_password_is_rejected():
    stored = hash_password("correct horse")
    assert not verify_password("Correct horse", stored)
    assert not verify_password("correct hors", stored)
    assert not verify_password("", stored)


def test_stored_format_carries_its_own_parameters():
    # 参数存在哈希串里，将来调大 n 时存量密码仍按各自的参数校验，
    # 不需要强制所有人改密码
    parts = hash_password("whatever").split("$")
    assert len(parts) == 6
    assert parts[0] == "scrypt"
    assert parts[1].isdigit()


def test_malformed_stored_value_is_rejected_not_raised():
    # 库里混进格式不对的值时，登录接口该回「密码错误」，不该 500
    for junk in ("", "plaintext", "scrypt$bad", "bcrypt$1$2$3$4$5", "scrypt$a$b$c$d$e"):
        assert not verify_password("x", junk)
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd server && uv run pytest tests/test_security.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.security'`

- [ ] **Step 3: 写实现**

创建 `server/app/security.py`：

```python
"""密码哈希。

用标准库的 scrypt，不引 bcrypt / passlib：这个项目为了少一个依赖，连后台传图
都没装 python-multipart（见 admin.py 的上传接口）。scrypt 是标准库里现成的、
抗 GPU 爆破的密码哈希函数，够用。

存储格式自带参数：

    scrypt$<n>$<r>$<p>$<salt-base64>$<hash-base64>

参数写进串里而不是写死在代码里，是为了将来调大 n 时存量密码仍按各自记录里的
参数校验——否则调参那天所有人都得重设密码。
"""

import base64
import hashlib
import hmac
import secrets

# CPU/内存开销。n=16384, r=8 要 128*n*r = 16MB 内存，算一次几十毫秒：
# 登录接口等得起，爆破的人则要为每个候选密码付同样的代价。
# 注意别把 n 调到需要超过 32MB，那是 OpenSSL 的默认上限，会直接抛错
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 64

# 密码长度。下限 8 位挡住生日和 123456；不强制大小写数字符号组合——
# 那类规则实际上会把人逼去把密码写在便签上
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 64


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码。

    解析不了的 stored 一律当「密码不对」返回 False，不往外抛：库里万一混进一条
    格式不对的哈希，登录接口该回「用户名或密码不正确」，不该 500 给对方看。
    """
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(key_b64, validate=True)
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False

    # 常量时间比对：普通的 == 会在第一个不同的字节上返回，
    # 逐字节的耗时差能被用来一位一位地猜出哈希
    return hmac.compare_digest(actual, expected)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd server && uv run pytest tests/test_security.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add server/app/security.py server/tests/test_security.py
git commit -m "feat(server): 密码哈希用标准库 scrypt"
```

---

### Task 2: 建表

**Files:**
- Create: `server/migrations/006_admin_auth.sql`, `server/migrations/006_rollback.sql`
- Modify: `server/schema.sql`（在文件末尾追加一节）

**Interfaces:**
- Consumes: `schema.sql` 里已有的 `touch_updated_at()` 触发器函数
- Produces: 三张表，后续任务按下列列名读写
  - `admin_users(id, username, display_name, password_hash, status, last_login_at, login_count, created_at, updated_at)`
  - `admin_sessions(token, admin_id, is_super, expires_at, created_at)`
  - `admin_login_attempts(username, fail_count, locked_until)`

- [ ] **Step 1: 写迁移**

创建 `server/migrations/006_admin_auth.sql`：

```sql
-- 管理端登录鉴权 —— 新建三张表（发布新代码之前跑）
--
-- 背景：/api/admin 那组接口出的是全量客户姓名和手机号，此前完全没有鉴权，
-- 只能在内网用。这三张表给它补上登录。
--
-- 纯新增，不动任何已有表和列：建完表旧版本服务照常跑，不需要停机。
-- 但注意**发布新代码之后**后台立刻需要登录，运营要提前拿到超管账号。
--
-- 前置条件：schema.sql 里的 touch_updated_at() 已存在（users 表那一节建的）。
-- 幂等：CREATE ... IF NOT EXISTS + DROP TRIGGER IF EXISTS，可重复执行。
-- 回滚：跑 006_rollback.sql。

BEGIN;

-- 普通管理员。超级管理员**不在这张表里**——它由服务器配置文件
-- （ADMIN_SUPER_USERNAME / ADMIN_SUPER_PASSWORD）定义，见 app/admin_auth.py。
-- 所以这张表不需要 role 列：里面的每一行都是普通管理员。
CREATE TABLE IF NOT EXISTS admin_users (
  id            bigint      PRIMARY KEY,

  -- 登录用户名。限制成 ASCII 是为了不出现「看起来一样但不是同一个」的用户名
  -- （全角字母、前后空格、同形字符），那会让运营以为账号被盗
  username      text        NOT NULL UNIQUE
                            CONSTRAINT admin_users_username_format
                            CHECK (username ~ '^[a-zA-Z0-9_.-]{3,32}$'),

  -- 给人看的姓名，可留空
  display_name  text        NOT NULL DEFAULT ''
                            CHECK (length(display_name) <= 40),

  -- 格式见 app/security.py，自带算法与参数
  password_hash text        NOT NULL,

  status        text        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'disabled')),

  last_login_at timestamptz,
  login_count   integer     NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS admin_users_touch_updated_at ON admin_users;
CREATE TRIGGER admin_users_touch_updated_at
  BEFORE UPDATE ON admin_users
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 登录态。
--
-- admin_id 可空：超管不在 admin_users 里，没有可引用的行，它的会话
-- admin_id 是 NULL、is_super 是 true。CHECK 把两者锁成互斥，
-- 免得出现「既是超管又指向某个普通账号」这种解释不清的行。
--
-- ON DELETE CASCADE 是有意的：超管删掉一个账号，那人的会话跟着没，
-- 下一个请求就掉线。不用在删除接口里另写一句「顺手清会话」，也就不会漏。
CREATE TABLE IF NOT EXISTS admin_sessions (
  token       text        PRIMARY KEY,
  admin_id    bigint      REFERENCES admin_users (id) ON DELETE CASCADE,
  is_super    boolean     NOT NULL DEFAULT false,
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),

  CONSTRAINT admin_sessions_subject CHECK (
    (is_super AND admin_id IS NULL) OR (NOT is_super AND admin_id IS NOT NULL)
  )
);

-- 停用账号时要按 admin_id 批量删会话
CREATE INDEX IF NOT EXISTS admin_sessions_admin_idx ON admin_sessions (admin_id);

-- 登录失败计数，防爆破。
--
-- 为什么落库不放进程内存：云托管会多实例，内存计数等于没计数。
-- 超管密码是整个后台唯一的钥匙，这层保护不能省。
-- 按 username 记而不是按 IP：超管账号只有一个，锁它比锁 IP 有效，
-- 也不会因为运营在同一个办公室出口 IP 下而互相牵连。
CREATE TABLE IF NOT EXISTS admin_login_attempts (
  username     text        PRIMARY KEY,
  fail_count   integer     NOT NULL DEFAULT 0,
  locked_until timestamptz
);

COMMIT;

-- 验证：
--   三张表都在（应返回 3 行）
--     SELECT tablename FROM pg_tables
--      WHERE tablename IN ('admin_users', 'admin_sessions', 'admin_login_attempts');
--   用户名格式约束真的生效（应报 check constraint violation）
--     INSERT INTO admin_users (id, username, password_hash) VALUES (1, 'ab', 'x');
--   会话主体互斥约束真的生效（两条都应报错，随后回滚）
--     BEGIN;
--     INSERT INTO admin_sessions (token, admin_id, is_super, expires_at)
--       VALUES ('t1', NULL, false, now() + interval '1 hour');
--     ROLLBACK;
```

- [ ] **Step 2: 写回滚**

创建 `server/migrations/006_rollback.sql`：

```sql
-- 006 的回滚：删掉三张表。
--
-- 删了之后后台就没有登录能力了，服务端代码必须先回退到不带鉴权的版本再跑这个，
-- 否则接口会因为查不到表而全部 500。
--
-- admin_sessions 有指向 admin_users 的外键，先删子表。

BEGIN;

DROP TABLE IF EXISTS admin_login_attempts;
DROP TABLE IF EXISTS admin_sessions;
DROP TABLE IF EXISTS admin_users;

COMMIT;
```

- [ ] **Step 3: 同步进 schema.sql**

在 `server/schema.sql` **末尾**追加（内容与迁移里的 CREATE 部分逐字一致，只是去掉 `BEGIN/COMMIT` 和验证注释）：

```sql
-- ============================================================
-- 管理端账号
-- ============================================================
-- 后台 /api/admin 那组接口出的是全量客户资料，必须登录才能看。
--
-- 超级管理员**不在 admin_users 里**：它由服务器配置文件
-- （ADMIN_SUPER_USERNAME / ADMIN_SUPER_PASSWORD）定义，见 app/admin_auth.py。
-- 所以这张表不需要 role 列——里面每一行都是普通管理员，由超管在后台创建。
CREATE TABLE IF NOT EXISTS admin_users (
  id            bigint      PRIMARY KEY,
  username      text        NOT NULL UNIQUE
                            CONSTRAINT admin_users_username_format
                            CHECK (username ~ '^[a-zA-Z0-9_.-]{3,32}$'),
  display_name  text        NOT NULL DEFAULT ''
                            CHECK (length(display_name) <= 40),
  password_hash text        NOT NULL,
  status        text        NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'disabled')),
  last_login_at timestamptz,
  login_count   integer     NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now()
);

DROP TRIGGER IF EXISTS admin_users_touch_updated_at ON admin_users;
CREATE TRIGGER admin_users_touch_updated_at
  BEFORE UPDATE ON admin_users
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- 登录态。admin_id 可空是因为超管不入库，它的会话 admin_id 是 NULL、
-- is_super 是 true，CHECK 把两者锁成互斥。
-- ON DELETE CASCADE：删号即踢下线，不用另写清会话的代码。
CREATE TABLE IF NOT EXISTS admin_sessions (
  token       text        PRIMARY KEY,
  admin_id    bigint      REFERENCES admin_users (id) ON DELETE CASCADE,
  is_super    boolean     NOT NULL DEFAULT false,
  expires_at  timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT admin_sessions_subject CHECK (
    (is_super AND admin_id IS NULL) OR (NOT is_super AND admin_id IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS admin_sessions_admin_idx ON admin_sessions (admin_id);

-- 登录失败计数，防爆破。落库不放内存：云托管多实例时内存计数等于没计数
CREATE TABLE IF NOT EXISTS admin_login_attempts (
  username     text        PRIMARY KEY,
  fail_count   integer     NOT NULL DEFAULT 0,
  locked_until timestamptz
);
```

- [ ] **Step 4: 在本地库上跑迁移并验证**

Run（`$DATABASE_URL` 取自 `server/.env`）：

```bash
cd server
psql "$DATABASE_URL" -f migrations/006_admin_auth.sql
psql "$DATABASE_URL" -c "SELECT tablename FROM pg_tables WHERE tablename IN ('admin_users','admin_sessions','admin_login_attempts') ORDER BY tablename;"
```

Expected: 输出三行 `admin_login_attempts` / `admin_sessions` / `admin_users`

再验证约束确实生效：

```bash
psql "$DATABASE_URL" -c "INSERT INTO admin_users (id, username, password_hash) VALUES (1, 'ab', 'x');"
```

Expected: 报 `new row for relation "admin_users" violates check constraint "admin_users_username_format"`

- [ ] **Step 5: 验证 schema.sql 仍然可重复执行**

Run: `psql "$DATABASE_URL" -f schema.sql`
Expected: 没有报错（全部 `IF NOT EXISTS` / `OR REPLACE` / `DROP ... IF EXISTS`）

- [ ] **Step 6: 提交**

```bash
git add server/migrations/006_admin_auth.sql server/migrations/006_rollback.sql server/schema.sql
git commit -m "feat(server): 管理端账号、会话、登录失败计数三张表"
```

---

### Task 3: 登录接口与鉴权依赖

超管配置、登录、限流、`current_admin` / `current_super` 依赖一起做完：它们互相咬合，拆开任何一半都测不了。这一步做完鉴权**还没挂到业务接口上**，业务接口的行为不变。

**Files:**
- Create: `server/app/admin_auth.py`
- Modify: `server/app/models.py`（末尾追加）、`server/app/main.py`（include router + `FIELD_MESSAGES`）、`server/.env.example`、`server/.env`（本地，不进版本库）
- Test: `server/tests/test_admin_auth.py`

**Interfaces:**
- Consumes: Task 1 的 `hash_password` / `verify_password` / `PASSWORD_MIN_LEN` / `PASSWORD_MAX_LEN`；Task 2 的三张表；既有的 `app.db.pool`、`app.snowflake.next_id`
- Produces:
  - `app.admin_auth.router`（prefix `/api/admin/auth`）
  - `app.admin_auth.current_admin(authorization: str) -> dict`，返回 `{"token", "id", "username", "displayName", "isSuper"}`，其中 `id` 对超管是 `None`
  - `app.admin_auth.current_super(admin: dict) -> dict`
  - `app.admin_auth.SUPER_USERNAME: str`、`SUPER_PASSWORD: str`、`TOKEN_TTL: timedelta`
  - `app.models.AdminLoginIn`（`username`, `password`）、`AdminPasswordChangeIn`（`old_password`, `new_password`）、`AdminAccountIn`（`username`, `display_name`, `password`）、`AdminPasswordResetIn`（`password`）、`AdminStatusIn`（`status`）

- [ ] **Step 1: 配置项先落地**

在 `server/.env.example` 的微信配置那一节**之后**追加：

```
# 管理后台的超级管理员。这一个账号定义在配置里、不入库：
# 它是后台唯一的初始钥匙，其余管理员账号由它登录后在「账号管理」页创建。
# 两项缺一服务直接启动失败——不给默认值，否则某次部署忘了配就会让后台裸奔。
#
# 改密码 = 改这里 + 重启服务。超管在系统内改不了自己的密码。
ADMIN_SUPER_USERNAME=root
ADMIN_SUPER_PASSWORD=
```

在本地的 `server/.env` 里也加上同样两项，密码随便填一个 8 位以上的值（测试要用）：

```
ADMIN_SUPER_USERNAME=root
ADMIN_SUPER_PASSWORD=dev-super-pass
```

- [ ] **Step 2: 写失败的测试**

创建 `server/tests/test_admin_auth.py`：

```python
"""管理端登录与鉴权。需要库可连，跑完清掉自己造的数据。

造的管理员账号统一用 test. 前缀，清理时按前缀删——库里可能有真实账号，
不能用 TRUNCATE。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.admin_auth import SUPER_PASSWORD, SUPER_USERNAME
from app.db import pool
from app.main import app

TEST_PREFIX = "test."


async def clean() -> None:
    async with pool.connection() as conn:
        # 会话随外键级联删除，不用单独清
        await conn.execute("DELETE FROM admin_users WHERE username LIKE %s", (f"{TEST_PREFIX}%",))
        await conn.execute(
            "DELETE FROM admin_login_attempts WHERE username LIKE %s", (f"{TEST_PREFIX}%",)
        )
        # 超管那条也清掉：有测试会故意用错密码登超管，留下的计数会累加，
        # 攒够 5 次之后**别的**测试就会莫名其妙吃 429
        await conn.execute(
            "DELETE FROM admin_login_attempts WHERE username = %s", (SUPER_USERNAME,)
        )


@pytest.fixture
async def client():
    await clean()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await clean()


async def login(client, username: str, password: str):
    return await client.post(
        "/api/admin/auth/login", json={"username": username, "password": password}
    )


async def super_headers(client) -> dict:
    r = await login(client, SUPER_USERNAME, SUPER_PASSWORD)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------------------------------------------------------------------
# 登录


async def test_super_admin_logs_in_with_configured_credentials(client):
    r = await login(client, SUPER_USERNAME, SUPER_PASSWORD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["token"]
    assert body["user"]["isSuper"] is True
    assert body["user"]["username"] == SUPER_USERNAME
    assert body["expiresAt"]


async def test_wrong_password_gives_the_same_message_as_unknown_user(client):
    wrong = await login(client, SUPER_USERNAME, "definitely-not-it")
    unknown = await login(client, "test.nobody", "definitely-not-it")
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    # 两种情况文案必须一模一样，否则等于告诉爆破的人哪些用户名存在
    assert wrong.json()["message"] == unknown.json()["message"]


async def test_repeated_failures_lock_the_username(client):
    victim = "test.locktarget"
    for _ in range(5):
        r = await login(client, victim, "nope")
        assert r.status_code == 401, r.text
    r = await login(client, victim, "nope")
    assert r.status_code == 429, r.text
    assert "次数过多" in r.json()["message"]


async def test_successful_login_clears_the_failure_count(client):
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO admin_login_attempts (username, fail_count, locked_until)
            VALUES (%s, 3, NULL)
            ON CONFLICT (username) DO UPDATE SET fail_count = 3, locked_until = NULL
            """,
            (SUPER_USERNAME,),
        )
    r = await login(client, SUPER_USERNAME, SUPER_PASSWORD)
    assert r.status_code == 200, r.text

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT fail_count FROM admin_login_attempts WHERE username = %s",
                (SUPER_USERNAME,),
            )
        ).fetchone()
    assert row is None or row["fail_count"] == 0


# ---------------------------------------------------------------------------
# 会话


async def test_me_returns_the_logged_in_admin(client):
    headers = await super_headers(client)
    r = await client.get("/api/admin/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["isSuper"] is True


async def test_missing_or_bad_token_is_401(client):
    assert (await client.get("/api/admin/auth/me")).status_code == 401
    r = await client.get("/api/admin/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    # 不带 Bearer 前缀的也不认
    headers = await super_headers(client)
    raw = headers["Authorization"].removeprefix("Bearer ")
    assert (await client.get("/api/admin/auth/me", headers={"Authorization": raw})).status_code == 401


async def test_logout_kills_the_token(client):
    headers = await super_headers(client)
    assert (await client.post("/api/admin/auth/logout", headers=headers)).status_code == 200
    assert (await client.get("/api/admin/auth/me", headers=headers)).status_code == 401


async def test_expired_session_is_401(client):
    headers = await super_headers(client)
    token = headers["Authorization"].removeprefix("Bearer ")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE admin_sessions SET expires_at = now() - interval '1 minute' WHERE token = %s",
            (token,),
        )
    assert (await client.get("/api/admin/auth/me", headers=headers)).status_code == 401


# ---------------------------------------------------------------------------
# 改自己的密码


async def test_super_admin_cannot_change_own_password_here(client):
    headers = await super_headers(client)
    r = await client.put(
        "/api/admin/auth/password",
        headers=headers,
        json={"oldPassword": SUPER_PASSWORD, "newPassword": "brand-new-pass"},
    )
    # 超管密码在配置文件里，接口改不了
    assert r.status_code == 400, r.text
    assert "配置" in r.json()["message"]
```

- [ ] **Step 3: 跑测试确认它失败**

Run: `cd server && uv run pytest tests/test_admin_auth.py -v`
Expected: 收集阶段就 FAIL — `ModuleNotFoundError: No module named 'app.admin_auth'`

- [ ] **Step 4: 补 pydantic 模型**

在 `server/app/models.py` **末尾**追加：

```python
# ---------------------------------------------------------------------------
# 管理端账号
#
# 注意这一节的模型服务的是**后台管理员**，与上面 users 表那套（小程序用户）
# 完全是两回事，不要混用。

# 与 schema.sql 的 admin_users.username CHECK 逐字一致
ADMIN_USERNAME_PATTERN = r"^[a-zA-Z0-9_.-]{3,32}$"

AdminUsername = Annotated[Trimmed, Field(pattern=ADMIN_USERNAME_PATTERN)]
# 长度上下限见 app/security.py，那里解释了为什么不强制字符组合
AdminPassword = Annotated[str, Field(min_length=8, max_length=64)]


class AdminLoginIn(BaseModel):
    """登录。

    这里**不**按 ADMIN_USERNAME_PATTERN 卡用户名：格式不对的用户名本来就登不上，
    提前回一个「格式不正确」等于告诉爆破的人这批候选不用试。一律走到密码比对，
    回同一句「用户名或密码不正确」。
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: Annotated[Trimmed, Field(min_length=1, max_length=64)]
    password: Annotated[str, Field(min_length=1, max_length=200)]


class AdminPasswordChangeIn(BaseModel):
    """改自己的密码。旧密码必填，防的是有人借着没锁屏的电脑改密码顶掉本人。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    old_password: Annotated[str, Field(min_length=1, max_length=200)]
    new_password: AdminPassword


class AdminAccountIn(BaseModel):
    """超管建号。没有注册入口，账号只能从这里来。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    username: AdminUsername
    display_name: Annotated[Trimmed, Field(max_length=40)] = ""
    password: AdminPassword


class AdminPasswordResetIn(BaseModel):
    """超管重置别人的密码。不要旧密码——超管本来就不知道对方的密码。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    password: AdminPassword


class AdminStatusIn(BaseModel):
    """启用 / 停用。取值与 schema.sql 的 admin_users.status CHECK 逐字一致。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    status: Literal["active", "disabled"]
```

- [ ] **Step 5: 写 admin_auth.py**

创建 `server/app/admin_auth.py`：

```python
"""管理端登录与鉴权。

超级管理员**不入库**，由配置文件的 ADMIN_SUPER_USERNAME / ADMIN_SUPER_PASSWORD
定义。由此推出两件事：

1. admin_users 表里每一行都是普通管理员，所以那张表不需要 role 列。
   「是不是超管」这个信息只存在于会话行的 is_super 上。
2. 超管改密码 = 改配置 + 重启服务，接口改不了（见 change_password）。
   代价是运维要动配置，好处是超管既停用不了也删不掉，后台锁不死在门外。

登录链路：POST /api/admin/auth/login → 比对配置或库里的哈希 → 建一条
admin_sessions → 前端带 Authorization: Bearer <token> 调后续接口。
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, status

# 这一行同时把仓库根的 .env 读进环境变量（db.py 在导入时 load_dotenv），
# 所以下面的 os.getenv 一定拿得到值。别把这个 import 挪到 getenv 后面
from .db import pool
from .models import AdminLoginIn, AdminPasswordChangeIn
from .security import verify_password

logger = logging.getLogger(__name__)

SUPER_USERNAME = os.getenv("ADMIN_SUPER_USERNAME", "").strip()
SUPER_PASSWORD = os.getenv("ADMIN_SUPER_PASSWORD", "")
if not SUPER_USERNAME or not SUPER_PASSWORD:
    # 不给默认值：默认账号会让某次忘了配的部署把后台悄悄开成公开的，
    # 而后台出的是全量客户手机号。宁可起不来
    raise RuntimeError(
        "缺少 ADMIN_SUPER_USERNAME 或 ADMIN_SUPER_PASSWORD："
        "把 .env.example 里管理后台那两项复制到 .env 并填上"
    )

# 登录态有效期。比小程序那边的 30 天短得多：后台一屏就是全量客户手机号，
# 一台没锁屏的电脑不该一个月都是登录状态
TOKEN_TTL = timedelta(hours=12)

# 连错几次锁多久。锁的是用户名不是 IP：超管账号只有一个，锁它才拦得住爆破，
# 锁 IP 会让同一个办公室的运营互相牵连
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 鉴权依赖


async def current_admin(authorization: str = Header(default="")) -> dict:
    """从 Authorization 头解出当前管理员。

    挂在 admin_router 上（见 app/admin.py），一次覆盖全部后台接口，
    包括将来新加的——逐个接口去挂迟早会漏一个。
    """
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT s.admin_id, s.is_super, s.expires_at,
                       a.username, a.display_name, a.status
                  FROM admin_sessions s
                  LEFT JOIN admin_users a ON a.id = s.admin_id
                 WHERE s.token = %s
                   AND s.expires_at > now()
                """,
                (token,),
            )
        ).fetchone()

        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")

        # 停用的账号即便手上还有没过期的 token 也进不来。
        # 停用接口会顺手删它的会话，这里再查一次是兜底：将来若有别处改了状态
        # 却忘了删会话，鉴权层仍然挡得住
        if not row["is_super"] and row["status"] != "active":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被停用，请联系超级管理员")

        # 滑动续期：剩余不足一半就续满。固定 12 小时会让运营正干到一半被踢出去，
        # 而一直在用的会话本来就说明人在电脑前
        if row["expires_at"] - _now() < TOKEN_TTL / 2:
            await conn.execute(
                "UPDATE admin_sessions SET expires_at = %s WHERE token = %s",
                (_now() + TOKEN_TTL, token),
            )

    return {
        "token": token,
        # 超管不入库，没有 id。所有拿这个字段去查 admin_users 的地方都要先判 isSuper
        "id": row["admin_id"],
        "username": SUPER_USERNAME if row["is_super"] else row["username"],
        "displayName": "超级管理员" if row["is_super"] else row["display_name"],
        "isSuper": row["is_super"],
    }


async def current_super(admin: dict = Depends(current_admin)) -> dict:
    """只有超管能过。挂在 admin_accounts 那个 router 上。"""
    if not admin["isSuper"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "没有权限")
    return admin


# ---------------------------------------------------------------------------
# 登录失败计数


async def _attempts(conn, username: str) -> dict | None:
    return await (
        await conn.execute(
            "SELECT fail_count, locked_until FROM admin_login_attempts WHERE username = %s",
            (username,),
        )
    ).fetchone()


async def _record_failure(conn, username: str, prior: dict | None) -> None:
    """失败计数 +1，到阈值就锁一段时间。

    锁过期后从 1 重新数，不接着上次往上加：一个人今天错 4 次、下周再错 1 次
    就被锁，那不是爆破的特征，只是记性不好。
    """
    expired = (
        prior is not None
        and prior["locked_until"] is not None
        and prior["locked_until"] <= _now()
    )
    count = 1 if (prior is None or expired) else prior["fail_count"] + 1
    locked_until = _now() + LOCKOUT if count >= MAX_FAILURES else None

    await conn.execute(
        """
        INSERT INTO admin_login_attempts (username, fail_count, locked_until)
        VALUES (%s, %s, %s)
        ON CONFLICT (username) DO UPDATE SET
          fail_count   = EXCLUDED.fail_count,
          locked_until = EXCLUDED.locked_until
        """,
        (username, count, locked_until),
    )


async def _clear_failures(conn, username: str) -> None:
    await conn.execute("DELETE FROM admin_login_attempts WHERE username = %s", (username,))


# ---------------------------------------------------------------------------
# 接口


@router.post("/login")
async def login(body: AdminLoginIn) -> dict:
    username = body.username

    try:
        async with pool.connection() as conn:
            prior = await _attempts(conn, username)
            if prior and prior["locked_until"] and prior["locked_until"] > _now():
                minutes = max(1, round((prior["locked_until"] - _now()).total_seconds() / 60))
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    f"密码错误次数过多，请 {minutes} 分钟后再试",
                )

            admin_id: int | None = None
            is_super = username == SUPER_USERNAME
            if is_super:
                # 配置里的密码是明文，常量时间比对，别用 ==
                ok = secrets.compare_digest(body.password, SUPER_PASSWORD)
            else:
                row = await (
                    await conn.execute(
                        "SELECT id, password_hash, status FROM admin_users WHERE username = %s",
                        (username,),
                    )
                ).fetchone()
                ok = row is not None and verify_password(body.password, row["password_hash"])
                if ok and row["status"] != "active":
                    # 密码是对的，只是号被停了。这条不算失败计数——
                    # 本人反复试自己的密码不是爆破
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN, "账号已被停用，请联系超级管理员"
                    )
                if ok:
                    admin_id = row["id"]

            if not ok:
                await _record_failure(conn, username, prior)
                logger.warning("后台登录失败 username=%s", username)
                # 「用户不存在」和「密码错误」回同一句：区分开等于给对方做用户名枚举
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码不正确")

            await _clear_failures(conn, username)

            token = secrets.token_urlsafe(32)
            expires_at = _now() + TOKEN_TTL
            await conn.execute(
                """
                INSERT INTO admin_sessions (token, admin_id, is_super, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (token, admin_id, is_super, expires_at),
            )

            display_name = "超级管理员"
            if not is_super:
                updated = await (
                    await conn.execute(
                        """
                        UPDATE admin_users
                           SET last_login_at = now(), login_count = login_count + 1
                         WHERE id = %s
                        RETURNING display_name
                        """,
                        (admin_id,),
                    )
                ).fetchone()
                display_name = updated["display_name"]
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("后台登录出错 username=%s", username)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "登录失败，请稍后再试"
        ) from None

    logger.info("后台登录成功 username=%s is_super=%s", username, is_super)
    return {
        "ok": True,
        "token": token,
        "expiresAt": expires_at.isoformat(),
        "user": {"username": username, "displayName": display_name, "isSuper": is_super},
    }


@router.post("/logout")
async def logout(admin: dict = Depends(current_admin)) -> dict:
    """只删当前这一条会话，不动这个人在别的电脑上的登录态。"""
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM admin_sessions WHERE token = %s", (admin["token"],))
    return {"ok": True}


@router.get("/me")
async def read_me(admin: dict = Depends(current_admin)) -> dict:
    """前端启动时用它验一次本地缓存的 token 还作不作数。"""
    return {
        "ok": True,
        "user": {
            "username": admin["username"],
            "displayName": admin["displayName"],
            "isSuper": admin["isSuper"],
        },
    }


@router.put("/password")
async def change_password(
    body: AdminPasswordChangeIn, admin: dict = Depends(current_admin)
) -> dict:
    """改自己的密码。改完当前这条会话留着，这个人在别处的会话全部作废。"""
    if admin["isSuper"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "超级管理员的密码在服务器配置文件里维护，改完需要重启服务",
        )

    # 循环导入：admin_accounts 会 import 本模块的依赖，本模块只在这里用一次
    # hash_password，直接从 security 拿即可，不必绕 admin_accounts
    from .security import hash_password

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT password_hash FROM admin_users WHERE id = %s", (admin["id"],)
            )
        ).fetchone()
        if row is None or not verify_password(body.old_password, row["password_hash"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前密码不正确")

        await conn.execute(
            "UPDATE admin_users SET password_hash = %s WHERE id = %s",
            (hash_password(body.new_password), admin["id"]),
        )
        # 密码换了，别处的登录态就该失效——不然改密码防不住已经登进去的人
        await conn.execute(
            "DELETE FROM admin_sessions WHERE admin_id = %s AND token <> %s",
            (admin["id"], admin["token"]),
        )

    logger.info("后台管理员改密码 username=%s", admin["username"])
    return {"ok": True}
```

- [ ] **Step 6: 在 main.py 里挂上 router 并补文案**

`server/app/main.py` 的 import 区加：

```python
from .admin_auth import router as admin_auth_router
```

`app.include_router(users_router)` 那一组的**前面**加（登录接口不需要鉴权，单独一个 router）：

```python
# 登录接口自己不能要求登录，所以它是独立的 router，不带 admin_router 上那个鉴权依赖
app.include_router(admin_auth_router)
```

`FIELD_MESSAGES` 字典里追加：

```python
    # 管理后台登录
    "username": "用户名要 3-32 位，只能用字母、数字和 _ . -",
    "password": "密码要 8 到 64 位",
    "oldPassword": "请输入当前密码",
    "newPassword": "新密码要 8 到 64 位",
    "displayName": "姓名过长",
```

`allow_methods` 现在是 `["GET", "POST", "PUT", "OPTIONS"]`，账号管理要用 DELETE，改成：

```python
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
```

- [ ] **Step 7: 跑测试确认通过**

Run: `cd server && uv run pytest tests/test_admin_auth.py -v`
Expected: 9 passed

- [ ] **Step 8: 跑全量测试确认没弄坏别的**

Run: `cd server && uv run pytest -q`
Expected: 全绿（此时鉴权还没挂到业务接口上，既有测试不受影响）

- [ ] **Step 9: 提交**

```bash
git add server/app/admin_auth.py server/app/models.py server/app/main.py \
        server/.env.example server/tests/test_admin_auth.py
git commit -m "feat(server): 管理端登录接口与鉴权依赖"
```

---

### Task 4: 把鉴权挂到全部后台接口上

这一步是真正「后台不再裸奔」的那一刻，同时必然弄红既有测试，两件事一起做完才算一个完整交付。

**Files:**
- Modify: `server/app/admin.py:1-11`（模块注释）、`server/app/admin.py:36`（router）
- Modify: `server/tests/conftest.py`、`server/tests/test_admin.py:57-64`、`server/tests/test_home_media.py`
- Modify: `server/README.md`

**Interfaces:**
- Consumes: Task 3 的 `current_admin`
- Produces: `server/tests/conftest.py` 里的 `auth_headers` fixture（`dict`，形如 `{"Authorization": "Bearer ..."}`），后续任务的测试直接用

- [ ] **Step 1: 写失败的测试**

在 `server/tests/test_admin_auth.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 鉴权真的覆盖了业务接口


@pytest.mark.parametrize(
    "path",
    ["/api/admin/appointments", "/api/admin/service-applications", "/api/admin/home-media"],
)
async def test_business_endpoints_require_login(client, path):
    # 这三条出的是全量客户资料和首页配置，没有 token 一条都不能给
    assert (await client.get(path)).status_code == 401


async def test_business_endpoints_work_with_a_token(client):
    headers = await super_headers(client)
    r = await client.get("/api/admin/appointments", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd server && uv run pytest tests/test_admin_auth.py -k require_login -v`
Expected: FAIL — 三条都返回 200，因为鉴权还没挂上

- [ ] **Step 3: 挂鉴权**

`server/app/admin.py` 第 36 行改成：

```python
# 鉴权挂在 router 上而不是逐个接口挂：这样将来在这个文件里加接口，
# 不需要记得加依赖也一样是受保护的。登录接口在 app/admin_auth.py，
# 那是独立的 router，不受这一行影响
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_admin)])
```

同文件 import 区补上（`from fastapi import ...` 那一行加 `Depends`）：

```python
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import cos, snowflake
from .admin_auth import current_admin
```

把模块注释开头第 6-10 行那段改掉——原文说「将来给这个 router 挂一个
`Depends(require_admin)`」和「⚠️ 当前没有鉴权」，现在已经做了：

```python
2. 鉴权好加。这个 router 挂了 `dependencies=[Depends(current_admin)]`，一行覆盖
   全部后台接口，不用逐个接口去改，新加的接口也不会漏掉。

登录与账号管理见 app/admin_auth.py 和 app/admin_accounts.py。
```

（删掉原第 10 行那句「⚠️ 当前**没有鉴权**，只能在内网或本机开发时用」。）

- [ ] **Step 4: 跑测试确认新测试通过、老测试变红**

Run: `cd server && uv run pytest -q`
Expected: `tests/test_admin_auth.py` 全绿；`tests/test_admin.py` 和
`tests/test_home_media.py` 大量 401 失败——这是预期的，下一步修

- [ ] **Step 5: 给既有测试补登录态**

在 `server/tests/conftest.py` 末尾追加：

```python
@pytest.fixture(scope="session")
async def auth_headers(db) -> dict:
    """后台接口现在全部要鉴权，各测试文件的 client 统一带上这个头。

    用配置里的超管登录一次，整个会话共用一条 token——每个测试各登一次会白白
    多跑几十次 scrypt，而 scrypt 是故意做得慢的。
    """
    from httpx import ASGITransport, AsyncClient

    from app.admin_auth import SUPER_PASSWORD, SUPER_USERNAME
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/admin/auth/login",
            json={"username": SUPER_USERNAME, "password": SUPER_PASSWORD},
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}
```

`server/tests/test_admin.py` 的 `client` fixture（第 57-64 行）改成：

```python
@pytest.fixture
async def client(auth_headers):
    await clean()
    transport = ASGITransport(app=app)
    # 头挂在 client 上，所有请求都带。小程序那几个公开接口也会带上这个头，
    # 但它们查的是 users.token，对不上就当没登录，行为不变
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    ) as c:
        yield c
    await clean()
```

`server/tests/test_home_media.py` 里的 client fixture 照同样方式改：给 fixture 加
`auth_headers` 参数，`AsyncClient(...)` 加 `headers=auth_headers`。

- [ ] **Step 6: 跑全量测试确认全绿**

Run: `cd server && uv run pytest -q`
Expected: 全部 passed

- [ ] **Step 7: 更新 README**

`server/README.md` 里「上线前要做的」中关于「后台接口无鉴权」的那一条改为已完成，
并补上运维说明。措辞：

```markdown
- ~~后台接口 `/api/admin/*` 没有鉴权~~ 已完成：需要登录才能访问。
  超级管理员由 `.env` 的 `ADMIN_SUPER_USERNAME` / `ADMIN_SUPER_PASSWORD` 指定，
  **上线前务必换成强密码**。其余管理员账号由超管登录后台后在「账号管理」页创建，
  系统没有注册入口。

  超管的密码在配置里，系统内改不了——改密码 = 改 `.env` + 重启服务。
  换来的是超管既停用不了也删不掉，后台不会被锁死在门外。
- CORS 现在是 `allow_origins=["*"]`。登录态走 `Authorization` 头不走 cookie，
  `*` 不构成漏洞，但上线时仍应收窄到后台的具体域名。
```

- [ ] **Step 8: 提交**

```bash
git add server/app/admin.py server/tests/conftest.py server/tests/test_admin.py \
        server/tests/test_home_media.py server/tests/test_admin_auth.py server/README.md
git commit -m "feat(server): 后台接口全部要求登录"
```

---

### Task 5: 账号管理接口

**Files:**
- Create: `server/app/admin_accounts.py`
- Modify: `server/app/main.py`（include router）
- Test: `server/tests/test_admin_auth.py`（追加一节）

**Interfaces:**
- Consumes: Task 3 的 `current_super`、`SUPER_USERNAME`；Task 1 的 `hash_password`；`app.snowflake.next_id`
- Produces: `app.admin_accounts.router`（prefix `/api/admin/accounts`）

- [ ] **Step 1: 写失败的测试**

在 `server/tests/test_admin_auth.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 账号管理


async def make_account(client, headers, username="test.alice", password="alice-pass-1"):
    r = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": username, "displayName": "爱丽丝", "password": password},
    )
    assert r.status_code == 201, r.text
    return r.json()["account"]


async def test_super_creates_an_account_that_can_log_in(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    assert account["username"] == "test.alice"
    assert account["status"] == "active"
    # 雪花 ID 超出 JS 安全整数范围，出接口必须是字符串
    assert isinstance(account["id"], str)
    # 哈希一个字节都不能出接口
    assert "password" not in account and "passwordHash" not in account

    r = await login(client, "test.alice", "alice-pass-1")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["isSuper"] is False


async def test_normal_admin_cannot_touch_account_management(client):
    headers = await super_headers(client)
    await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}

    assert (await client.get("/api/admin/accounts", headers=alice)).status_code == 403
    r = await client.post(
        "/api/admin/accounts",
        headers=alice,
        json={"username": "test.bob", "displayName": "", "password": "bob-pass-11"},
    )
    assert r.status_code == 403
    # 但业务接口她是能看的
    assert (await client.get("/api/admin/appointments", headers=alice)).status_code == 200


async def test_normal_admin_changes_own_password(client):
    headers = await super_headers(client)
    await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}

    bad = await client.put(
        "/api/admin/auth/password",
        headers=alice,
        json={"oldPassword": "wrong-one-11", "newPassword": "alice-pass-2"},
    )
    assert bad.status_code == 400, bad.text

    ok = await client.put(
        "/api/admin/auth/password",
        headers=alice,
        json={"oldPassword": "alice-pass-1", "newPassword": "alice-pass-2"},
    )
    assert ok.status_code == 200, ok.text

    assert (await login(client, "test.alice", "alice-pass-1")).status_code == 401
    assert (await login(client, "test.alice", "alice-pass-2")).status_code == 200
    # 改密码的那条会话自己留着，不用重登
    assert (await client.get("/api/admin/auth/me", headers=alice)).status_code == 200


async def test_reserved_and_duplicate_usernames_are_refused(client):
    headers = await super_headers(client)
    reserved = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": SUPER_USERNAME, "displayName": "", "password": "whatever-11"},
    )
    # 建一个和超管同名的普通账号，登录时永远被超管判定抢先命中，是个点不动的鬼影
    assert reserved.status_code == 400, reserved.text

    await make_account(client, headers)
    dup = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": "test.alice", "displayName": "", "password": "another-11"},
    )
    assert dup.status_code == 409, dup.text


async def test_bad_username_or_short_password_is_rejected(client):
    headers = await super_headers(client)
    short = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": "test.bob", "displayName": "", "password": "short"},
    )
    assert short.status_code == 400
    bad_name = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": "有中文", "displayName": "", "password": "good-pass-11"},
    )
    assert bad_name.status_code == 400


async def test_disabling_an_account_kills_its_session_immediately(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await client.get("/api/admin/appointments", headers=alice)).status_code == 200

    r = await client.put(
        f"/api/admin/accounts/{account['id']}/status",
        headers=headers,
        json={"status": "disabled"},
    )
    assert r.status_code == 200, r.text

    # 手上那条 token 立刻不作数
    assert (await client.get("/api/admin/appointments", headers=alice)).status_code == 401
    # 也登不回来
    assert (await login(client, "test.alice", "alice-pass-1")).status_code == 403


async def test_reset_password_logs_the_account_out_everywhere(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}

    r = await client.put(
        f"/api/admin/accounts/{account['id']}/password",
        headers=headers,
        json={"password": "reset-pass-11"},
    )
    assert r.status_code == 200, r.text

    assert (await client.get("/api/admin/auth/me", headers=alice)).status_code == 401
    assert (await login(client, "test.alice", "reset-pass-11")).status_code == 200


async def test_deleting_an_account_cascades_its_sessions(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}

    r = await client.delete(f"/api/admin/accounts/{account['id']}", headers=headers)
    assert r.status_code == 200, r.text

    assert (await client.get("/api/admin/auth/me", headers=alice)).status_code == 401
    assert (await login(client, "test.alice", "alice-pass-1")).status_code == 401


async def test_operations_on_a_missing_account_are_404(client):
    headers = await super_headers(client)
    missing = "1234567890123456789"
    assert (await client.delete(f"/api/admin/accounts/{missing}", headers=headers)).status_code == 404
    r = await client.put(
        f"/api/admin/accounts/{missing}/status", headers=headers, json={"status": "disabled"}
    )
    assert r.status_code == 404


async def test_account_list_shows_created_accounts(client):
    headers = await super_headers(client)
    await make_account(client, headers)
    r = await client.get("/api/admin/accounts", headers=headers)
    assert r.status_code == 200, r.text
    names = [a["username"] for a in r.json()["items"]]
    assert "test.alice" in names
```

- [ ] **Step 2: 跑测试确认它失败**

Run: `cd server && uv run pytest tests/test_admin_auth.py -k account -v`
Expected: FAIL — 建号返回 405/404，`/api/admin/accounts` 还不存在

- [ ] **Step 3: 写实现**

创建 `server/app/admin_accounts.py`：

```python
"""管理员账号管理。只有超级管理员能进。

没有注册接口：账号只能由超管在这里创建。这是需求定死的——后台出的是全量客户
资料，不能让任何人自助开号。

超管自己不在 admin_users 里（见 app/admin_auth.py），所以这里的每个接口都不必
判断「会不会误删自己」——超管压根不在这张表的查询结果里。
"""

import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status

from .admin_auth import SUPER_USERNAME, current_super
from .db import pool
from .models import AdminAccountIn, AdminPasswordResetIn, AdminStatusIn
from .security import hash_password
from .snowflake import next_id

logger = logging.getLogger(__name__)

# 依赖挂在 router 上，这个文件里将来加接口也自动只有超管能进
router = APIRouter(
    prefix="/api/admin/accounts",
    tags=["admin-accounts"],
    dependencies=[Depends(current_super)],
)

# password_hash 一个字节都不出接口
ACCOUNT_COLUMNS = "id, username, display_name, status, last_login_at, login_count, created_at"


def to_json(row: dict) -> dict:
    return {
        # 雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，出接口一律转字符串
        "id": str(row["id"]),
        "username": row["username"],
        "displayName": row["display_name"],
        "status": row["status"],
        "lastLoginAt": row["last_login_at"].isoformat() if row["last_login_at"] else None,
        "loginCount": row["login_count"],
        "createdAt": row["created_at"].isoformat(),
    }


@router.get("")
async def list_accounts() -> dict:
    """全部管理员账号。

    不分页：管理员是个位数到几十的量级，一页出得完；加了分页反而让前端多一套
    翻页状态要维护。真到几百个账号那天再说。
    """
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                f"SELECT {ACCOUNT_COLUMNS} FROM admin_users ORDER BY created_at DESC, id DESC"
            )
        ).fetchall()
    return {"ok": True, "items": [to_json(row) for row in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_account(body: AdminAccountIn) -> dict:
    if body.username == SUPER_USERNAME:
        # 同名的普通账号登录时永远被超管判定抢先命中，建出来就是个点不动的鬼影
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "这个用户名被保留，换一个")

    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    INSERT INTO admin_users (id, username, display_name, password_hash)
                    VALUES (%s, %s, %s, %s)
                    RETURNING {ACCOUNT_COLUMNS}
                    """,
                    (next_id(), body.username, body.display_name, hash_password(body.password)),
                )
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个用户名已被占用") from None
    except psycopg.Error:
        logger.exception("创建管理员账号失败 username=%s", body.username)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "创建失败，请稍后再试"
        ) from None

    logger.info("新建管理员账号 username=%s", body.username)
    return {"ok": True, "account": to_json(row)}


@router.put("/{account_id}/password")
async def reset_password(account_id: int, body: AdminPasswordResetIn) -> dict:
    """超管重置某人的密码。改完把这个人所有会话清掉，让他必须用新密码重登。"""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "UPDATE admin_users SET password_hash = %s WHERE id = %s RETURNING username",
                (hash_password(body.password), account_id),
            )
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
        await conn.execute("DELETE FROM admin_sessions WHERE admin_id = %s", (account_id,))

    logger.info("重置管理员密码 username=%s", row["username"])
    return {"ok": True}


@router.put("/{account_id}/status")
async def set_status(account_id: int, body: AdminStatusIn) -> dict:
    """启用 / 停用。

    停用时顺手清掉会话，那个人手上没过期的 token 立刻作废——只改状态的话，
    要等鉴权层下一次查到 status 才拦得住，中间那一瞬他还在后台里翻客户手机号。
    """
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                f"UPDATE admin_users SET status = %s WHERE id = %s RETURNING {ACCOUNT_COLUMNS}",
                (body.status, account_id),
            )
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
        if body.status == "disabled":
            await conn.execute("DELETE FROM admin_sessions WHERE admin_id = %s", (account_id,))

    logger.info("管理员账号状态变更 username=%s status=%s", row["username"], body.status)
    return {"ok": True, "account": to_json(row)}


@router.delete("/{account_id}")
async def delete_account(account_id: int) -> dict:
    """删号。会话随 admin_sessions 的外键级联删除，这里不用管。"""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "DELETE FROM admin_users WHERE id = %s RETURNING username", (account_id,)
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")

    logger.info("删除管理员账号 username=%s", row["username"])
    return {"ok": True}
```

- [ ] **Step 4: 挂 router**

`server/app/main.py` 的 import 区加：

```python
from .admin_accounts import router as admin_accounts_router
```

在 `app.include_router(admin_auth_router)` 之后加：

```python
app.include_router(admin_accounts_router)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd server && uv run pytest tests/test_admin_auth.py -v`
Expected: 全部 passed

- [ ] **Step 6: 跑全量测试**

Run: `cd server && uv run pytest -q`
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add server/app/admin_accounts.py server/app/main.py server/tests/test_admin_auth.py
git commit -m "feat(server): 超管的账号管理接口"
```

---

### Task 6: 前端请求出入口收敛

把 token 注入和 401 处理收到一个文件里。先做这一步，后面所有前端任务都直接用它。

**Files:**
- Create: `website/src/lib/api.ts`
- Modify: `website/src/features/antony/api.ts`

**Interfaces:**
- Consumes: Task 3/5 的接口约定
- Produces（`@/lib/api`）：
  - `call<T>(path: string, init?: RequestInit): Promise<T>`
  - `get<T>(path: string, params: Record<string, string | number>): Promise<T>`
  - `readToken(): string` / `writeToken(token: string): void` / `clearToken(): void`
  - `setUnauthorizedHandler(fn: () => void): void`

- [ ] **Step 1: 写 lib/api.ts**

创建 `website/src/lib/api.ts`：

```ts
// 后台请求的统一出入口。
//
// 登录态注入和 401 处理只在这里写一遍：散到各业务页去判断，早晚有一个页面
// 漏掉，表现是「明明已经掉线了，那个页面还在转圈」。

import type { PagedResult } from '@/features/antony/types'

const BASE = '/api/admin'
const TOKEN_KEY = 'antony-admin-token'

export const readToken = (): string => localStorage.getItem(TOKEN_KEY) ?? ''
export const writeToken = (token: string): void => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = (): void => localStorage.removeItem(TOKEN_KEY)

// 401 时通知外层把人送回登录页。由 session-context 在挂载时注册，
// 这里留一个空实现，免得注册之前发的请求炸掉
let onUnauthorized: () => void = () => {}
export const setUnauthorizedHandler = (fn: () => void): void => {
  onUnauthorized = fn
}

/**
 * 服务端约定：成功是 {ok:true, ...}，失败是 {ok:false, message}，HTTP 状态码同时表达。
 * 这里不吞异常也不给默认值——查不出来就该在页面上报错，
 * 悄悄返回空列表会被当成「今天没人预约」。
 */
export async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = readToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, { ...init, headers })
  } catch (err) {
    throw new Error(`连不上后台服务：${err instanceof Error ? err.message : String(err)}`)
  }

  const body: unknown = await res.json().catch(() => null)

  if (res.status === 401) {
    // 登录态没了就地清掉并送回登录页。留着一个已经失效的 token 只会让
    // 下一个页面再撞一次 401
    clearToken()
    onUnauthorized()
  }

  if (!res.ok || !isOk(body)) {
    throw new Error(errorMessage(body) ?? `请求失败（HTTP ${res.status}）`)
  }
  return body as T
}

export async function get<T>(
  path: string,
  params: Record<string, string | number>,
): Promise<T> {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== '' && value !== null && value !== undefined) query.set(key, String(value))
  }
  return call<T>(`${path}?${query}`)
}

function isOk(body: unknown): boolean {
  return typeof body === 'object' && body !== null && (body as { ok?: unknown }).ok === true
}

function errorMessage(body: unknown): string | null {
  if (typeof body !== 'object' || body === null) return null
  const message = (body as { message?: unknown }).message
  return typeof message === 'string' ? message : null
}

// 分页接口的返回形状，业务模块复用
export type { PagedResult }
```

- [ ] **Step 2: 改 features/antony/api.ts 用它**

把 `website/src/features/antony/api.ts` 里的 `BASE`、`call`、`get`、`isOk`、
`errorMessage` **整段删掉**，换成从 `@/lib/api` 引入。改完文件应该是：

```ts
// 安东尼之家后台接口。服务端是仓库里的 server/（FastAPI）。
// 开发时 vite 把 /api/admin 代理过去，见 vite.config.ts。
//
// 请求本身（登录态、401 处理、错误约定）在 @/lib/api，这里只列有哪些接口。

import { call, get } from '@/lib/api'

import type {
  Appointment,
  AppointmentQuery,
  HomeMediaResult,
  HomeMediaUploadResult,
  HomeSlot,
  PagedResult,
  ServiceApplication,
  ServiceApplicationQuery,
} from './types'

export type PageParams = {
  page: number
  pageSize: number
}

export const listAppointments = (params: AppointmentQuery & PageParams) =>
  get<PagedResult<Appointment>>('/appointments', params)

export const listServiceApplications = (params: ServiceApplicationQuery & PageParams) =>
  get<PagedResult<ServiceApplication>>('/service-applications', params)

// ---------------------------------------------------------------------------
// 首页配图

export const getHomeMedia = () => call<HomeMediaResult>('/home-media')

/** 整组替换某个位置的图，数组顺序即首页上的展示顺序。传空数组是清空。 */
export const saveHomeMedia = (slot: HomeSlot, keys: string[]) =>
  call<{ ok: true; slot: HomeSlot; count: number }>(`/home-media/${slot}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keys }),
  })

/**
 * 传一张图，拿回 COS 对象键。
 *
 * 请求体是裸的文件字节，不是 FormData：服务端解析 multipart 要多装一个依赖，
 * 而这里一次只传一张图，裸 body 就够（服务端按文件头判类型，不看文件名）。
 * 只上传、不落库——运营点了「保存」才会写进配置。
 */
export const uploadHomeImage = (file: File) =>
  call<HomeMediaUploadResult>('/home-media/upload', {
    method: 'POST',
    headers: { 'Content-Type': 'application/octet-stream' },
    body: file,
  })
```

- [ ] **Step 3: 类型检查与 lint**

Run: `cd website && pnpm build && pnpm lint`
Expected: 都通过，无 TS 报错

- [ ] **Step 4: 提交**

```bash
git add website/src/lib/api.ts website/src/features/antony/api.ts
git commit -m "refactor(website): 后台请求收敛到 lib/api，统一带登录态"
```

---

### Task 7: 登录页与路由守卫

**Files:**
- Create: `website/src/features/auth/api.ts`、`session-context.tsx`、`require-auth.tsx`、`login-page.tsx`
- Modify: `website/src/main.tsx`

**Interfaces:**
- Consumes: `@/lib/api` 的 `call` / `readToken` / `writeToken` / `clearToken` / `setUnauthorizedHandler`
- Produces:
  - `AdminUser = { username: string; displayName: string; isSuper: boolean }`
  - `SessionProvider`（组件）、`useSession(): { user, ready, signIn, signOut }`
  - `RequireAuth`（组件，包子路由）

- [ ] **Step 1: 写接口层**

创建 `website/src/features/auth/api.ts`：

```ts
// 管理端登录相关接口。服务端见 server/app/admin_auth.py。

import { call } from '@/lib/api'

export type AdminUser = {
  username: string
  displayName: string
  /** 超管额外能进「账号管理」。服务端也拦，这个值只用来决定菜单显不显示 */
  isSuper: boolean
}

type LoginResult = { ok: true; token: string; expiresAt: string; user: AdminUser }

export const login = (username: string, password: string) =>
  call<LoginResult>('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })

export const logout = () => call<{ ok: true }>('/auth/logout', { method: 'POST' })

export const fetchMe = () => call<{ ok: true; user: AdminUser }>('/auth/me')

export const changeMyPassword = (oldPassword: string, newPassword: string) =>
  call<{ ok: true }>('/auth/password', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ oldPassword, newPassword }),
  })
```

- [ ] **Step 2: 写 session context**

创建 `website/src/features/auth/session-context.tsx`：

```tsx
// 当前登录态。
//
// 启动时若本地有 token，先调一次 /auth/me 验过再放行，不直接信任本地缓存的
// 用户信息——账号可能已经被超管停用或删掉了，拿着旧缓存进去只会每个接口撞一次
// 401，看着像后台坏了。

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'

import { clearToken, readToken, setUnauthorizedHandler, writeToken } from '@/lib/api'

import { fetchMe, login as loginRequest, logout as logoutRequest } from './api'
import type { AdminUser } from './api'

type Session = {
  user: AdminUser | null
  /** 首次 /auth/me 校验是否已经完成。没完成时不要下判断，否则会闪一下登录页 */
  ready: boolean
  signIn: (username: string, password: string) => Promise<void>
  signOut: () => Promise<void>
}

const SessionContext = createContext<Session | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AdminUser | null>(null)
  const [ready, setReady] = useState(false)

  // 任何一个请求撞了 401，都在这里把人打回未登录
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null))
  }, [])

  useEffect(() => {
    if (!readToken()) {
      setReady(true)
      return
    }
    let cancelled = false
    void fetchMe()
      .then((body) => {
        if (!cancelled) setUser(body.user)
      })
      // token 失效时 call() 已经清过 token 并通知过了，这里只需要别把异常抛出去
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setReady(true)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const signIn = useCallback(async (username: string, password: string) => {
    const body = await loginRequest(username, password)
    // 先写 token 再置 user：跳转后立刻发的请求要能带上它
    writeToken(body.token)
    setUser(body.user)
  }, [])

  const signOut = useCallback(async () => {
    try {
      await logoutRequest()
    } finally {
      // 服务端那条会话删没删成，本地都得退干净——退不掉的「退出登录」更糟
      clearToken()
      setUser(null)
    }
  }, [])

  return (
    <SessionContext value={{ user, ready, signIn, signOut }}>{children}</SessionContext>
  )
}

export function useSession(): Session {
  const session = useContext(SessionContext)
  if (!session) throw new Error('useSession 必须在 SessionProvider 里用')
  return session
}
```

- [ ] **Step 3: 写路由守卫**

创建 `website/src/features/auth/require-auth.tsx`：

```tsx
import { Navigate, Outlet, useLocation } from 'react-router-dom'

import { useSession } from './session-context'

/** 没登录一律送到登录页，并把原地址记下来，登录完直接回去。 */
export function RequireAuth() {
  const { user, ready } = useSession()
  const location = useLocation()

  // 首次 /auth/me 还没回来时什么都不渲染：这时候判断会先闪一下登录页，
  // 已经登录的人每次刷新都看见闪一下，像是登录态不稳
  if (!ready) return null

  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />

  return <Outlet />
}
```

- [ ] **Step 4: 写登录页**

创建 `website/src/features/auth/login-page.tsx`：

```tsx
import { useState } from 'react'
import { Navigate, useLocation, useNavigate } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { useSession } from './session-context'

export function LoginPage() {
  const { user, ready, signIn } = useSession()
  const navigate = useNavigate()
  const location = useLocation()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (!ready) return null
  if (user) {
    const from = (location.state as { from?: string } | null)?.from
    return <Navigate to={from ?? '/antony/appointments'} replace />
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await signIn(username.trim(), password)
      const from = (location.state as { from?: string } | null)?.from
      navigate(from ?? '/antony/appointments', { replace: true })
    } catch (err) {
      // 就地显示，不弹 toast：错误紧挨着输入框才看得见，
      // 而登录失败之后人的视线本来就在输入框上
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-80 rounded-lg border border-border bg-card p-6"
      >
        <h1 className="mb-1 text-base font-semibold">小程序矩阵 · 管理后台</h1>
        <p className="mb-5 text-sm text-muted-foreground">账号由超级管理员分配</p>

        <div className="mb-3 grid gap-1.5">
          <Label htmlFor="username">用户名</Label>
          <Input
            id="username"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
          />
        </div>

        <div className="mb-4 grid gap-1.5">
          <Label htmlFor="password">密码</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>

        {error && <p className="mb-3 text-sm text-destructive">{error}</p>}

        <Button type="submit" className="w-full" disabled={busy || !username || !password}>
          {busy ? '登录中…' : '登录'}
        </Button>
      </form>
    </div>
  )
}
```

- [ ] **Step 5: 接进路由**

`website/src/main.tsx` 整份改成：

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { Toaster } from '@/components/ui/sonner'
import { AppShell } from '@/app-shell'
import { LoginPage } from '@/features/auth/login-page'
import { RequireAuth } from '@/features/auth/require-auth'
import { SessionProvider } from '@/features/auth/session-context'
import { AppointmentsPage } from '@/features/antony/appointments-page'
import { HomeMediaPage } from '@/features/antony/home-media-page'
import { ServiceApplicationsPage } from '@/features/antony/service-applications-page'

import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          {/* 除登录页外一律要登录。守卫套在 AppShell 外面，
              未登录时连侧栏都不该渲染出来 */}
          <Route element={<RequireAuth />}>
            <Route element={<AppShell />}>
              <Route index element={<Navigate to="/antony/appointments" replace />} />
              <Route path="/antony/appointments" element={<AppointmentsPage />} />
              <Route path="/antony/service-applications" element={<ServiceApplicationsPage />} />
              <Route path="/antony/home-media" element={<HomeMediaPage />} />
              {/* 打错地址不该白屏，回到默认页 */}
              <Route path="*" element={<Navigate to="/antony/appointments" replace />} />
            </Route>
          </Route>
        </Routes>
      </SessionProvider>
    </BrowserRouter>
    {/* 接口报错统一走 toast，见 features/antony/use-paged-list.ts */}
    <Toaster position="top-center" richColors />
  </StrictMode>,
)
```

- [ ] **Step 6: 类型检查与 lint**

Run: `cd website && pnpm build && pnpm lint`
Expected: 都通过

> 若 `<SessionContext value={...}>` 报类型错（React 19 才支持 Context 直接当 Provider 用），
> 改成 `<SessionContext.Provider value={...}>`。package.json 里是 react 19，正常不会报。

- [ ] **Step 7: 手工验证**

两个终端分别跑：

```bash
cd server && uv run uvicorn app.main:app --port 3000
cd website && pnpm dev
```

打开 `http://localhost:5190/antony/appointments`，逐条确认：

1. 直接被弹到 `/login`
2. 输错密码 → 表单下方显示「用户名或密码不正确」，不跳转
3. 输对 `.env` 里的超管账号密码 → 跳回 `/antony/appointments`，列表能出数据
4. 刷新页面 → 仍在列表页，不回登录页
5. 在 DevTools 里 `localStorage.removeItem('antony-admin-token')` 后点侧栏切页 → 被送回登录页

- [ ] **Step 8: 提交**

```bash
git add website/src/features/auth website/src/main.tsx
git commit -m "feat(website): 登录页与路由守卫"
```

---

### Task 8: 侧栏账号区与修改我的密码

**Files:**
- Create: `website/src/components/ui/dialog.tsx`、`website/src/features/auth/change-password-dialog.tsx`
- Modify: `website/src/app-shell.tsx`

**Interfaces:**
- Consumes: Task 7 的 `useSession`、`@/features/auth/api` 的 `changeMyPassword`
- Produces: `Dialog` / `DialogContent` / `DialogHeader` / `DialogTitle` / `DialogDescription` / `DialogFooter` / `DialogTrigger` / `DialogClose`；`ChangePasswordDialog`（受控组件，props `{ open, onOpenChange }`）

- [ ] **Step 1: 加 Dialog 组件**

创建 `website/src/components/ui/dialog.tsx`（与 `sheet.tsx` 同一个 radix primitive，
只是居中而不是贴边；样式沿用 sheet 的写法）：

```tsx
"use client"

import * as React from "react"
import { Dialog as DialogPrimitive } from "radix-ui"
import { XIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

function Dialog({ ...props }: React.ComponentProps<typeof DialogPrimitive.Root>) {
  return <DialogPrimitive.Root data-slot="dialog" {...props} />
}

function DialogTrigger({
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Trigger>) {
  return <DialogPrimitive.Trigger data-slot="dialog-trigger" {...props} />
}

function DialogClose({ ...props }: React.ComponentProps<typeof DialogPrimitive.Close>) {
  return <DialogPrimitive.Close data-slot="dialog-close" {...props} />
}

function DialogOverlay({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      data-slot="dialog-overlay"
      className={cn(
        "fixed inset-0 z-50 bg-black/10 duration-100 supports-backdrop-filter:backdrop-blur-xs data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0",
        className
      )}
      {...props}
    />
  )
}

function DialogContent({
  className,
  children,
  showCloseButton = true,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  showCloseButton?: boolean
}) {
  return (
    <DialogPrimitive.Portal data-slot="dialog-portal">
      <DialogOverlay />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        className={cn(
          "fixed top-1/2 left-1/2 z-50 flex w-full max-w-96 -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-lg border border-border bg-popover p-4 text-sm text-popover-foreground shadow-lg duration-150 data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95",
          className
        )}
        {...props}
      >
        {children}
        {showCloseButton && (
          <DialogPrimitive.Close data-slot="dialog-close" asChild>
            <Button variant="ghost" className="absolute top-3 right-3" size="icon-sm">
              <XIcon />
              <span className="sr-only">关闭</span>
            </Button>
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-header"
      className={cn("flex flex-col gap-0.5", className)}
      {...props}
    />
  )
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="dialog-footer"
      className={cn("flex justify-end gap-2", className)}
      {...props}
    />
  )
}

function DialogTitle({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Title>) {
  return (
    <DialogPrimitive.Title
      data-slot="dialog-title"
      className={cn("font-heading text-base font-medium text-foreground", className)}
      {...props}
    />
  )
}

function DialogDescription({
  className,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      data-slot="dialog-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Dialog,
  DialogTrigger,
  DialogClose,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
}
```

- [ ] **Step 2: 写改密码对话框**

创建 `website/src/features/auth/change-password-dialog.tsx`：

```tsx
import { useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

import { changeMyPassword } from './api'
import { useSession } from './session-context'

export function ChangePasswordDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { user } = useSession()
  const [oldPassword, setOld] = useState('')
  const [newPassword, setNew] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)

  // 超管的密码在服务器配置文件里，接口改不了（服务端也会拒）。
  // 这里直接说清楚，而不是让人填完再吃一个错误
  if (user?.isSuper) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>修改密码</DialogTitle>
            <DialogDescription>
              超级管理员的密码在服务器配置文件（.env 的 ADMIN_SUPER_PASSWORD）里维护，
              改完需要重启服务。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              知道了
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    )
  }

  const mismatch = confirm !== '' && confirm !== newPassword
  const canSubmit = !busy && oldPassword !== '' && newPassword.length >= 8 && !mismatch && confirm !== ''

  const submit = async () => {
    setBusy(true)
    try {
      await changeMyPassword(oldPassword, newPassword)
      // 别处的会话在服务端已经作废，当前这条留着，所以不用重登
      toast.success('密码已修改，其他设备上的登录已退出')
      onOpenChange(false)
      setOld('')
      setNew('')
      setConfirm('')
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>修改密码</DialogTitle>
          <DialogDescription>改完之后，你在其他设备上的登录会全部退出。</DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="old-password">当前密码</Label>
            <Input
              id="old-password"
              type="password"
              autoComplete="current-password"
              value={oldPassword}
              onChange={(e) => setOld(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-password">新密码</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(e) => setNew(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">8 到 64 位</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="confirm-password">再输一次</Label>
            <Input
              id="confirm-password"
              type="password"
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
            {mismatch && <p className="text-xs text-destructive">两次输入的新密码不一样</p>}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button onClick={submit} disabled={!canSubmit}>
            {busy ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 3: 侧栏加账号区**

`website/src/app-shell.tsx` 整份改成：

```tsx
import { useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { ChangePasswordDialog } from '@/features/auth/change-password-dialog'
import { useSession } from '@/features/auth/session-context'
import { cn } from '@/lib/utils'

// 一个后台管一个矩阵：左侧按小程序分组，组内是各自的数据模块。
// 现在只有安东尼之家；再接别的小程序时在这里加一组，功能代码各自留在 features/ 下。
const NAV = [
  {
    group: '安东尼之家',
    items: [
      { to: '/antony/appointments', label: '展厅预约申请' },
      { to: '/antony/service-applications', label: '服务申请' },
      { to: '/antony/home-media', label: '首页图片' },
    ],
  },
]

// 只有超管看得见。服务端也拦（见 admin_accounts.py），
// 这里隐藏只是不给普通管理员看见一个点不动的入口
const SUPER_NAV = {
  group: '系统',
  items: [{ to: '/accounts', label: '账号管理' }],
}

export function AppShell() {
  const { user, signOut } = useSession()
  const [changing, setChanging] = useState(false)
  const sections = user?.isSuper ? [...NAV, SUPER_NAV] : NAV

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <aside className="sticky top-0 flex h-screen w-52 shrink-0 flex-col border-r border-border bg-card">
        <div className="flex h-14 items-center border-b border-border px-4 text-sm font-semibold tracking-wide">
          小程序矩阵 · 管理后台
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          {sections.map((section) => (
            <div key={section.group} className="mb-3">
              <div className="px-2 py-1 text-xs text-muted-foreground">{section.group}</div>
              {section.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      'block rounded-md px-2 py-1.5 text-sm transition-colors',
                      isActive
                        ? 'bg-muted font-medium text-foreground'
                        : 'text-muted-foreground hover:bg-muted/60 hover:text-foreground',
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        <div className="border-t border-border p-3">
          <div className="truncate text-sm font-medium" title={user?.username}>
            {user?.displayName || user?.username}
          </div>
          <div className="mb-2 text-xs text-muted-foreground">
            {user?.isSuper ? '超级管理员' : '管理员'}
          </div>
          <div className="flex gap-1">
            <Button variant="outline" size="sm" onClick={() => setChanging(true)}>
              修改密码
            </Button>
            <Button variant="ghost" size="sm" onClick={() => void signOut()}>
              退出登录
            </Button>
          </div>
        </div>
      </aside>

      <main className="min-w-0 flex-1">
        <Outlet />
      </main>

      <ChangePasswordDialog open={changing} onOpenChange={setChanging} />
    </div>
  )
}
```

- [ ] **Step 4: 类型检查与 lint**

Run: `cd website && pnpm build && pnpm lint`
Expected: 都通过

- [ ] **Step 5: 手工验证**

服务端和前端都跑着，用超管登录后确认：

1. 侧栏底部显示「超级管理员」，并多出「系统 / 账号管理」分组（点进去是 404 回跳，Task 9 才建页面）
2. 点「修改密码」→ 弹窗说明超管密码在配置文件里，没有输入框
3. 点「退出登录」→ 回到登录页；刷新仍在登录页

- [ ] **Step 6: 提交**

```bash
git add website/src/components/ui/dialog.tsx \
        website/src/features/auth/change-password-dialog.tsx website/src/app-shell.tsx
git commit -m "feat(website): 侧栏账号区与修改密码"
```

---

### Task 9: 账号管理页

**Files:**
- Create: `website/src/lib/format.ts`、`website/src/features/accounts/api.ts`、`website/src/features/accounts/accounts-page.tsx`
- Modify: `website/src/features/antony/use-paged-list.ts`（挪走 `formatTime`）、`website/src/features/antony/appointments-page.tsx:29`、`website/src/features/antony/service-applications-page.tsx:37`、`website/src/main.tsx`（加路由）

**Interfaces:**
- Consumes: `@/lib/api` 的 `call`；Task 5 的账号管理接口；Task 8 的 `Dialog` 组件族；
  `@/components/status-badge` 的 `StatusBadge`，它只收一个 prop：
  `status: StatusOption<string>`，即 `{ value: string; label: string; tone: 'pending' | 'active' | 'done' | 'muted' }`
  （定义在 `@/features/antony/constants`，**没有** `success` 这个 tone）
- Produces: `AccountsPage`（组件）；`@/lib/format` 的 `formatTime(iso: string): string`

- [ ] **Step 1: 把 formatTime 挪到 lib**

账号管理页要显示时间，但 `app-shell.tsx` 开头写明「功能代码各自留在 features/ 下」，
新的 `features/accounts/` 不该去 import `features/antony/`。这个函数与业务无关，挪到 lib。

创建 `website/src/lib/format.ts`：

```ts
/** 2026-08-05T14:03:22+08:00 → 2026-08-05 14:03 */
export function formatTime(iso: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}
```

从 `website/src/features/antony/use-paged-list.ts` **删掉**末尾的 `formatTime`
（第 123-129 行，连同它上面那行注释）。

`appointments-page.tsx` 第 29 行和 `service-applications-page.tsx` 第 37 行，
把 `import { formatTime, usePagedList } from './use-paged-list'` 拆成两行：

```ts
import { formatTime } from '@/lib/format'
import { usePagedList } from './use-paged-list'
```

（`import` 顺序按各文件既有的分组：`@/` 开头的和相对路径的分属两组。）

- [ ] **Step 2: 写接口层**

创建 `website/src/features/accounts/api.ts`：

```ts
// 管理员账号管理。只有超管调得通，服务端见 server/app/admin_accounts.py。

import { call } from '@/lib/api'

export type Account = {
  /** 雪花 ID，字符串。别转成 number，18 位会丢精度 */
  id: string
  username: string
  displayName: string
  status: 'active' | 'disabled'
  lastLoginAt: string | null
  loginCount: number
  createdAt: string
}

const json = (body: unknown): RequestInit => ({
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const listAccounts = () => call<{ ok: true; items: Account[] }>('/accounts')

export const createAccount = (input: {
  username: string
  displayName: string
  password: string
}) =>
  call<{ ok: true; account: Account }>('/accounts', { method: 'POST', ...json(input) })

export const resetAccountPassword = (id: string, password: string) =>
  call<{ ok: true }>(`/accounts/${id}/password`, { method: 'PUT', ...json({ password }) })

export const setAccountStatus = (id: string, status: Account['status']) =>
  call<{ ok: true; account: Account }>(`/accounts/${id}/status`, {
    method: 'PUT',
    ...json({ status }),
  })

export const deleteAccount = (id: string) =>
  call<{ ok: true }>(`/accounts/${id}`, { method: 'DELETE' })
```

- [ ] **Step 3: 写页面**

创建 `website/src/features/accounts/accounts-page.tsx`：

```tsx
import { useCallback, useEffect, useState } from 'react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { StatusBadge } from '@/components/status-badge'
import { formatTime } from '@/lib/format'

import {
  createAccount,
  deleteAccount,
  listAccounts,
  resetAccountPassword,
  setAccountStatus,
} from './api'
import type { Account } from './api'

// 与 server/app/security.py 的 PASSWORD_MIN_LEN 一致
const PASSWORD_MIN = 8

// tone 的取值来自 @/features/antony/constants 的 StatusOption：
// 只有 pending / active / done / muted 四种，正常的用 done，停用的用 muted
const STATUS_META = {
  active: { value: 'active', label: '正常', tone: 'done' },
  disabled: { value: 'disabled', label: '已停用', tone: 'muted' },
} as const

export function AccountsPage() {
  const [items, setItems] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)
  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<Account | null>(null)

  const reload = useCallback(async () => {
    setLoading(true)
    try {
      const body = await listAccounts()
      setItems(body.items)
    } catch (err) {
      // 出错就清空并弹提示：留着上一次的列表会让人以为刚建的号没建上
      setItems([])
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  const toggle = async (account: Account) => {
    const next = account.status === 'active' ? 'disabled' : 'active'
    if (next === 'disabled' && !confirm(`停用「${account.username}」？他会立刻被踢下线。`)) return
    try {
      await setAccountStatus(account.id, next)
      toast.success(next === 'disabled' ? '已停用' : '已启用')
      await reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  const remove = async (account: Account) => {
    if (!confirm(`删除「${account.username}」？删了就找不回来了，只是停用请点「停用」。`)) return
    try {
      await deleteAccount(account.id)
      toast.success('已删除')
      await reload()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    }
  }

  return (
    <div className="p-6">
      <header className="mb-4 flex items-baseline gap-3">
        <h1 className="text-lg font-semibold">账号管理</h1>
        <span className="text-sm text-muted-foreground">共 {items.length} 个</span>
        <Button className="ml-auto" onClick={() => setCreating(true)}>
          新建账号
        </Button>
      </header>

      <p className="mb-3 text-sm text-muted-foreground">
        后台没有注册入口，账号只能在这里创建。超级管理员的账号密码在服务器配置文件里，
        不在这个列表中。
      </p>

      <div className="overflow-x-auto rounded-lg border border-border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-40">用户名</TableHead>
              <TableHead className="w-32">姓名</TableHead>
              <TableHead className="w-24">状态</TableHead>
              <TableHead className="w-36">最近登录</TableHead>
              <TableHead className="w-20 text-right">登录次数</TableHead>
              <TableHead className="w-36">创建时间</TableHead>
              <TableHead>操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading ? (
              Array.from({ length: 3 }, (_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={7}>
                    <Skeleton className="h-5 w-full" />
                  </TableCell>
                </TableRow>
              ))
            ) : items.length === 0 ? (
              <TableRow>
                <TableCell colSpan={7} className="h-24 text-center text-muted-foreground">
                  还没有其他管理员，点右上角新建
                </TableCell>
              </TableRow>
            ) : (
              items.map((account) => (
                <TableRow key={account.id}>
                  <TableCell className="font-medium">{account.username}</TableCell>
                  <TableCell>
                    {account.displayName || <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell>
                    <StatusBadge status={STATUS_META[account.status]} />
                  </TableCell>
                  <TableCell>
                    {account.lastLoginAt ? (
                      formatTime(account.lastLoginAt)
                    ) : (
                      <span className="text-muted-foreground">从未登录</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{account.loginCount}</TableCell>
                  <TableCell>{formatTime(account.createdAt)}</TableCell>
                  <TableCell>
                    <div className="flex gap-1">
                      <Button variant="outline" size="sm" onClick={() => setResetting(account)}>
                        重置密码
                      </Button>
                      <Button variant="outline" size="sm" onClick={() => void toggle(account)}>
                        {account.status === 'active' ? '停用' : '启用'}
                      </Button>
                      <Button
                        variant="destructive"
                        size="sm"
                        onClick={() => void remove(account)}
                      >
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <CreateDialog open={creating} onOpenChange={setCreating} onCreated={reload} />
      <ResetDialog account={resetting} onClose={() => setResetting(null)} />
    </div>
  )
}

function CreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: () => Promise<void>
}) {
  const [username, setUsername] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    setBusy(true)
    try {
      await createAccount({ username: username.trim(), displayName: displayName.trim(), password })
      // 密码只在这一刻能看到，之后库里只有哈希，谁都读不回来
      toast.success(`已创建「${username.trim()}」，把初始密码告诉本人`)
      onOpenChange(false)
      setUsername('')
      setDisplayName('')
      setPassword('')
      await onCreated()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新建账号</DialogTitle>
          <DialogDescription>
            初始密码由你设置并线下告诉本人，对方登录后可以自己改。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="new-username">用户名</Label>
            <Input
              id="new-username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="字母、数字和 _ . -"
            />
            <p className="text-xs text-muted-foreground">3 到 32 位，创建后不能改</p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-display-name">姓名</Label>
            <Input
              id="new-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="可留空"
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-account-password">初始密码</Label>
            <Input
              id="new-account-password"
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <p className="text-xs text-muted-foreground">{PASSWORD_MIN} 到 64 位</p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={submit}
            disabled={busy || username.trim().length < 3 || password.length < PASSWORD_MIN}
          >
            {busy ? '创建中…' : '创建'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ResetDialog({
  account,
  onClose,
}: {
  account: Account | null
  onClose: () => void
}) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!account) return
    setBusy(true)
    try {
      await resetAccountPassword(account.id, password)
      toast.success(`已重置「${account.username}」的密码，他已被踢下线`)
      setPassword('')
      onClose()
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={account !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重置密码</DialogTitle>
          <DialogDescription>
            给「{account?.username}」设一个新密码。他当前的登录会立刻失效，需要用新密码重登。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-1.5">
          <Label htmlFor="reset-password">新密码</Label>
          <Input
            id="reset-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <p className="text-xs text-muted-foreground">{PASSWORD_MIN} 到 64 位</p>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            取消
          </Button>
          <Button onClick={submit} disabled={busy || password.length < PASSWORD_MIN}>
            {busy ? '保存中…' : '保存'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```

- [ ] **Step 4: 加路由**

`website/src/main.tsx` 的 import 区加：

```tsx
import { AccountsPage } from '@/features/accounts/accounts-page'
```

在 `/antony/home-media` 那一行**之后**、`path="*"` 那一行**之前**加：

```tsx
              {/* 只有超管进得去，普通管理员访问会吃服务端的 403 并在页面上报错 */}
              <Route path="/accounts" element={<AccountsPage />} />
```

- [ ] **Step 5: 类型检查与 lint**

Run: `cd website && pnpm build && pnpm lint`
Expected: 都通过。`tsc` 会顺带确认两个 antony 页面的 `formatTime` 引用都改到位了

- [ ] **Step 6: 手工验证整条链路**

服务端和前端都跑着，用超管登录后：

1. 进「账号管理」→ 列表为空，提示「还没有其他管理员」
2. 新建账号 `zhangsan` / 密码 `zhangsan-123` → 列表出现一行，状态「正常」，最近登录「从未登录」
3. 重复用同一个用户名新建 → toast 提示「这个用户名已被占用」
4. 用户名填 2 位或密码填 5 位 → 「创建」按钮是灰的
5. 退出登录，用 `zhangsan` 登录 → 侧栏底部显示「管理员」，**看不到**「系统 / 账号管理」分组；手动访问 `/accounts` → 页面上报 403 的错误提示
6. `zhangsan` 点「修改密码」→ 是真的三个输入框（不是超管那段说明），改成 `zhangsan-456`；改完刷新页面仍是登录状态
7. 退出，用 `zhangsan-456` 能登进来，用 `zhangsan-123` 登不进来
8. 换回超管登录，把 `zhangsan` 停用 → 状态变「已停用」
9. （另开一个浏览器窗口先让 `zhangsan` 登录着，再执行第 8 步）那个窗口点任意菜单 → 立刻被送回登录页
10. 删除 `zhangsan` → 列表空

- [ ] **Step 7: 提交**

```bash
git add website/src/features/accounts website/src/lib/format.ts website/src/main.tsx \
        website/src/features/antony/use-paged-list.ts \
        website/src/features/antony/appointments-page.tsx \
        website/src/features/antony/service-applications-page.tsx
git commit -m "feat(website): 账号管理页"
```

---

## 完成后的收尾

- [ ] 全量后端测试：`cd server && uv run pytest -q` 全绿
- [ ] 前端：`cd website && pnpm build && pnpm lint` 均通过
- [ ] 确认 `server/.env` 里的 `ADMIN_SUPER_PASSWORD` **不是**开发用的那个弱密码就上线，
      且 `.env` 没有被 `git add`（`server/.gitignore` 已经忽略它，`git status` 再确认一次）
- [ ] 部署顺序：**先跑 `migrations/006_admin_auth.sql`，再发新代码**。反过来的话，
      新代码起来时查不到 `admin_sessions` 表，后台每个接口都会 500

## Self-Review 记录

- **Spec 覆盖**：一～八节逐节对照，均有对应任务（一→Task 3 配置 + Task 5 保留用户名校验；二→Task 2；三→Task 1；四→Task 3 的 TTL/滑动续期；五→Task 3/4/5；六→Task 6/7/8/9；七→Task 1/3/4/5 的测试 + Task 4 改既有测试；八→Task 4 的 README 与 `.env.example`）。
- **占位符**：无 TBD/TODO，每个代码步骤都给了完整可粘贴的实现。
- **类型一致性**：`current_admin` 返回的键（`token` / `id` / `username` / `displayName` / `isSuper`）在 Task 3、4、5 中用法一致；前端 `AdminUser` 三个字段与服务端 `/auth/me`、`/auth/login` 出参一致；`Account` 七个字段与 `admin_accounts.to_json` 一一对应。
- 自审时改掉的三处：`StatusBadge` 的 props 一开始写成了 `{label, tone:'success'}`，
  实际是 `StatusOption<string>` 且没有 `success` 这个 tone；`formatTime` 原本让
  `features/accounts/` 直接去 import `features/antony/`，违反这个仓库自己的分区约定，
  改为挪到 `lib/format.ts`；测试的 `clean()` 没有清超管的登录失败计数，
  攒够 5 次会让**别的**测试莫名吃 429。

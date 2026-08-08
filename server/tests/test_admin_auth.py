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

# 本文件里每一次成功登录（含反复调用 super_headers()）拿到的 token，测试跑完要
# 精确删掉这些会话对应的行。不能按 `is_super` 一刀切删：conftest.py 的
# auth_headers 也是一条超管会话，test_admin.py / test_home_media.py 整个测试
# 会话期间都在用它，粗暴地按 is_super 删会把那条也删掉，那两个文件会莫名其妙
# 全红。精确记 token、精确删，才不会误伤别的文件正在用的会话
_issued_tokens: set[str] = set()


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
        # 本文件登录超管建的那些会话按 token 精确删掉——见 _issued_tokens 上面
        # 那段注释，为什么不能按 is_super 整批删
        if _issued_tokens:
            await conn.execute(
                "DELETE FROM admin_sessions WHERE token = ANY(%s)",
                (list(_issued_tokens),),
            )
            _issued_tokens.clear()


@pytest.fixture
async def client():
    await clean()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await clean()


async def login(client, username: str, password: str):
    r = await client.post(
        "/api/admin/auth/login", json={"username": username, "password": password}
    )
    if r.status_code == 200:
        # 记下这个 token，交给 clean() 在这条测试结束时精确删掉
        _issued_tokens.add(r.json()["token"])
    return r


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


async def test_non_ascii_password_against_super_username_is_401_not_500(client):
    # 早期实现用 secrets.compare_digest 直接比对超管密码明文，带非 ASCII 字符的
    # 密码会让它抛 TypeError、裸奔成 500（500 和 401 的区别本身就能让人一次请求
    # 判断出这就是超管用户名）。现在密码统一走 verify_password（scrypt，对任意
    # Unicode 都能正确算），这行留作回归测试，确认这条路径不会再裸奔出 500
    r = await login(client, SUPER_USERNAME, "密码不对但是中文")
    assert r.status_code == 401, r.text


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


# ---------------------------------------------------------------------------
# 鉴权真的覆盖了业务接口


# 遍历真实路由表，而不是手抄一份路径清单：将来在 admin.py 里新加接口、或者有人
# 不小心把某条路由挪出受保护的 router，这条测试都会自动纳入、自动失败，不需要
# 有人记得回来往清单里补一行——「新接口自动受保护」正是鉴权挂在 router 层而不是
# 逐个接口挂的意义，这条测试就是在守这个性质本身。
#
# 走 app.openapi()["paths"] 而不是直接遍历 app.routes：这个 FastAPI 版本
# （0.141）里 include_router() 挂上去的子路由不会被立刻拍平进 app.routes，
# 会包一层内部的 `_IncludedRouter`，直接遍历 app.routes 只能看到在 main.py
# 里用 @app.xxx 直接定义的那几条，admin/home/users 那些子 router 全部隐身，
# 断言过不了会以为鉴权哪里漏了，其实是遍历方式不对。openapi() 是 FastAPI
# 生成 /docs 用的公开接口，本来就要求把全部路由（含嵌套 router）拍平，稳。
ADMIN_PATHS = [
    (path, method.upper())
    for path, methods in app.openapi()["paths"].items()
    for method in methods
    if path.startswith("/api/admin/") and not path.startswith("/api/admin/auth/")
]

# 上面这行推导要是被改挂成空列表，parametrize 会用 0 条用例安静地「全绿」过去，
# 而这条测试原本就是要盯住「后台路由一条都没漏保护」——放在模块级，写错时收集
# 阶段就直接报错，不会被 0 用例悄悄放过
assert ADMIN_PATHS, "遍历路由表得到了空列表，鉴权覆盖测试形同虚设"


@pytest.mark.parametrize("path,method", ADMIN_PATHS)
async def test_business_endpoints_require_login(client, path, method):
    """不带 token 时，/api/admin/ 下任何一条业务接口（登录本身除外）都要 401。

    路径里 `{slot}` 这类占位符随便填一个合法值——鉴权是路由器级别的依赖，在
    请求体解析、路径参数校验之前就生效，占位符填什么不影响这里断言的 401。
    """
    url = path.format(slot="hero")
    r = await client.request(method, url)
    assert r.status_code == 401, f"{method} {url}: {r.text}"


async def test_business_endpoints_work_with_a_token(client):
    headers = await super_headers(client)
    r = await client.get("/api/admin/appointments", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

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

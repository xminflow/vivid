"""安家立业的会员开通。

一期免费、自助、即时生效，不分等级。到期时间在开通那一刻写成一年后，
但**任何地方都不校验它**（docs/adr/0002），所以这里只钉「写下来了」，
不钉「过期了会怎样」——那是二期的事。
"""

from datetime import datetime, timedelta

import pytest

from app.anjia import db as anjia_db
from app.db import pool as antony_pool

from .conftest import auth, login

pytestmark = pytest.mark.skipif(
    not anjia_db.configured(), reason="没配 DATABASE_URL_ANJIA，安家立业的接口没注册"
)


async def join(client, headers) -> dict:
    r = await client.post("/api/anjia/users/me/membership", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["user"]


async def test_new_user_is_not_a_member(client):
    body = await login(client)
    assert body["user"]["isMember"] is False
    assert body["user"]["memberExpiresAt"] is None


async def test_join_makes_you_a_member(client):
    headers = await auth(client)
    user = await join(client, headers)
    assert user["isMember"] is True
    assert user["memberExpiresAt"]

    # 不只是出参变了，库里也真的变了
    async with anjia_db.get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT is_member, member_since FROM users WHERE id = %s",
                (int(user["id"]),),
            )
        ).fetchone()
    assert row["is_member"] is True
    assert row["member_since"] is not None


async def test_membership_survives_a_reload(client):
    """开通完重新读一次用户，还是会员——出参不是临时拼出来的。"""
    headers = await auth(client)
    await join(client, headers)

    r = await client.get("/api/anjia/users/me", headers=headers)
    assert r.status_code == 200
    assert r.json()["user"]["isMember"] is True


async def test_expiry_is_one_year_out(client):
    headers = await auth(client)
    user = await join(client, headers)

    expires = datetime.fromisoformat(user["memberExpiresAt"])
    async with anjia_db.get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT member_since FROM users WHERE id = %s", (int(user["id"]),)
            )
        ).fetchone()
    # 一年后。用开通时间做基准而不是「现在」，免得跨零点或慢机器上抖动
    assert abs((expires - row["member_since"]) - timedelta(days=365)) < timedelta(days=1)


async def test_joining_twice_changes_nothing(client):
    """重复点开通是幂等的：不报错，也**不延长有效期**。

    延长会变成「反复点击刷有效期」的漏洞，所以这里逐字段比对，不是只看不报错。
    """
    headers = await auth(client)
    first = await join(client, headers)
    second = await join(client, headers)

    assert second["isMember"] is True
    assert second["memberExpiresAt"] == first["memberExpiresAt"]

    async with anjia_db.get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT member_since FROM users WHERE id = %s", (int(first["id"]),)
            )
        ).fetchone()
    assert row["member_since"].isoformat()  # 存在
    # 第二次开通没把 member_since 推后
    async with anjia_db.get_pool().connection() as conn:
        again = await (
            await conn.execute(
                "SELECT member_since FROM users WHERE id = %s", (int(first["id"]),)
            )
        ).fetchone()
    assert again["member_since"] == row["member_since"]


async def test_join_requires_login(client):
    r = await client.post("/api/anjia/users/me/membership")
    assert r.status_code == 401


async def test_membership_is_per_user(client):
    """一个人开通不会把别人也变成会员。"""
    mine = await auth(client, "1")
    theirs = await auth(client, "2")
    await join(client, mine)

    r = await client.get("/api/anjia/users/me", headers=theirs)
    assert r.json()["user"]["isMember"] is False


async def test_writes_to_anjia_database(client):
    """会员标记落在安家立业的库，安东尼之家的库里查不到这个人。

    两个池连错库不抛异常，只会安静地写到别人家，所以正反两边都断言。
    """
    headers = await auth(client)
    user = await join(client, headers)
    user_id = int(user["id"])

    async with anjia_db.get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT is_member FROM users WHERE id = %s", (user_id,)
            )
        ).fetchone()
    assert row["is_member"] is True

    async with antony_pool.connection() as conn:
        leaked = await (
            await conn.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        ).fetchone()
    assert leaked is None, "安家立业的会员写进了安东尼之家的库"

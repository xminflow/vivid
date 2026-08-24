"""安家立业的用户与登录接口测试。

夹具（假微信、建号、清理）在 tests/anjia/conftest.py。

这组用例最要紧的一条其实是 test_writes_to_anjia_database——两个小程序两个库，
连错库不会报错，只会把数据写到别人家去，靠人眼是看不出来的。
"""

import pytest

from app.anjia import db as anjia_db
from app.db import pool as antony_pool

from .conftest import TEST_OPENID_PREFIX, login

pytestmark = pytest.mark.skipif(
    not anjia_db.configured(), reason="没配 DATABASE_URL_ANJIA，安家立业的接口没注册"
)


async def test_login_creates_user(client):
    body = await login(client)
    assert body["ok"] is True
    assert body["token"]
    # 雪花 id 超过 JS 安全整数，必须是字符串出参
    assert isinstance(body["user"]["id"], str)
    assert body["user"]["loginCount"] == 1


async def test_login_twice_keeps_same_user(client):
    first = await login(client)
    second = await login(client)
    assert first["user"]["id"] == second["user"]["id"]
    assert second["user"]["loginCount"] == 2
    # 每次登录换新 token，老的不再有效
    assert first["token"] != second["token"]
    r = await client.get(
        "/api/anjia/users/me", headers={"Authorization": f"Bearer {first['token']}"}
    )
    assert r.status_code == 401


async def test_me_requires_token(client):
    r = await client.get("/api/anjia/users/me")
    assert r.status_code == 401


async def test_me_returns_logged_in_user(client):
    body = await login(client)
    r = await client.get(
        "/api/anjia/users/me", headers={"Authorization": f"Bearer {body['token']}"}
    )
    assert r.status_code == 200
    assert r.json()["user"]["id"] == body["user"]["id"]


async def test_different_code_is_a_different_user(client):
    """假 code2session 从 code 推 openid，换个 code 就该是另一个人。

    多用户场景（谁的认证是谁的）全靠这条成立，所以单独钉一下。
    """
    first = await login(client, "1")
    second = await login(client, "2")
    assert first["user"]["id"] != second["user"]["id"]
    assert second["user"]["loginCount"] == 1


async def test_writes_to_anjia_database(client):
    """写进的是安家立业的库，不是安东尼之家的。

    两个池连错库是这次改动最可能出的错，且它不会抛异常——只会安静地写到别人家。
    所以正反两边都查：本库有这一行，另一个库没有。
    """
    body = await login(client)
    user_id = int(body["user"]["id"])

    async with anjia_db.get_pool().connection() as conn:
        row = await (
            await conn.execute("SELECT openid FROM users WHERE id = %s", (user_id,))
        ).fetchone()
    assert row is not None
    assert row["openid"] == f"{TEST_OPENID_PREFIX}1"

    async with antony_pool.connection() as conn:
        leaked = await (
            await conn.execute("SELECT id FROM users WHERE id = %s", (user_id,))
        ).fetchone()
    assert leaked is None, "安家立业的用户写进了安东尼之家的库"

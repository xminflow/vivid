"""后台的安家立业用户列表。只读——没有封禁，也没有后台开通会员。"""

import pytest

from app.anjia import db as anjia_db

from .conftest import auth, login

pytestmark = pytest.mark.skipif(
    not anjia_db.configured(), reason="没配 DATABASE_URL_ANJIA，安家立业的接口没注册"
)


async def listing(client, auth_headers, query: str = "") -> dict:
    r = await client.get(f"/api/admin/anjia/users{query}", headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()


def find(body: dict, user_id: str) -> dict | None:
    return next((i for i in body["items"] if i["id"] == user_id), None)


async def test_registered_user_shows_up(client, auth_headers):
    body = await login(client)
    row = find(await listing(client, auth_headers), body["user"]["id"])
    assert row is not None
    assert row["isMember"] is False
    assert row["loginCount"] == 1


async def test_membership_is_visible(client, auth_headers):
    headers = await auth(client)
    r = await client.post("/api/anjia/users/me/membership", headers=headers)
    user_id = r.json()["user"]["id"]

    row = find(await listing(client, auth_headers, "?member=true"), user_id)
    assert row is not None
    assert row["isMember"] is True
    assert row["memberExpiresAt"]


async def test_secrets_never_leave_the_server(client, auth_headers):
    """openid / session_key / token 一个都不能出接口。"""
    await login(client)
    body = await listing(client, auth_headers)
    assert body["items"], "列表是空的，这条断言等于没跑"
    for item in body["items"]:
        assert not {"openid", "unionid", "sessionKey", "token"} & set(item)


async def test_ids_are_strings(client, auth_headers):
    """雪花 ID 有 18 位，出成数字前端会丢末几位。"""
    body = await login(client)
    row = find(await listing(client, auth_headers), body["user"]["id"])
    assert isinstance(row["id"], str)


async def test_requires_an_admin(client):
    r = await client.get("/api/admin/anjia/users")
    assert r.status_code == 401


async def test_reads_the_anjia_database(client, auth_headers):
    """读的是安家立业的库。

    安东尼之家的用户表里有 member_name / phone 这些列，安家立业的没有；
    真要是连错了库，这里出的就会是另一批人。用「刚建的这个号在不在」来判定。
    """
    body = await login(client, "9")
    assert find(await listing(client, auth_headers, "?pageSize=100"), body["user"]["id"])

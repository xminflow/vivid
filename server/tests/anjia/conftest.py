"""安家立业接口测试的共用夹具。

不真连微信：`code2session` 用假的替掉，只验从 openid 往后的落库与鉴权逻辑。
假的那个**从 code 推出 openid**（`test_openid_` + code），于是「换个用户登录」
就是「换个 code 提交」，不必为多用户场景再写一层 monkeypatch。

造的数据都用 TEST_OPENID_PREFIX 开头的 openid，每个用例前后各清一次。
清理只删 users，认证申请靠外键 ON DELETE CASCADE 跟着走。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.anjia import db as anjia_db
from app.anjia import users as anjia_users
from app.db import pool as antony_pool
from app.main import app

TEST_OPENID_PREFIX = "test_openid_"


async def fake_code2session(
    code: str, appid: str = "", secret: str = "", suffix: str = ""
) -> dict:
    return {
        "openid": f"{TEST_OPENID_PREFIX}{code}",
        "unionid": None,
        "session_key": "fake-session-key",
    }


@pytest.fixture(autouse=True)
def wechat(monkeypatch):
    monkeypatch.setattr(anjia_users, "code2session", fake_code2session)


async def clean():
    async with anjia_db.get_pool().connection() as conn:
        await conn.execute(
            "DELETE FROM users WHERE openid LIKE %s", (f"{TEST_OPENID_PREFIX}%",)
        )


@pytest.fixture
async def client(db):
    await clean()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await clean()


async def login(client, code: str = "1") -> dict:
    """登录并返回响应体。同一个 code 就是同一个用户，换 code 就是换个人。"""
    r = await client.post("/api/anjia/auth/login", json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()


async def auth(client, code: str = "1") -> dict:
    """登录并返回可以直接塞进 headers 的请求头。"""
    body = await login(client, code)
    return {"Authorization": f"Bearer {body['token']}"}


# 普通管理员（非超管）。用 test. 前缀，与 tests/test_admin_auth.py 的约定一致——
# 库里可能有真实账号，清理只能按前缀删
PLAIN_ADMIN = "test.anjia.reviewer"
PLAIN_ADMIN_PASSWORD = "reviewer-pass-123"


async def _drop_plain_admin() -> None:
    async with antony_pool.connection() as conn:
        # 会话随外键级联删，不用单独清
        await conn.execute("DELETE FROM admin_users WHERE username = %s", (PLAIN_ADMIN,))
        await conn.execute(
            "DELETE FROM admin_login_attempts WHERE username = %s", (PLAIN_ADMIN,)
        )


@pytest.fixture
async def plain_admin(client, auth_headers):
    """一个**不是超管**的管理员的请求头。

    企业认证的审核权限刻意没有收紧到超管（审核是日常高频动作，卡在一个人身上会
    积压），而「没收紧」这件事只有拿一个真的普通管理员去打才证明得了——用超管测
    永远是绿的，哪天权限被误改成 current_super 也发现不了。
    """
    await _drop_plain_admin()
    r = await client.post(
        "/api/admin/accounts",
        headers=auth_headers,
        json={
            "username": PLAIN_ADMIN,
            "displayName": "测试审核员",
            "password": PLAIN_ADMIN_PASSWORD,
        },
    )
    assert r.status_code == 201, r.text

    r = await client.post(
        "/api/admin/auth/login",
        json={"username": PLAIN_ADMIN, "password": PLAIN_ADMIN_PASSWORD},
    )
    assert r.status_code == 200, r.text

    yield {"Authorization": f"Bearer {r.json()['token']}"}

    await _drop_plain_admin()

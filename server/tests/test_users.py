"""用户与登录接口测试。

不真连微信：code2session 用假的替掉，只验从 openid 往后的落库与鉴权逻辑。
造的数据都用 TEST_OPENID_PREFIX 开头的 openid，跑完清干净。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import cos
from app import users as users_module
from app.db import pool
from app.main import app
from app.snowflake import Snowflake

TEST_OPENID_PREFIX = "test_openid_"
# 形状跟 cos.build_key 生成的一致。测试不真传文件，只验从对象键往后的落库与出参
TEST_AVATAR_KEY = "uploads/avatar/20260804/0123456789abcdef0123456789abcdef.jpg"


def fake_session(openid: str, unionid: str | None = None):
    async def code2session(code: str) -> dict:
        # code 原样带进 openid，一个测试里能造多个不同用户
        return {"openid": openid, "unionid": unionid, "session_key": "fake-session-key"}

    return code2session


@pytest.fixture(autouse=True)
def wechat(monkeypatch):
    monkeypatch.setattr(users_module, "code2session", fake_session(f"{TEST_OPENID_PREFIX}1"))


@pytest.fixture
async def client():
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM users WHERE openid LIKE %s", (f"{TEST_OPENID_PREFIX}%",)
        )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    async with pool.connection() as conn:
        # 预约挂着外键，先清预约再清用户
        await conn.execute(
            """
            DELETE FROM appointments
             WHERE user_id IN (SELECT id FROM users WHERE openid LIKE %s)
            """,
            (f"{TEST_OPENID_PREFIX}%",),
        )
        await conn.execute(
            "DELETE FROM users WHERE openid LIKE %s", (f"{TEST_OPENID_PREFIX}%",)
        )


@pytest.fixture(scope="session", autouse=True)
async def db():
    await pool.open(wait=True, timeout=10)
    yield
    await pool.close()


async def login(client, **body) -> dict:
    r = await client.post("/api/auth/login", json={"code": "fake-code", **body})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- 雪花 ID


def test_snowflake_ids_are_unique_and_increasing():
    gen = Snowflake(worker_id=7)
    ids = [gen.next_id() for _ in range(5000)]
    assert len(set(ids)) == 5000
    assert ids == sorted(ids)


def test_snowflake_carries_its_worker_id():
    gen = Snowflake(worker_id=7)
    assert (gen.next_id() >> 12) & 0x3FF == 7


def test_snowflake_rejects_an_out_of_range_worker_id():
    with pytest.raises(ValueError):
        Snowflake(worker_id=1024)


# ---------------------------------------------------------------- 登录


async def test_login_creates_a_user_and_returns_a_token(client):
    body = await login(client, nickname="小明", avatarUrl="https://x/y.png")
    assert body["ok"] is True
    assert body["token"]
    assert body["user"]["nickname"] == "小明"
    # 雪花 id 超出 JS 安全整数，必须以字符串出接口
    assert isinstance(body["user"]["id"], str)
    assert int(body["user"]["id"]) > 0


async def test_login_never_leaks_openid_or_session_key(client):
    body = await login(client)
    assert "openid" not in body["user"]
    assert "sessionKey" not in body["user"]
    assert TEST_OPENID_PREFIX not in str(body)


async def test_logging_in_again_keeps_the_same_user(client):
    first = await login(client, nickname="小明")
    second = await login(client)

    assert second["user"]["id"] == first["user"]["id"]
    assert second["token"] != first["token"]
    # 这次没带昵称，不能把上次授权的洗成空
    assert second["user"]["nickname"] == "小明"

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT login_count FROM users WHERE id = %s", (int(first["user"]["id"]),)
            )
        ).fetchone()
    assert row["login_count"] == 2


async def test_different_openids_get_different_users(client, monkeypatch):
    first = await login(client)
    monkeypatch.setattr(users_module, "code2session", fake_session(f"{TEST_OPENID_PREFIX}2"))
    second = await login(client)
    assert first["user"]["id"] != second["user"]["id"]


async def test_login_needs_a_code(client):
    r = await client.post("/api/auth/login", json={})
    assert r.status_code == 400


# ---------------------------------------------------------------- 我的信息


async def test_profile_round_trip(client):
    token = (await login(client))["token"]
    auth = {"Authorization": f"Bearer {token}"}
    profile = {
        "memberName": "陈女士",
        "phone": "13612345678",
        "email": "chen@example.com",
        "birthday": "1990-05-20",
        "gender": "女",
        "region": ["浙江省", "杭州市", "西湖区"],
    }

    saved = await client.put("/api/users/me", json=profile, headers=auth)
    assert saved.status_code == 200, saved.text
    assert saved.json()["user"]["memberName"] == "陈女士"

    read = await client.get("/api/users/me", headers=auth)
    assert read.status_code == 200
    user = read.json()["user"]
    for key, value in profile.items():
        assert user[key] == value


async def test_profile_fields_may_all_be_blank(client):
    token = (await login(client))["token"]
    r = await client.put("/api/users/me", json={}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user"]["birthday"] == ""


async def test_profile_rejects_a_bad_phone(client):
    token = (await login(client))["token"]
    r = await client.put(
        "/api/users/me", json={"phone": "123"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 400
    assert r.json()["message"] == "电话格式不正确"


async def test_profile_rejects_a_bad_email(client):
    token = (await login(client))["token"]
    r = await client.put(
        "/api/users/me", json={"email": "not-an-email"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 400
    assert r.json()["message"] == "邮箱格式不正确"


async def test_profile_rejects_a_future_birthday(client):
    token = (await login(client))["token"]
    r = await client.put(
        "/api/users/me", json={"birthday": "2099-01-01"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 400
    assert r.json()["message"] == "生日不在可选范围内"


# ---------------------------------------------------------------- 头像


async def test_avatar_is_stored_as_a_key_and_returned_as_a_signed_url(client):
    token = (await login(client))["token"]
    auth = {"Authorization": f"Bearer {token}"}

    saved = await client.put(
        "/api/users/me/avatar", json={"avatarKey": TEST_AVATAR_KEY}, headers=auth
    )
    assert saved.status_code == 200, saved.text

    async with pool.connection() as conn:
        row = await (
            await conn.execute("SELECT avatar_key FROM users WHERE token = %s", (token,))
        ).fetchone()
    assert row["avatar_key"] == TEST_AVATAR_KEY

    user = (await client.get("/api/users/me", headers=auth)).json()["user"]
    # 对象键是内部标识，不能原样出接口——客户端拿着它也取不到图，只会被当成能拼 URL 的东西
    assert user["avatarUrl"] != TEST_AVATAR_KEY
    if cos.configured():
        assert user["avatarUrl"].startswith("https://")
        assert "q-signature=" in user["avatarUrl"]
    else:
        # 没配 COS 就签不出地址，回落到微信授权的外链（这里是空），页面显示会员名首字
        assert user["avatarUrl"] == ""


async def test_avatar_rejects_a_key_we_never_issued(client):
    token = (await login(client))["token"]
    auth = {"Authorization": f"Bearer {token}"}

    for bad in ("secrets/keys.jpg", "uploads/../../etc/passwd", ""):
        r = await client.put("/api/users/me/avatar", json={"avatarKey": bad}, headers=auth)
        assert r.status_code == 400, f"{bad} 应当被拒: {r.text}"


async def test_avatar_needs_a_token(client):
    r = await client.put("/api/users/me/avatar", json={"avatarKey": TEST_AVATAR_KEY})
    assert r.status_code == 401


async def test_saving_the_profile_does_not_wipe_the_avatar(client):
    """「我的信息」是整份覆盖提交，不能把另一条链路存的头像顺手清掉。"""
    token = (await login(client))["token"]
    auth = {"Authorization": f"Bearer {token}"}

    await client.put("/api/users/me/avatar", json={"avatarKey": TEST_AVATAR_KEY}, headers=auth)
    r = await client.put("/api/users/me", json={"memberName": "陈女士"}, headers=auth)
    assert r.status_code == 200, r.text

    async with pool.connection() as conn:
        row = await (
            await conn.execute("SELECT avatar_key FROM users WHERE token = %s", (token,))
        ).fetchone()
    assert row["avatar_key"] == TEST_AVATAR_KEY


# ---------------------------------------------------------------- 鉴权


async def test_me_needs_a_token(client):
    r = await client.get("/api/users/me")
    assert r.status_code == 401
    assert r.json()["ok"] is False


async def test_a_wrong_token_is_rejected(client):
    r = await client.get("/api/users/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_an_expired_token_is_rejected(client):
    token = (await login(client))["token"]
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE users SET token_expires_at = now() - interval '1 day' WHERE token = %s",
            (token,),
        )
    r = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


async def test_a_banned_user_cannot_use_their_token(client):
    token = (await login(client))["token"]
    async with pool.connection() as conn:
        await conn.execute("UPDATE users SET status = 'banned' WHERE token = %s", (token,))
    r = await client.get("/api/users/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# ---------------------------------------------------------------- 预约归属


async def test_an_appointment_submitted_while_logged_in_belongs_to_the_user(client):
    body = await login(client)
    auth = {"Authorization": f"Bearer {body['token']}"}
    form = {
        "name": "测试用户",
        "phone": "13559990001",
        "visitorType": "设计师",
        "visitDate": "2099-01-01",
        "partySize": 2,
        "purpose": "展厅参观",
    }
    created = await client.post("/api/appointments", json=form, headers=auth)
    assert created.status_code == 201, created.text

    mine = await client.get("/api/users/me/appointments", headers=auth)
    assert mine.status_code == 200
    assert [item["phone"] for item in mine.json()["items"]] == ["13559990001"]


async def test_an_appointment_without_a_token_still_goes_through(client):
    form = {
        "name": "路人",
        "phone": "13559990002",
        "visitorType": "设计师",
        "visitDate": "2099-01-01",
        "partySize": 2,
        "purpose": "展厅参观",
    }
    r = await client.post("/api/appointments", json=form)
    assert r.status_code == 201

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT user_id FROM appointments WHERE phone = %s", ("13559990002",)
            )
        ).fetchone()
        await conn.execute("DELETE FROM appointments WHERE phone = %s", ("13559990002",))
    assert row["user_id"] is None

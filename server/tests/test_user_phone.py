"""微信手机号快速验证接口测试。

不真连微信：wxphone.get_phone_number 用假的替掉，只验从「拿到号码」往后的配额、
回写与错误语义。真实的 HTTP 交互单独在最后那一组里用假的 _post / AsyncClient 验。

造的数据都用 TEST_OPENID_PREFIX 开头的 openid，跑完清干净；user_phone_quota 挂着
ON DELETE CASCADE，删用户时跟着走，不用单独清。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import users as users_module
from app import wxphone
from app.db import pool
from app.main import app

TEST_OPENID = "test_phone_openid_1"
PHONE = "13800138000"
OTHER_PHONE = "13900139000"


def fake_session(openid: str):
    async def code2session(code: str, appid: str = "", secret: str = "") -> dict:
        return {"openid": openid, "unionid": None, "session_key": "fake-session-key"}

    return code2session


@pytest.fixture(autouse=True)
def wechat(monkeypatch):
    monkeypatch.setattr(users_module, "code2session", fake_session(TEST_OPENID))


async def _clean() -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM users WHERE openid = %s", (TEST_OPENID,))


@pytest.fixture
async def client():
    await _clean()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await _clean()


async def login(client) -> dict:
    r = await client.post("/api/auth/login", json={"code": "fake-code"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def grant(monkeypatch, phone: str = PHONE) -> None:
    async def get_phone_number(code: str) -> str:
        return phone

    monkeypatch.setattr(wxphone, "get_phone_number", get_phone_number)


async def read_phone(client, headers) -> str:
    r = await client.get("/api/users/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["user"]["phone"]


async def quota_row() -> dict | None:
    async with pool.connection() as conn:
        return await (
            await conn.execute(
                """
                SELECT q.quota_date, q.used
                  FROM user_phone_quota q
                  JOIN users u ON u.id = q.user_id
                 WHERE u.openid = %s
                """,
                (TEST_OPENID,),
            )
        ).fetchone()


# ---------------------------------------------------------------- 换号与回写


async def test_returns_the_plain_number(client, monkeypatch):
    """返回明文，不脱敏——四个入口拿到之后都要填进表单再提交。"""
    headers = await login(client)
    grant(monkeypatch)

    r = await client.post("/api/users/me/phone", json={"code": "wx-code"}, headers=headers)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "phone": PHONE}


async def test_fills_in_a_blank_profile_phone(client, monkeypatch):
    headers = await login(client)
    assert await read_phone(client, headers) == ""
    grant(monkeypatch)

    await client.post("/api/users/me/phone", json={"code": "wx-code"}, headers=headers)

    assert await read_phone(client, headers) == PHONE


async def test_never_overwrites_a_phone_the_user_already_has(client, monkeypatch):
    """用户可能在给家人下单。覆盖会静默改掉他自己的号，而他不会察觉。"""
    headers = await login(client)
    r = await client.put("/api/users/me", json={"phone": OTHER_PHONE}, headers=headers)
    assert r.status_code == 200, r.text
    grant(monkeypatch)

    r = await client.post("/api/users/me/phone", json={"code": "wx-code"}, headers=headers)

    # 拿到的还是这次授权的号（当前表单要用它），但资料里那个一动不动
    assert r.json()["phone"] == PHONE
    assert await read_phone(client, headers) == OTHER_PHONE


# ---------------------------------------------------------------- 鉴权与入参


async def test_requires_a_login(client, monkeypatch):
    grant(monkeypatch)
    r = await client.post("/api/users/me/phone", json={"code": "wx-code"})
    assert r.status_code == 401


async def test_rejects_a_blank_code(client, monkeypatch):
    """入参校验统一被 main.py 的 RequestValidationError 处理器收成 400 + 中文 message。"""
    headers = await login(client)
    grant(monkeypatch)
    r = await client.post("/api/users/me/phone", json={"code": "  "}, headers=headers)
    assert r.status_code == 400
    assert r.json()["ok"] is False


# ---------------------------------------------------------------- 配额


async def test_counts_every_call_against_the_daily_quota(client, monkeypatch):
    headers = await login(client)
    grant(monkeypatch)

    for _ in range(3):
        await client.post("/api/users/me/phone", json={"code": "wx-code"}, headers=headers)

    row = await quota_row()
    assert row is not None and row["used"] == 3


async def test_refuses_once_the_daily_quota_is_used_up(client, monkeypatch):
    """微信那个接口按次收费，超额必须直接 429，不能继续往微信打。"""
    headers = await login(client)
    calls = []

    async def get_phone_number(code: str) -> str:
        calls.append(code)
        return PHONE

    monkeypatch.setattr(wxphone, "get_phone_number", get_phone_number)
    monkeypatch.setattr(users_module, "PHONE_DAILY_QUOTA", 2)

    for _ in range(2):
        r = await client.post("/api/users/me/phone", json={"code": "wx"}, headers=headers)
        assert r.status_code == 200, r.text

    r = await client.post("/api/users/me/phone", json={"code": "wx"}, headers=headers)

    assert r.status_code == 429
    # 错误信封统一是 {ok, message}，见 main.py 的 on_http_error
    assert "手动输入" in r.json()["message"]
    # 关键：超额那一次**没有**打到微信，否则限频等于没限
    assert len(calls) == 2


async def test_a_failed_call_still_burns_quota(client, monkeypatch):
    """失败不退配额：失败的调用同样可能计费，退了等于给重试留一个无上限的口子。"""
    headers = await login(client)

    async def get_phone_number(code: str) -> str:
        raise wxphone.PhoneError("获取失败，请手动输入", "errcode=40029")

    monkeypatch.setattr(wxphone, "get_phone_number", get_phone_number)

    r = await client.post("/api/users/me/phone", json={"code": "wx"}, headers=headers)

    assert r.status_code == 502
    row = await quota_row()
    assert row is not None and row["used"] == 1


async def test_quota_resets_on_a_new_day(client, monkeypatch):
    """跨天靠写入时的 CASE 归零，不靠定时任务。"""
    headers = await login(client)
    grant(monkeypatch)
    await client.post("/api/users/me/phone", json={"code": "wx"}, headers=headers)

    # 把这一行伪造成昨天的、且已经用满
    async with pool.connection() as conn:
        await conn.execute(
            """
            UPDATE user_phone_quota
               SET quota_date = quota_date - 1, used = 999
             WHERE user_id IN (SELECT id FROM users WHERE openid = %s)
            """,
            (TEST_OPENID,),
        )

    r = await client.post("/api/users/me/phone", json={"code": "wx"}, headers=headers)

    assert r.status_code == 200, r.text
    row = await quota_row()
    assert row is not None and row["used"] == 1


# ---------------------------------------------------------------- 微信侧交互


async def test_takes_the_number_without_the_country_code(monkeypatch):
    """必须取 purePhoneNumber：phoneNumber 带 +86，落库过不了 users.phone 的 CHECK。"""

    async def _post(code: str) -> dict:
        return {
            "errcode": 0,
            "phone_info": {
                "phoneNumber": "+8613800138000",
                "purePhoneNumber": PHONE,
                "countryCode": "86",
            },
        }

    monkeypatch.setattr(wxphone.wechat_token, "configured", lambda: True)
    monkeypatch.setattr(wxphone, "_post", _post)

    assert await wxphone.get_phone_number("wx-code") == PHONE


async def test_raises_on_a_wechat_error_instead_of_returning_blank(monkeypatch):
    """禁止静默兜底：微信报错就抛，不能返回空串当作「没拿到就算了」。"""

    async def _post(code: str) -> dict:
        return {"errcode": 40029, "errmsg": "invalid code"}

    monkeypatch.setattr(wxphone.wechat_token, "configured", lambda: True)
    monkeypatch.setattr(wxphone, "_post", _post)

    with pytest.raises(wxphone.PhoneError) as exc:
        await wxphone.get_phone_number("wx-code")

    assert exc.value.message == "获取失败，请手动输入"
    assert "40029" in exc.value.detail


async def test_raises_when_the_reply_has_no_number(monkeypatch):
    async def _post(code: str) -> dict:
        return {"errcode": 0, "phone_info": {}}

    monkeypatch.setattr(wxphone.wechat_token, "configured", lambda: True)
    monkeypatch.setattr(wxphone, "_post", _post)

    with pytest.raises(wxphone.PhoneError):
        await wxphone.get_phone_number("wx-code")


async def test_retries_once_when_the_access_token_was_kicked_out(monkeypatch):
    """40001 是 token 被别处顶掉。作废缓存重换一个再试一次，且只试一次。"""
    tokens: list[bool] = []
    invalidated: list[bool] = []

    async def get_token(*, force: bool = False) -> str:
        tokens.append(force)
        return "tok"

    class FakeResponse:
        def json(self) -> dict:
            if len(tokens) == 1:
                return {"errcode": 40001, "errmsg": "invalid credential"}
            return {"errcode": 0, "phone_info": {"purePhoneNumber": PHONE}}

    class FakeClient:
        def __init__(self, **kwargs) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def post(self, url, params=None, json=None) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(wxphone.wechat_token, "configured", lambda: True)
    monkeypatch.setattr(wxphone.wechat_token, "get_token", get_token)
    monkeypatch.setattr(wxphone.wechat_token, "invalidate", lambda: invalidated.append(True))
    monkeypatch.setattr(wxphone.httpx, "AsyncClient", FakeClient)

    assert await wxphone.get_phone_number("wx-code") == PHONE
    assert tokens == [False, True]
    assert invalidated == [True]


async def test_says_which_variable_is_missing_when_unconfigured(monkeypatch):
    monkeypatch.setattr(wxphone.wechat_token, "configured", lambda: False)

    with pytest.raises(wxphone.PhoneError) as exc:
        await wxphone.get_phone_number("wx-code")

    assert "WX_APPID" in exc.value.detail


def test_masks_the_number_for_logs():
    assert wxphone.mask(PHONE) == "138****8000"
    assert wxphone.mask("") == "***"
    assert wxphone.mask("139") == "***"

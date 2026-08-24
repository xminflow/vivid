"""支付回调的验签前置校验。不连微信、不连库。

这些用例守的是**一个无鉴权的公网入口**。`_precheck_callback` 看着只是几个
字符串比较，但它挡掉的每一条都对应一个具体的、不需要任何凭据就能触发的问题
（见该函数的注释）。少一条都不会有任何报错，只会在某天变成一次故障。
"""

import time

import pytest

from app import wxpay

SERIAL = "PUB_KEY_ID_TEST_0001"


def headers(**overrides) -> dict:
    """一份形状正确的回调头。用例只改自己关心的那一项。"""
    base = {
        "wechatpay-signature-type": "WECHATPAY2-SHA256-RSA2048",
        "wechatpay-serial": SERIAL,
        "wechatpay-timestamp": str(int(time.time())),
        "wechatpay-nonce": "abcdef123456",
        "wechatpay-signature": "c2lnbmF0dXJl",
    }
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture(autouse=True)
def public_key_id(monkeypatch):
    monkeypatch.setattr(wxpay, "PUBLIC_KEY_ID", SERIAL)


def test_a_well_formed_callback_passes_precheck():
    assert wxpay._precheck_callback(headers()) == ""


def test_rejects_an_unknown_certificate_serial():
    """**最重要的一条**。SDK 在序列号对不上时会走「下载平台证书」那条分支，
    也就是任何人带一个乱写的序列号打过来，都能让我们主动向微信发一次 HTTPS
    请求：既消耗 /v3/certificates 的频率配额（打爆了真实支付跟着挂），
    又占住 anyio 的阻塞线程池（占满之后连密码校验都要排队）。
    这一条把那件事变成一次字符串比较。"""
    rejected = wxpay._precheck_callback(headers(**{"wechatpay-serial": "SOMETHING_ELSE"}))
    assert "序列号" in rejected


def test_rejects_a_missing_signature_type():
    """SDK 在这里是直接 raise Exception，落到调用方就是一次 logger.exception
    打整条堆栈——一个不需要凭据就能刷满日志文件的口子。"""
    rejected = wxpay._precheck_callback(headers(**{"wechatpay-signature-type": None}))
    assert "签名类型" in rejected


def test_rejects_a_wrong_signature_type():
    rejected = wxpay._precheck_callback(
        headers(**{"wechatpay-signature-type": "WECHATPAY2-SHA256-RSA4096"})
    )
    assert "签名类型" in rejected


def test_rejects_a_stale_timestamp():
    """SDK 只验签名，**不看时间窗口**（utils.rsa_verify 里没有任何时间比较）。
    补在我们这一层，否则一份验过签的请求体可以被无限期重放。"""
    old = str(int(time.time()) - wxpay.CALLBACK_MAX_AGE_SECONDS - 60)
    rejected = wxpay._precheck_callback(headers(**{"wechatpay-timestamp": old}))
    assert "时间戳" in rejected


def test_rejects_a_timestamp_from_the_future():
    """未来的时间戳同样要挡：签名串里带着它，放行等于允许预先构造。"""
    ahead = str(int(time.time()) + wxpay.CALLBACK_MAX_AGE_SECONDS + 60)
    assert "时间戳" in wxpay._precheck_callback(headers(**{"wechatpay-timestamp": ahead}))


def test_accepts_a_timestamp_inside_the_window():
    recent = str(int(time.time()) - wxpay.CALLBACK_MAX_AGE_SECONDS + 30)
    assert wxpay._precheck_callback(headers(**{"wechatpay-timestamp": recent})) == ""


def test_rejects_a_non_numeric_timestamp():
    assert "时间戳" in wxpay._precheck_callback(headers(**{"wechatpay-timestamp": "不是数字"}))


def test_rejects_a_missing_signature():
    assert "签名" in wxpay._precheck_callback(headers(**{"wechatpay-signature": None}))


def test_rejects_a_missing_nonce():
    assert "随机串" in wxpay._precheck_callback(headers(**{"wechatpay-nonce": None}))


# ---------------------------------------------------------------------------
# 验签之后：这条通知是发给我们的吗


class _FakeClient:
    """只实现 callback()，返回我们指定的 result。"""

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def callback(self, headers, body):
        self.calls += 1
        return self.result


@pytest.fixture
def fake_client(monkeypatch):
    """把 client() 换成假的，同时给 MCHID / APPID 一个已知值。"""
    holder: dict = {"client": None}

    def make(resource: dict | None):
        holder["client"] = _FakeClient({"resource": resource} if resource is not None else None)
        return holder["client"]

    monkeypatch.setattr(wxpay, "MCHID", "1600000000")
    monkeypatch.setattr(wxpay, "APPID", "wxtestappid")
    monkeypatch.setattr(wxpay, "client", lambda: holder["client"])
    return make


def a_resource(**overrides) -> dict:
    base = {
        "mchid": "1600000000",
        "appid": "wxtestappid",
        "out_trade_no": "AX20260812000001",
        "transaction_id": "42000123",
        "trade_state": "SUCCESS",
        "amount": {"total": 100000},
    }
    base.update(overrides)
    return base


async def test_verify_returns_the_resource_when_everything_matches(fake_client):
    fake_client(a_resource())
    resource = await wxpay.verify_callback(headers(), "{}")
    assert resource is not None
    assert resource["out_trade_no"] == "AX20260812000001"


async def test_verify_never_calls_the_sdk_when_precheck_fails(fake_client):
    """前置校验不过时**一次也不能进 SDK**——进去就等于触发那次证书下载。"""
    client = fake_client(a_resource())
    result = await wxpay.verify_callback(headers(**{"wechatpay-serial": "WRONG"}), "{}")
    assert result is None
    assert client.calls == 0, "序列号不对时不该调用 SDK"


async def test_verify_rejects_another_merchants_notification(fake_client):
    """验签证明它来自微信，这一条确认它是发给**我们**的。
    挡的是配置错误：某台机器的 .env 混用了另一个商户号时，
    没有这一条的话两个商户的订单会静默地互相结算。"""
    fake_client(a_resource(mchid="1699999999"))
    assert await wxpay.verify_callback(headers(), "{}") is None


async def test_verify_rejects_another_appid(fake_client):
    fake_client(a_resource(appid="wxsomeoneelse"))
    assert await wxpay.verify_callback(headers(), "{}") is None


async def test_verify_returns_none_when_the_signature_does_not_check_out(fake_client):
    fake_client(None)
    assert await wxpay.verify_callback(headers(), "{}") is None


async def test_verify_normalises_header_case(fake_client):
    """Starlette 给的是小写，但这个函数的调用方不止一个。
    大小写不统一的表现是「验签偶尔失败」，极难查。"""
    fake_client(a_resource())
    upper = {k.title(): v for k, v in headers().items()}
    assert await wxpay.verify_callback(upper, "{}") is not None

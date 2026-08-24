"""发货通知（订阅消息）。不连微信。

这一层的核心约定只有一条：**发不出通知绝不能让发货失败**。
货已经交给快递了，一条通知发不出去不该让运营看到一个失败的操作。

与发货信息录入的区别也在这里——那件事失败要在界面上警告运营
（微信的硬要求，不做会影响交易权限），这件事失败只记日志。
"""

import pytest

from app import wxnotice


@pytest.fixture
def sent(monkeypatch):
    """打桩发送，记录发出去的 payload 和要模拟的应答。"""
    state = {"posted": [], "reply": {"errcode": 0}}

    async def _post(payload):
        state["posted"].append(payload)
        return state["reply"]

    monkeypatch.setattr(wxnotice, "SHIP_TEMPLATE_ID", "TPL_TEST_0001")
    monkeypatch.setattr(wxnotice.wechat_token, "configured", lambda: True)
    monkeypatch.setattr(wxnotice, "_post", _post)
    return state


async def send(**overrides):
    body = {
        "openid": "oTestUser",
        "order_id": "81000000000000001",
        "order_no": "AX20260812000001",
        "title": "云朵沙发",
        "company": "SF",
        "tracking_no": "SF123456789",
    }
    body.update(overrides)
    await wxnotice.send_ship_notice(**body)


async def test_sends_with_the_configured_template(sent):
    await send()
    assert len(sent["posted"]) == 1
    payload = sent["posted"][0]
    assert payload["template_id"] == "TPL_TEST_0001"
    assert payload["touser"] == "oTestUser"


async def test_links_to_that_order(sent):
    """点通知要落在这一单的详情页，不是订单列表——用户点进来就是想看这一单。"""
    await send(order_id="81000000000000009")
    assert sent["posted"][0]["page"] == "pages/order/order?id=81000000000000009"


async def test_fills_every_template_field(sent):
    """模板缺字段微信会整条拒发（47003）。"""
    await send()
    data = sent["posted"][0]["data"]
    assert set(data) == set(wxnotice._FIELDS)
    assert all("value" in v for v in data.values())


async def test_clips_long_values(sent):
    """thing 类字段有 20 字符上限，超了整条发不出去。
    截断而不是报错——用户收到一条标题被截短的通知，比收不到强。"""
    await send(title="超级无敌加长版意大利真皮转角沙发带电动脚踏和杯架")
    value = sent["posted"][0]["data"]["thing5"]["value"]
    assert len(value) <= 20


async def test_fills_a_readable_placeholder_for_non_express(sent):
    """同城配送和无需物流没有快递公司与运单号，但模板字段不能留空。"""
    await send(company="", tracking_no="")
    data = sent["posted"][0]["data"]
    assert data["thing4"]["value"] == "无需快递"
    # 物流编号是 character_string 类型，只能数字/字母/符号——占位不能是中文
    assert data["character_string3"]["value"] == "-"


async def test_character_string_fields_never_contain_chinese(sent):
    """**这一条挡的是一整类静默失败**。

    character_string 类字段只接受数字/字母/符号，混进一个中文字符整条被 47003
    拒掉。而「没有运单号」恰恰是两档发货方式的常态（同城配送、无需物流），
    占位写成「无」的话，这两档的通知永远发不出去，且只在日志里留一行。
    """
    await send(company="", tracking_no="")
    data = sent["posted"][0]["data"]
    for key in ("character_string2", "character_string3"):
        value = data[key]["value"]
        assert value.isascii(), f"{key} 含非 ASCII 字符：{value!r}"
        assert value, f"{key} 不能为空"


async def test_ship_time_is_east_eight_and_from_the_order(sent):
    """发货时间取库里的 shipped_at，并按东八区显示。

    不用机器本地时区：容器的 TZ 由 Dockerfile 和 compose 各设一次，
    漏一处就会给用户发出一个差 8 小时的发货时间。
    """
    from datetime import datetime, timezone

    # 2026-08-13 02:30 UTC == 10:30 北京时间
    await send(shipped_at=datetime(2026, 8, 13, 2, 30, tzinfo=timezone.utc))
    assert sent["posted"][0]["data"]["time1"]["value"] == "2026-08-13 10:30"


async def test_ship_time_falls_back_to_now(sent):
    """没传 shipped_at 也不能把模板字段留空——留空是整条被拒。"""
    await send()
    assert sent["posted"][0]["data"]["time1"]["value"]


async def test_user_refusal_is_not_an_error(sent):
    """43101 = 用户没授权或额度用完。这是常态，不该被当成故障。"""
    sent["reply"] = {"errcode": 43101, "errmsg": "user refuse to accept the msg"}
    await send()  # 不抛就算通过


async def test_other_errors_do_not_raise_either(sent):
    """47003 通常是 _FIELDS 的键名与后台那个模板对不上。
    要记日志让人看得到，但同样不能抛——发货已经完成了。"""
    sent["reply"] = {"errcode": 47003, "errmsg": "argument invalid"}
    await send()


async def test_network_failure_does_not_raise(sent, monkeypatch):
    """微信不通同样不该影响发货。"""

    async def boom(payload):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(wxnotice, "_post", boom)
    await send()


async def test_does_nothing_when_the_template_is_not_configured(sent, monkeypatch):
    """没配模板就整个功能关掉，一次请求都不该发出去。"""
    monkeypatch.setattr(wxnotice, "SHIP_TEMPLATE_ID", "")
    await send()
    assert sent["posted"] == []

"""安玺·集 发货与退款（后台）。需要库可连，**不连微信**。

微信那两套接口整体打桩——它们分属两个鉴权体系，在这里重跑没有意义：
  发货信息录入  api.weixin.qq.com  + access_token   → app/wxship.py
  退款          api.mch.weixin.qq.com + 商户私钥签名 → app/wxpay.py

这里验的是我们自己的编排，重点在几条「顺序」和「失败了怎么办」：
  * 发货必须**先落库再回传微信**，回传失败不回滚发货
  * 退款必须**先调微信再改状态**，微信失败就不改
  * 回传失败的单要能被查出来（shipping_uploaded_at IS NULL）
  * 退款只有超管能做
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import users as users_module
from app import wxpay, wxship
from app.db import pool
from app.main import app

PREFIX = "ZZ测试"
TEST_OPENID = "test_openid_ship"
IMAGE_A = "static/shop/20260101/testa.jpg"

ADDRESS = {
    "receiver": "陈女士",
    "phone": "13612345678",
    "province": "上海市",
    "city": "上海市",
    "district": "静安区",
    "detail": "南京西路 1000 号",
}


async def clean() -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM shop_orders WHERE user_id IN"
            " (SELECT id FROM users WHERE openid LIKE %s)",
            (f"{TEST_OPENID}%",),
        )
        await conn.execute(
            """
            DELETE FROM shop_products
             WHERE title LIKE %s
                OR category_id IN (SELECT id FROM shop_categories WHERE name LIKE %s)
            """,
            (f"{PREFIX}%", f"{PREFIX}%"),
        )
        await conn.execute("DELETE FROM shop_categories WHERE name LIKE %s", (f"{PREFIX}%",))
        await conn.execute("DELETE FROM users WHERE openid LIKE %s", (f"{TEST_OPENID}%",))


@pytest.fixture
async def client(auth_headers):
    await clean()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=auth_headers) as c:
        yield c
    await clean()


def fake_session(openid: str):
    # 签名要跟真的一致：code2session 现在按小程序收 appid/secret（见 app/wechat.py）
    async def code2session(code: str, appid: str = "", secret: str = "") -> dict:
        return {"openid": openid, "unionid": None, "session_key": "fake-session-key"}

    return code2session


@pytest.fixture
async def shopper(client, monkeypatch):
    monkeypatch.setattr(users_module, "code2session", fake_session(f"{TEST_OPENID}1"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"code": "fake-code"})
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


@pytest.fixture
def wechat(monkeypatch):
    """把微信两套接口都打桩。测试通过改 state 来模拟失败。"""
    state = {"uploaded": [], "upload_fails": False, "refunded": [], "refund_fails": False}

    async def upload_shipping_info(**kwargs):
        if state["upload_fails"]:
            raise wxship.ShipError("回传微信失败（10060005: 物流类型有误）")
        state["uploaded"].append(kwargs)

    async def refund(**kwargs):
        if state["refund_fails"]:
            raise wxpay.PayError("退款失败")
        state["refunded"].append(kwargs)
        return {"refund_id": "WXREFUND_TEST_1"}

    # 下单也要打桩：configured() 一旦是 True，create_order 就会去向微信下单，
    # 而本地没有支付配置，SDK 会拿着 None 私钥去签名
    async def jsapi_order(**kwargs):
        return {"timeStamp": "1", "nonceStr": "n", "package": "prepay_id=x",
                "signType": "RSA", "paySign": "sig"}

    # 发货前会查一次「是不是已在系统外退款」，同样要打桩。
    # 默认回 SUCCESS（没退款），要模拟系统外退款的用例改用 wechat_query fixture
    async def query_order(out_trade_no):
        state["queried"].append(out_trade_no)
        return {"trade_state": "SUCCESS", "transaction_id": "WX1",
                "amount": {"total": 100000}}

    state["queried"] = []
    monkeypatch.setattr(wxship, "configured", lambda: True)
    monkeypatch.setattr(wxship, "upload_shipping_info", upload_shipping_info)
    monkeypatch.setattr(wxpay, "configured", lambda: True)
    monkeypatch.setattr(wxpay, "jsapi_order", jsapi_order)
    monkeypatch.setattr(wxpay, "query_order", query_order)
    monkeypatch.setattr(wxpay, "refund", refund)
    return state


async def a_paid_order(client, shopper) -> str:
    """造一笔已支付的订单，返回 id。支付链路本身由 test_shop_pay.py 覆盖，
    这里直接把状态改成待发货。"""
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}发货分类"})
    category_id = r.json()["id"]
    r = await client.post(
        "/api/admin/shop/products",
        json={
            "categoryId": category_id,
            "title": f"{PREFIX}发货沙发",
            "summary": "",
            "priceCents": 100000,
            "images": [IMAGE_A],
            "detailImages": [],
            "params": [],
            "sortOrder": 0,
        },
    )
    product_id = r.json()["id"]
    await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "active"})

    r = await shopper.post("/api/shop/addresses", json=ADDRESS)
    address_id = r.json()["item"]["id"]
    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    order_id = r.json()["order"]["id"]

    async with pool.connection() as conn:
        await conn.execute(
            """
            UPDATE shop_orders
               SET status = 'pending_ship', paid_at = now(), transaction_id = %s
             WHERE id = %s
            """,
            (f"WXTEST{order_id}", int(order_id)),
        )
    return order_id


# ---------------------------------------------------------------------------
# 发货


async def test_shipping_marks_the_order_and_tells_wechat(client, shopper, wechat):
    order_id = await a_paid_order(client, shopper)

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship",
        json={"shippingType": "express", "shippingCompany": "SF", "trackingNo": "SF123456789"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["warning"] == ""

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    body = detail.json()["order"]
    assert body["status"] == "pending_receive"
    assert body["shipping"]["trackingNo"] == "SF123456789"
    assert body["shipping"]["shippedAt"] is not None

    assert len(wechat["uploaded"]) == 1
    assert wechat["uploaded"][0]["shipping_type"] == "express"
    assert wechat["uploaded"][0]["tracking_no"] == "SF123456789"


async def test_shipping_survives_a_failing_wechat(client, shopper, wechat):
    """回传失败**不能回滚发货**——货可能已经交给快递了。
    但要在返回里带警告，并且这笔单必须能被查出来重试。"""
    wechat["upload_fails"] = True
    order_id = await a_paid_order(client, shopper)

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship",
        json={"shippingType": "express", "shippingCompany": "SF", "trackingNo": "SF999"},
    )
    assert r.status_code == 200, r.text
    assert "回传微信失败" in r.json()["warning"]

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "pending_receive", "发货本身要成功"

    # 靠这个条件能把没传成功的单捞出来
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT count(*) AS n FROM shop_orders
                 WHERE id = %s AND shipped_at IS NOT NULL AND shipping_uploaded_at IS NULL
                """,
                (int(order_id),),
            )
        ).fetchone()
    assert row["n"] == 1


async def test_local_delivery_needs_no_tracking(client, shopper, wechat):
    """卖家具走专线或自送时没有运单号，这两档是刚需。"""
    order_id = await a_paid_order(client, shopper)
    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "local"}
    )
    assert r.status_code == 200, r.text
    assert wechat["uploaded"][0]["shipping_type"] == "local"


async def test_express_without_tracking_is_rejected(client, shopper, wechat):
    order_id = await a_paid_order(client, shopper)
    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship",
        json={"shippingType": "express", "shippingCompany": "SF"},
    )
    assert r.status_code == 400
    assert "运单号" in r.json()["message"]


async def test_cannot_ship_an_unpaid_order(client, shopper, wechat):
    """没付钱就发货是真金白银的损失。"""
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}未付分类"})
    category_id = r.json()["id"]
    r = await client.post(
        "/api/admin/shop/products",
        json={"categoryId": category_id, "title": f"{PREFIX}未付沙发", "summary": "",
              "priceCents": 100, "images": [IMAGE_A], "detailImages": [], "params": [],
              "sortOrder": 0},
    )
    product_id = r.json()["id"]
    await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "active"})
    r = await shopper.post("/api/shop/addresses", json=ADDRESS)
    r = await shopper.post(
        "/api/shop/orders",
        json={"addressId": r.json()["item"]["id"], "source": "direct",
              "items": [{"productId": product_id, "quantity": 1}]},
    )
    order_id = r.json()["order"]["id"]  # 停在 pending_pay

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"}
    )
    assert r.status_code == 409


async def test_cannot_ship_twice(client, shopper, wechat):
    order_id = await a_paid_order(client, shopper)
    await client.post(f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"})
    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"}
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 改运单号


async def test_updating_tracking_reuploads_to_wechat(client, shopper, wechat):
    """填错了要能改，改完必须重传——微信那边存的还是旧单号，
    不重传等于用户查不到物流。"""
    order_id = await a_paid_order(client, shopper)
    await client.post(
        f"/api/admin/shop/orders/{order_id}/ship",
        json={"shippingType": "express", "shippingCompany": "SF", "trackingNo": "WRONG"},
    )
    assert len(wechat["uploaded"]) == 1

    r = await client.put(
        f"/api/admin/shop/orders/{order_id}/ship",
        json={"shippingCompany": "JD", "trackingNo": "JD888"},
    )
    assert r.status_code == 200, r.text
    assert len(wechat["uploaded"]) == 2, "改完要重新回传"
    assert wechat["uploaded"][1]["tracking_no"] == "JD888"

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["shipping"]["trackingNo"] == "JD888"


async def test_cannot_update_tracking_of_a_non_express_order(client, shopper, wechat):
    order_id = await a_paid_order(client, shopper)
    await client.post(f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"})
    r = await client.put(
        f"/api/admin/shop/orders/{order_id}/ship",
        json={"shippingCompany": "SF", "trackingNo": "SF1"},
    )
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# 退款


async def test_refund_calls_wechat_then_marks_the_order(client, shopper, wechat):
    order_id = await a_paid_order(client, shopper)

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "客户不要了"}
    )
    assert r.status_code == 200, r.text

    assert len(wechat["refunded"]) == 1
    # 只做整退，金额取订单的，请求里没有金额字段
    assert wechat["refunded"][0]["total_cents"] == 100000

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    body = detail.json()["order"]
    assert body["status"] == "refunded"
    assert body["refund"]["reason"] == "客户不要了"
    assert body["refund"]["refundId"] == "WXREFUND_TEST_1"


async def test_refund_does_not_change_status_when_wechat_fails(client, shopper, wechat):
    """微信退款失败而我们置成已退款，用户看到「已退款」却没收到钱——
    那是最难解释的一种故障。"""
    wechat["refund_fails"] = True
    order_id = await a_paid_order(client, shopper)

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "测试"}
    )
    assert r.status_code == 502

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "pending_ship"


async def test_refund_reason_is_required(client, shopper, wechat):
    """退款不可逆，事后要说得清为什么退。"""
    order_id = await a_paid_order(client, shopper)
    r = await client.post(f"/api/admin/shop/orders/{order_id}/refund", json={"reason": ""})
    assert r.status_code == 400


async def test_cannot_refund_an_unpaid_order(client, shopper, wechat):
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}退款分类"})
    category_id = r.json()["id"]
    r = await client.post(
        "/api/admin/shop/products",
        json={"categoryId": category_id, "title": f"{PREFIX}退款沙发", "summary": "",
              "priceCents": 100, "images": [IMAGE_A], "detailImages": [], "params": [],
              "sortOrder": 0},
    )
    product_id = r.json()["id"]
    await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "active"})
    r = await shopper.post("/api/shop/addresses", json=ADDRESS)
    r = await shopper.post(
        "/api/shop/orders",
        json={"addressId": r.json()["item"]["id"], "source": "direct",
              "items": [{"productId": product_id, "quantity": 1}]},
    )
    r = await client.post(
        f"/api/admin/shop/orders/{r.json()['order']['id']}/refund", json={"reason": "测试"}
    )
    assert r.status_code == 409


@pytest.mark.parametrize("ship_first", [False, True])
async def test_refund_works_before_and_after_shipping(client, shopper, wechat, ship_first):
    """**已发货的单照样能退款**。

    发出去的货追不回来是运营要面对的问题（联系客户退货），不是系统该替他决定的事。
    系统能做的是把钱退掉并留下记录。写成参数化是因为这两条路径的区别只有
    「退款时订单处于哪个状态」，而那正是这条用例要盯住的东西。
    """
    order_id = await a_paid_order(client, shopper)
    if ship_first:
        r = await client.post(
            f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"}
        )
        assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "客户要求退货"}
    )
    assert r.status_code == 200, r.text

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    body = detail.json()["order"]
    assert body["status"] == "refunded"
    assert body["refund"]["reason"] == "客户要求退货"
    # 已发货的单退款后，物流信息仍然留着——那是发生过的事实，不该被抹掉
    if ship_first:
        assert body["shipping"]["shippedAt"] is not None


async def test_refund_works_on_a_completed_order(client, shopper, wechat):
    """已完成（自动确认收货之后）也能退——售后退货就是这个场景。"""
    from app.shop_pay import sweep_auto_receipt

    order_id = await a_paid_order(client, shopper)
    await client.post(f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"})
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET shipped_at = now() - interval '11 days' WHERE id = %s",
            (int(order_id),),
        )
    await sweep_auto_receipt()

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "completed"

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "售后退货"}
    )
    assert r.status_code == 200, r.text
    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "refunded"


async def test_cannot_refund_twice(client, shopper, wechat):
    order_id = await a_paid_order(client, shopper)
    await client.post(f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "第一次"})
    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "第二次"}
    )
    assert r.status_code == 409
    assert len(wechat["refunded"]) == 1


# ---------------------------------------------------------------------------
# 自动确认收货


async def test_auto_receipt_after_ten_days(client, shopper, wechat):
    from app.shop_pay import sweep_auto_receipt

    order_id = await a_paid_order(client, shopper)
    await client.post(f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"})

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET shipped_at = now() - interval '11 days' WHERE id = %s",
            (int(order_id),),
        )

    n = await sweep_auto_receipt()
    assert n >= 1

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "completed"
    assert detail.json()["order"]["receivedAt"] is not None


async def test_auto_receipt_leaves_fresh_shipments_alone(client, shopper, wechat):
    from app.shop_pay import sweep_auto_receipt

    order_id = await a_paid_order(client, shopper)
    await client.post(f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"})

    await sweep_auto_receipt()

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "pending_receive"


# ---------------------------------------------------------------------------
# 系统外退款：发货前查一次
#
# 退款的正常入口是后台按钮，但商户超管、微信客诉、我们故障期间的应急操作都绕得过去。
# 绕过去的唯一硬损失是「运营把一笔已退款的单发出去」——白送一件货。
#
# 所以查询挂在**发货那一刻**，不做定时轮询：调用量 = 发货次数，与订单存量无关。
# 一度写成每分钟扫 20 笔、每笔每小时一次，那个量正比于订单存量，随业务增长没有上限。


@pytest.fixture
def wechat_query(monkeypatch, wechat):
    """在 wechat 之上再打桩查单，模拟微信说「这笔已退款」。"""
    state = {"trade_state": "SUCCESS", "queried": []}

    async def query_order(out_trade_no):
        state["queried"].append(out_trade_no)
        return {"trade_state": state["trade_state"], "transaction_id": "WX1",
                "amount": {"total": 100000}}

    monkeypatch.setattr(wxpay, "query_order", query_order)
    return state


async def test_shipping_is_blocked_when_refunded_outside(client, shopper, wechat, wechat_query):
    """有人在商户平台退了款，我们的库还是待发货。发货请求必须被拦下，
    并把状态同步过来——放过去就是白送一件货。"""
    order_id = await a_paid_order(client, shopper)
    wechat_query["trade_state"] = "REFUND"

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"}
    )
    assert r.status_code == 409
    assert "退款" in r.json()["message"]

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    body = detail.json()["order"]
    assert body["status"] == "refunded"
    assert "商户平台" in body["refund"]["reason"]
    assert len(wechat["uploaded"]) == 0, "被拦下就不该回传物流信息给微信"


async def test_shipping_queries_wechat_exactly_once(client, shopper, wechat_query):
    """调用量 = 发货次数。一笔单发一次货就查一次，不是每小时一次。"""
    order_id = await a_paid_order(client, shopper)
    wechat_query["trade_state"] = "SUCCESS"

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"}
    )
    assert r.status_code == 200, r.text
    assert len(wechat_query["queried"]) == 1


async def test_shipping_proceeds_when_the_query_fails(client, shopper, wechat_query, monkeypatch):
    """查不到不该阻断发货——微信抖一下不能让运营发不了货。
    真有系统外退款而这次没查到，还有每日账单对账兜底。"""

    async def boom(out_trade_no):
        raise wxpay.PayError("微信超时")

    monkeypatch.setattr(wxpay, "query_order", boom)
    order_id = await a_paid_order(client, shopper)

    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/ship", json={"shippingType": "none"}
    )
    assert r.status_code == 200, r.text

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "pending_receive"


async def test_concurrent_refunds_use_one_refund_number(client, shopper, wechat):
    """**重复退款的回归**。

    老代码是「读状态 → 调微信 → 改状态」，中间没有任何互斥：两个超管同时点，
    两次都读到 pending_ship，各自生成一个新的 out_refund_no 去调微信。

    现在靠的是一条不变量——**同一笔订单永远只用一个 out_refund_no**。
    微信对相同的退款单号 + 相同金额是幂等的，所以哪怕两次调用都发出去了，
    也只会退出去一笔。这里钉住的就是那个「只有一个号」。
    """
    import asyncio

    order_id = await a_paid_order(client, shopper)

    results = await asyncio.gather(
        client.post(f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "甲点的"}),
        client.post(f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "乙点的"}),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, Exception) and r.status_code == 200]
    assert ok, f"至少要有一次成功：{results}"

    refund_nos = {call["out_refund_no"] for call in wechat["refunded"]}
    assert len(refund_nos) == 1, f"同一笔订单只能用一个退款单号，实际用了 {refund_nos}"

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "refunded"


async def test_retrying_a_failed_refund_reuses_the_same_number(client, shopper, wechat):
    """微信那次调用失败了（网络断在半路，钱可能退了也可能没退）。

    重试必须沿用同一个退款单号：换一个新号就是向微信发起**第二笔**退款，
    而第一笔到底成没成功我们并不知道。用同一个号，微信自己会去重。
    """
    order_id = await a_paid_order(client, shopper)

    wechat["refund_fails"] = True
    r = await client.post(
        f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "第一次，会失败"}
    )
    assert r.status_code == 502

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "pending_ship", "微信没退成功就不能改状态"
    claimed = detail.json()["order"]["refund"]["refundId"]
    assert claimed, "失败也要把退款单号留下来，重试时才沿用得上"

    wechat["refund_fails"] = False
    r = await client.post(f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "重试"})
    assert r.status_code == 200, r.text

    assert wechat["refunded"][-1]["out_refund_no"] == claimed, "重试必须沿用同一个号"


async def test_refund_holds_no_connection_while_calling_wechat(client, shopper, wechat, monkeypatch):
    """退款期间不该占着连接池里的连接。

    池只有 5 条（db.py），而向微信退款是一次几百毫秒起步的网络调用。老代码把
    「查订单 → 调微信 → 改状态」整个裹在一个 `pool.connection()` 里，
    微信慢一次就能让几笔并发退款把池抽干，连不相干的接口一起 503。

    验法：把池临时缩到 1 条，然后在打桩的 refund 里再去借一条。
    老结构下这会直接超时——那一条正被退款请求自己占着。
    """
    from app.db import pool

    order_id = await a_paid_order(client, shopper)
    borrowed = {"ok": False}

    async def refund(**kwargs):
        # 退款调用进行中：此刻本请求不该持有任何连接，所以还借得到
        async with pool.connection(timeout=5) as conn:
            await conn.execute("SELECT 1")
        borrowed["ok"] = True
        wechat["refunded"].append(kwargs)
        return {"refund_id": "WXREFUND_TEST_1"}

    monkeypatch.setattr(wxpay, "refund", refund)

    await pool.resize(min_size=1, max_size=1)
    try:
        r = await client.post(
            f"/api/admin/shop/orders/{order_id}/refund", json={"reason": "连接占用检查"}
        )
    finally:
        await pool.resize(min_size=1, max_size=5)

    assert r.status_code == 200, r.text
    assert borrowed["ok"], "退款期间应当还借得到连接"


async def test_shipping_holds_no_connection_while_calling_wechat(
    client, shopper, wechat, monkeypatch
):
    """发货同理：回传物流信息是网络调用，不该占着连接。"""
    from app import wxship
    from app.db import pool

    order_id = await a_paid_order(client, shopper)
    borrowed = {"ok": False}

    async def upload_shipping_info(**kwargs):
        async with pool.connection(timeout=5) as conn:
            await conn.execute("SELECT 1")
        borrowed["ok"] = True
        wechat["uploaded"].append(kwargs)

    monkeypatch.setattr(wxship, "upload_shipping_info", upload_shipping_info)

    await pool.resize(min_size=1, max_size=1)
    try:
        r = await client.post(
            f"/api/admin/shop/orders/{order_id}/ship",
            json={"shippingType": "express", "shippingCompany": "SF", "trackingNo": "SF123456"},
        )
    finally:
        await pool.resize(min_size=1, max_size=5)

    assert r.status_code == 200, r.text
    assert borrowed["ok"], "回传物流信息期间应当还借得到连接"

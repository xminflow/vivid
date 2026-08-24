"""安玺·集 支付编排。需要库可连，**不连微信**。

wxpay 那一层整体打桩：验签、解密、HTTP 都是 SDK 的事，在这里重跑一遍没有意义，
而且真连微信要凭据。这里验的是**我们自己的编排**：

  * 三个入口（回调 / 主动查单 / 超时扫描）最终走同一段状态迁移
  * 幂等：重复回调、回调与查单同时到达，都只迁移一次
  * 金额不符必须拒绝——这是「有人在中间改了金额」的唯一防线
  * 清购物车只对 source='cart' 的单发生，且发生在**支付成功**而不是下单时
  * 未配置支付时下单仍然成功，只是没有 payParams

隔离沿用 test_shop_orders.py：ZZ测试 前缀 + test_openid_pay 用户。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import users as users_module
from app import wxpay
from app.db import pool
from app.main import app

PREFIX = "ZZ测试"
TEST_OPENID = "test_openid_pay"
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
        # 先删台账再删订单：台账按 order_no 关联（故意没有外键——order_not_found
        # 那一类恰恰是「没有对应订单」），订单先删掉就找不到该清哪几条了。
        #
        # 这一步不能省：test_callback_rejects_a_wrong_amount 和
        # test_sweep_stops_retrying_an_order_it_cannot_settle 每跑一次各写一条，
        # 而这些测试打的是**共享的远程开发库**。不清的话那边会积起一堆
        # 看着像真事故的记录，把真正需要人处理的那一条淹掉
        await conn.execute(
            "DELETE FROM payment_anomalies WHERE order_no IN"
            " (SELECT order_no FROM shop_orders WHERE user_id IN"
            "  (SELECT id FROM users WHERE openid LIKE %s))",
            (f"{TEST_OPENID}%",),
        )
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
        assert r.status_code == 200, r.text
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


@pytest.fixture
def paid_stub(monkeypatch):
    """把 wxpay 整体打桩成「已配置，且下单/查单都成功」。

    返回一个 dict，测试可以改 trade_state / total 来模拟不同的微信应答。
    """
    state = {"trade_state": "SUCCESS", "total": None, "transaction_id": "WX_TEST_0001",
             "closed": [], "queried": []}

    async def jsapi_order(**kwargs):
        return {"timeStamp": "1", "nonceStr": "n", "package": "prepay_id=x",
                "signType": "RSA", "paySign": "sig"}

    async def query_order(out_trade_no):
        state["queried"].append(out_trade_no)
        return {
            "trade_state": state["trade_state"],
            "transaction_id": state["transaction_id"],
            "amount": {"total": state["total"]},
        }

    async def close_order(out_trade_no):
        state["closed"].append(out_trade_no)

    monkeypatch.setattr(wxpay, "configured", lambda: True)
    monkeypatch.setattr(wxpay, "jsapi_order", jsapi_order)
    monkeypatch.setattr(wxpay, "query_order", query_order)
    monkeypatch.setattr(wxpay, "close_order", close_order)
    monkeypatch.setattr(wxpay, "NOTIFY_URL", "https://example.test/api/shop/pay/notify")
    return state


async def a_product(client, price_cents: int = 100000) -> str:
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}支付分类"})
    assert r.status_code == 200, r.text
    category_id = r.json()["id"]
    r = await client.post(
        "/api/admin/shop/products",
        json={
            "categoryId": category_id,
            "title": f"{PREFIX}支付沙发",
            "summary": "",
            "priceCents": price_cents,
            "images": [IMAGE_A],
            "detailImages": [],
            "params": [],
            "sortOrder": 0,
        },
    )
    assert r.status_code == 200, r.text
    product_id = r.json()["id"]
    r = await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "active"})
    assert r.status_code == 200, r.text
    return product_id


async def an_order(client, shopper, source: str = "direct", price_cents: int = 100000) -> dict:
    product_id = await a_product(client, price_cents)
    r = await shopper.post("/api/shop/addresses", json=ADDRESS)
    address_id = r.json()["item"]["id"]

    if source == "cart":
        r = await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 1})
        assert r.status_code == 200, r.text

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": source,
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    return {"id": body["order"]["id"], "orderNo": body["order"]["orderNo"],
            "payParams": body.get("payParams"), "productId": product_id}


async def notify(order_no: str, *, total: int, transaction_id: str = "WX_TEST_0001",
                 trade_state: str = "SUCCESS") -> int:
    """打一次回调。验签已经在 wxpay.verify_callback 那层打桩掉了。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/shop/pay/notify", content=b"{}")
    return r.status_code


@pytest.fixture
def callback_stub(monkeypatch, paid_stub):
    """让 verify_callback 直接返回我们指定的 resource。

    真实的验签由 SDK 负责，在这里重跑没有意义——要验的是「验过之后我们怎么处理」。
    """
    payload = {"resource": None}

    async def verify_callback(headers, body):
        return payload["resource"]

    monkeypatch.setattr(wxpay, "verify_callback", verify_callback)
    return payload


# ---------------------------------------------------------------------------
# 未配置支付


async def test_order_still_works_without_pay_config(client, shopper, monkeypatch):
    """没配支付时下单照样成功，只是没有 payParams。

    这条是「不涉及交易的功能必须能在没有支付配置的机器上开发」的保证。
    """
    monkeypatch.setattr(wxpay, "configured", lambda: False)
    order = await an_order(client, shopper)
    assert order["payParams"] is None

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_pay"


async def test_pay_endpoint_says_so_when_not_configured(client, shopper, monkeypatch):
    monkeypatch.setattr(wxpay, "configured", lambda: False)
    order = await an_order(client, shopper)
    r = await shopper.post(f"/api/shop/orders/{order['id']}/pay")
    assert r.status_code == 503
    assert "支付" in r.json()["message"]


# ---------------------------------------------------------------------------
# 下单即拉起支付


async def test_order_returns_pay_params_when_configured(client, shopper, paid_stub):
    order = await an_order(client, shopper)
    assert order["payParams"] is not None
    # 这五个键是 wx.requestPayment 要的，少一个小程序就调不起来
    assert set(order["payParams"]) == {
        "timeStamp", "nonceStr", "package", "signType", "paySign"
    }


async def test_order_survives_a_failing_wechat(client, shopper, paid_stub, monkeypatch):
    """向微信下单失败**不能回滚订单**：订单本身是有效的，
    用户可以在详情页点「去支付」重试。绑进同一个事务的话，
    微信抖一下就会让用户白填一遍地址。"""

    async def boom(**kwargs):
        raise wxpay.PayError("微信挂了")

    monkeypatch.setattr(wxpay, "jsapi_order", boom)
    order = await an_order(client, shopper)
    assert order["payParams"] is None

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_pay"


# ---------------------------------------------------------------------------
# 回调


async def test_callback_marks_the_order_paid(client, shopper, callback_stub):
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_A",
        "amount": {"total": 100000},
    }
    assert await notify(order["orderNo"], total=100000) == 200

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    body = r.json()["order"]
    assert body["status"] == "pending_ship"
    assert body["paidAt"] is not None


async def test_callback_is_idempotent(client, shopper, callback_stub):
    """微信会重投回调。第二次必须什么都不改，也不能报错。"""
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_B",
        "amount": {"total": 100000},
    }
    assert await notify(order["orderNo"], total=100000) == 200
    first = (await shopper.get(f"/api/shop/orders/{order['id']}")).json()["order"]["paidAt"]

    assert await notify(order["orderNo"], total=100000) == 200
    second = (await shopper.get(f"/api/shop/orders/{order['id']}")).json()["order"]["paidAt"]
    assert first == second


async def test_callback_rejects_a_wrong_amount(client, shopper, callback_stub):
    """金额对不上是严重事件：要么建单时算错了，要么有人在中间改了金额。
    自动放行等于认了一笔金额不符的支付。"""
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_C",
        "amount": {"total": 1},  # 一分钱买一千块的沙发
    }
    assert await notify(order["orderNo"], total=1) == 200

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_pay"


async def test_callback_with_bad_signature_changes_nothing(client, shopper, callback_stub):
    """验签没过就什么都不做，并且要回 FAIL 让微信知道我们没受理。"""
    order = await an_order(client, shopper)
    callback_stub["resource"] = None  # 验签失败

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/shop/pay/notify", content=b"{}")
    assert r.json()["code"] == "FAIL"

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_pay"


async def test_callback_needs_no_auth(client, shopper, callback_stub):
    """微信不会带我们的 token，这条路由必须能匿名访问。"""
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_D",
        "amount": {"total": 100000},
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/shop/pay/notify", content=b"{}")
    assert r.status_code == 200
    assert r.json()["code"] == "SUCCESS"


# ---------------------------------------------------------------------------
# 清购物车


async def test_paying_a_cart_order_clears_those_cart_rows(client, shopper, callback_stub):
    """清车发生在**支付成功**这一刻，不是下单时。"""
    order = await an_order(client, shopper, source="cart", price_cents=100000)

    cart = (await shopper.get("/api/shop/cart")).json()
    assert cart["count"] == 1, "下单不该清车"

    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_E",
        "amount": {"total": 100000},
    }
    await notify(order["orderNo"], total=100000)

    cart = (await shopper.get("/api/shop/cart")).json()
    assert cart["count"] == 0, "支付成功后应清掉对应的车行"


async def test_paying_a_direct_order_leaves_the_cart_alone(client, shopper, callback_stub):
    """详情页「立即购买」不该动用户车里的同款——这正是 shop_orders.source 存在的理由。"""
    product_id = await a_product(client, 100000)
    r = await shopper.post("/api/shop/addresses", json=ADDRESS)
    address_id = r.json()["item"]["id"]
    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 3})

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    order = r.json()["order"]

    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_F",
        "amount": {"total": 100000},
    }
    await notify(order["orderNo"], total=100000)

    cart = (await shopper.get("/api/shop/cart")).json()
    assert cart["count"] == 1, "立即购买不该清掉车里的同款"
    assert cart["items"][0]["quantity"] == 3


# ---------------------------------------------------------------------------
# 主动查单


async def test_sync_pay_settles_when_wechat_says_paid(client, shopper, paid_stub):
    """回调迟到时的兜底。小程序把 requestPayment 成功当作「已提交」，
    真正算数的是这里查出来的结果。"""
    order = await an_order(client, shopper, price_cents=100000)
    paid_stub["total"] = 100000

    r = await shopper.post(f"/api/shop/orders/{order['id']}/sync-pay")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending_ship"


async def test_sync_pay_is_throttled(client, shopper, paid_stub):
    """用户反复下拉刷新不能把微信的查单接口打爆。"""
    order = await an_order(client, shopper, price_cents=100000)
    paid_stub["trade_state"] = "NOTPAY"

    await shopper.post(f"/api/shop/orders/{order['id']}/sync-pay")
    r = await shopper.post(f"/api/shop/orders/{order['id']}/sync-pay")
    assert r.json().get("throttled") is True
    assert len(paid_stub["queried"]) == 1, "第二次不该真的去查微信"


async def test_sync_pay_does_not_query_when_already_paid(client, shopper, paid_stub, callback_stub):
    """回调先到的话就没必要再查一次。"""
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_G",
        "amount": {"total": 100000},
    }
    await notify(order["orderNo"], total=100000)
    paid_stub["queried"].clear()

    r = await shopper.post(f"/api/shop/orders/{order['id']}/sync-pay")
    assert r.json()["status"] == "pending_ship"
    assert paid_stub["queried"] == [], "已支付就不该再去查微信"


async def test_sync_pay_is_scoped_to_its_owner(client, shopper, paid_stub, monkeypatch):
    order = await an_order(client, shopper)
    monkeypatch.setattr(users_module, "code2session", fake_session(f"{TEST_OPENID}2"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"code": "fake-code"})
        other = {"Authorization": f"Bearer {r.json()['token']}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=other) as c:
        assert (await c.post(f"/api/shop/orders/{order['id']}/sync-pay")).status_code == 404
        assert (await c.post(f"/api/shop/orders/{order['id']}/pay")).status_code == 404


# ---------------------------------------------------------------------------
# 超时关单


async def test_sweep_closes_expired_orders(client, shopper, paid_stub):
    """超时未付的单要关掉，且**先关微信再改自己的状态**。"""
    from app.shop_pay import sweep_expired_orders

    order = await an_order(client, shopper)
    paid_stub["trade_state"] = "NOTPAY"

    # 把创建时间推回 31 分钟前，模拟超时
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '31 minutes' WHERE id = %s",
            (int(order["id"]),),
        )

    closed = await sweep_expired_orders()
    assert closed >= 1
    assert order["orderNo"] in paid_stub["closed"], "必须先调微信关单"

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    body = r.json()["order"]
    assert body["status"] == "closed"
    assert body["closeReason"] == "timeout"


async def test_sweep_settles_an_order_paid_at_the_last_second(client, shopper, paid_stub):
    """卡在超时边界上付成功的，必须按支付成功处理而不是关单——
    否则钱收了、订单是关的。"""
    from app.shop_pay import sweep_expired_orders

    order = await an_order(client, shopper, price_cents=100000)
    paid_stub["trade_state"] = "SUCCESS"
    paid_stub["total"] = 100000

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '31 minutes' WHERE id = %s",
            (int(order["id"]),),
        )

    await sweep_expired_orders()
    assert order["orderNo"] not in paid_stub["closed"], "已支付的单不能去关"

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_ship"


async def test_sweep_leaves_fresh_orders_alone(client, shopper, paid_stub):
    from app.shop_pay import sweep_expired_orders

    order = await an_order(client, shopper)
    paid_stub["trade_state"] = "NOTPAY"

    await sweep_expired_orders()

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_pay"
    assert paid_stub["closed"] == []


# ---------------------------------------------------------------------------
# 超时关单：不再被永远处理不掉的单堵死


async def test_sweep_closes_an_order_wechat_never_saw(client, shopper, paid_stub, monkeypatch):
    """**队头阻塞的回归**。

    下单时微信抖了一下（订单按设计保留为待付款），微信侧根本没有这笔单，
    查单永远返回 ORDERNOTEXIST。老代码在这里 continue，而这笔单又是最老的，
    `ORDER BY created_at LIMIT 50` 保证它每轮都被优先选中——攒够 50 笔，
    整个超时关单就再也关不掉任何单了。

    现在它被识别成「重试也没用」，直接本地关掉，且**不去调 close_order**
    （关一笔微信不认识的单必然失败）。
    """
    from app.shop_pay import sweep_expired_orders

    order = await an_order(client, shopper)

    async def query_order(out_trade_no):
        raise wxpay.PayOrderNotExist("查单失败：微信侧没有这笔单")

    monkeypatch.setattr(wxpay, "query_order", query_order)

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '31 minutes' WHERE id = %s",
            (int(order["id"]),),
        )

    assert await sweep_expired_orders() >= 1
    assert paid_stub["closed"] == [], "微信不认识这笔单，不该再去调关单"

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    body = r.json()["order"]
    assert body["status"] == "closed"
    assert body["closeReason"] == "never_submitted"


async def test_sweep_gives_up_after_repeated_failures(client, shopper, paid_stub, monkeypatch):
    """可以重试的失败要重试，但不能无限重试。

    到上限之后这笔单不再被选中——否则它会永远占着取数窗口的队头，
    把后面所有超时单挡在外面。
    """
    from app.shop_pay import SWEEP_MAX_ATTEMPTS, sweep_expired_orders

    order = await an_order(client, shopper)
    tries = {"n": 0}

    async def query_order(out_trade_no):
        tries["n"] += 1
        raise wxpay.PayError("查单失败")

    monkeypatch.setattr(wxpay, "query_order", query_order)

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '31 minutes' WHERE id = %s",
            (int(order["id"]),),
        )

    for _ in range(SWEEP_MAX_ATTEMPTS):
        await sweep_expired_orders()
    assert tries["n"] == SWEEP_MAX_ATTEMPTS

    # 再扫一轮：这笔单已经到上限，不该再被选中，也就不该再问微信
    await sweep_expired_orders()
    assert tries["n"] == SWEEP_MAX_ATTEMPTS, "到上限之后不该再选中它"

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT status, sweep_attempts FROM shop_orders WHERE id = %s",
                (int(order["id"]),),
            )
        ).fetchone()
    assert row["status"] == "pending_pay", "查单一直失败就不能凭空关单"
    assert row["sweep_attempts"] == SWEEP_MAX_ATTEMPTS


async def test_sweep_stops_retrying_an_order_it_cannot_settle(client, shopper, paid_stub):
    """微信说付了但金额对不上：状态迁移不了，台账已经记了，
    这里只保证它别永远留在取数窗口里被反复查。"""
    from app.shop_pay import SWEEP_MAX_ATTEMPTS, sweep_expired_orders

    order = await an_order(client, shopper, price_cents=100000)
    paid_stub["trade_state"] = "SUCCESS"
    paid_stub["total"] = 1  # 金额不符

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '31 minutes' WHERE id = %s",
            (int(order["id"]),),
        )

    await sweep_expired_orders()

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT status, sweep_attempts FROM shop_orders WHERE id = %s",
                (int(order["id"]),),
            )
        ).fetchone()
    assert row["status"] == "pending_pay", "金额不符不能置为已支付"
    assert row["sweep_attempts"] == 1
    assert SWEEP_MAX_ATTEMPTS > 1


async def test_sweep_ignores_the_other_environments_orders(client, shopper, paid_stub):
    """**撞号那个问题的回归**。

    close_order 是按商户订单号关的，而两个环境共用同一个微信商户号。不限定前缀的话，
    这里会去关**另一个环境里客户正在支付的那一单**。
    """
    from app.order_no import ORDER_NO_PREFIX
    from app.shop_pay import sweep_expired_orders

    order = await an_order(client, shopper)
    paid_stub["trade_state"] = "NOTPAY"

    other = "AD" if ORDER_NO_PREFIX == "AX" else "AX"
    foreign_no = other + order["orderNo"][2:]

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '31 minutes',"
            " order_no = %s WHERE id = %s",
            (foreign_no, int(order["id"])),
        )

    await sweep_expired_orders()
    assert paid_stub["queried"] == [], "别的环境的单号，一次也不该去问微信"
    assert paid_stub["closed"] == []

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT status FROM shop_orders WHERE id = %s", (int(order["id"]),)
            )
        ).fetchone()
    assert row["status"] == "pending_pay"


# ---------------------------------------------------------------------------
# 重新拉起支付：节流与超时窗口


async def test_pay_is_throttled(client, shopper, paid_stub):
    """每次调用都真的向微信下一次单。没有节流的话，循环调这个接口
    等于无限消耗微信的下单频率配额，还各占一条数据库连接。"""
    order = await an_order(client, shopper)

    first = await shopper.post(f"/api/shop/orders/{order['id']}/pay")
    assert first.status_code == 200, first.text

    second = await shopper.post(f"/api/shop/orders/{order['id']}/pay")
    assert second.status_code == 429


async def test_pay_is_allowed_again_after_the_throttle_window(client, shopper, paid_stub):
    order = await an_order(client, shopper)
    assert (await shopper.post(f"/api/shop/orders/{order['id']}/pay")).status_code == 200

    # 把上次下单时刻推回去，等价于等过了节流窗口
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET last_pay_at = now() - interval '1 hour' WHERE id = %s",
            (int(order["id"]),),
        )

    assert (await shopper.post(f"/api/shop/orders/{order['id']}/pay")).status_code == 200


async def test_pay_refuses_when_the_order_is_about_to_expire(client, shopper, paid_stub):
    """支付截止时刻锚定 created_at。剩余窗口太短就别下单了——
    微信要求 time_expire 是未来时刻，而且用户刚打开收银台就会被超时扫描关掉。"""
    order = await an_order(client, shopper)

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '29 minutes 30 seconds',"
            " last_pay_at = NULL WHERE id = %s",
            (int(order["id"]),),
        )

    r = await shopper.post(f"/api/shop/orders/{order['id']}/pay")
    assert r.status_code == 409
    assert "超时" in r.json()["message"]


async def test_time_expire_is_anchored_to_created_at(client, shopper, paid_stub, monkeypatch):
    """time_expire 必须由订单的 created_at 算，不能用「现在 + 30 分钟」——
    否则用户在第 29 分钟点一次「去支付」，微信侧的有效期就被推到第 59 分钟，
    而我们仍然按 created_at 在第 30 分钟关单，正是要避免的那种不一致。"""
    from datetime import datetime, timedelta, timezone

    seen: dict = {}

    async def jsapi_order(**kwargs):
        seen.update(kwargs)
        return {"timeStamp": "1", "nonceStr": "n", "package": "prepay_id=x",
                "signType": "RSA", "paySign": "sig"}

    monkeypatch.setattr(wxpay, "jsapi_order", jsapi_order)

    order = await an_order(client, shopper)
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT created_at FROM shop_orders WHERE id = %s", (int(order["id"]),)
            )
        ).fetchone()

    assert seen["expire_at"] == row["created_at"] + timedelta(
        minutes=wxpay.PAY_TIMEOUT_MINUTES
    )

    # 把 created_at 推回 20 分钟，再拉一次：截止时刻必须跟着往前挪，
    # 而不是又变成「现在 + 30 分钟」
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET created_at = now() - interval '20 minutes',"
            " last_pay_at = NULL WHERE id = %s",
            (int(order["id"]),),
        )
    r = await shopper.post(f"/api/shop/orders/{order['id']}/pay")
    assert r.status_code == 200, r.text

    remaining = seen["expire_at"] - datetime.now(timezone.utc)
    assert remaining < timedelta(minutes=11), "锚点必须是 created_at，不是现在"


def test_expire_at_is_serialised_in_the_east_eight_form():
    """微信要的是带冒号偏移量的 RFC3339。strftime('%z') 给的是 +0800，微信不认；
    而 time.localtime + 字面量 '+08:00' 只有机器时区恰好是东八区时才对。"""
    from datetime import datetime, timezone

    utc_noon = datetime(2026, 8, 12, 4, 0, 0, tzinfo=timezone.utc)
    assert utc_noon.astimezone(wxpay.CHINA_TZ).isoformat(timespec="seconds") == (
        "2026-08-12T12:00:00+08:00"
    )

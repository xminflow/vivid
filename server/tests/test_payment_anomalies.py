"""支付异常台账。需要库，**不连微信**。

守的是同一件事：**钱收了但订单没走通时，这件事不能只留在日志里**。

三条路径最终都要向微信返回 SUCCESS（重投一百次结果一样），所以老代码里
唯一的线索是一行没人会主动去翻的 error 日志。这些用例钉住「一定落库」，
以及后台能把它查出来、标记掉。

隔离沿用 test_shop_pay.py：ZZ测试 前缀 + test_openid_anom 用户。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import users as users_module
from app import wxpay
from app.db import pool
from app.main import app

PREFIX = "ZZ测试"
TEST_OPENID = "test_openid_anom"
IMAGE_A = "static/shop/20260101/testa.jpg"


async def clean() -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM payment_anomalies WHERE order_no LIKE %s", ("ZZTEST%",)
        )
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


@pytest.fixture
async def shopper(client, monkeypatch):
    # 签名要跟真的一致：code2session 现在按小程序收 appid/secret（见 app/wechat.py）
    async def code2session(code: str, appid: str = "", secret: str = "") -> dict:
        return {"openid": f"{TEST_OPENID}1", "unionid": None, "session_key": "k"}

    monkeypatch.setattr(users_module, "code2session", code2session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"code": "fake"})
        headers = {"Authorization": f"Bearer {r.json()['token']}"}
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


@pytest.fixture
def callback_stub(monkeypatch):
    """支付整体打桩：已配置，且验签直接返回我们指定的 resource。"""
    payload: dict = {"resource": None}

    async def verify_callback(headers, body):
        return payload["resource"]

    async def jsapi_order(**kwargs):
        return {"timeStamp": "1", "nonceStr": "n", "package": "prepay_id=x",
                "signType": "RSA", "paySign": "s"}

    monkeypatch.setattr(wxpay, "configured", lambda: True)
    monkeypatch.setattr(wxpay, "verify_callback", verify_callback)
    monkeypatch.setattr(wxpay, "jsapi_order", jsapi_order)
    monkeypatch.setattr(wxpay, "NOTIFY_URL", "https://example.test/api/shop/pay/notify")
    return payload


async def an_order(client, shopper, price_cents: int = 100000) -> dict:
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}台账分类"})
    category_id = r.json()["id"]
    r = await client.post(
        "/api/admin/shop/products",
        json={"categoryId": category_id, "title": f"{PREFIX}台账沙发", "summary": "",
              "priceCents": price_cents, "images": [IMAGE_A], "detailImages": [],
              "params": [], "sortOrder": 0},
    )
    product_id = r.json()["id"]
    await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "active"})

    r = await shopper.post(
        "/api/shop/addresses",
        json={"receiver": "陈女士", "phone": "13612345678", "province": "上海市",
              "city": "上海市", "district": "静安区", "detail": "南京西路 1 号"},
    )
    r = await shopper.post(
        "/api/shop/orders",
        json={"addressId": r.json()["item"]["id"], "source": "direct",
              "items": [{"productId": product_id, "quantity": 1}]},
    )
    assert r.status_code == 200, r.text
    return r.json()["order"]


async def notify() -> int:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/shop/pay/notify", content=b"{}")
    return r.json()["code"]


async def open_anomalies(client) -> list[dict]:
    r = await client.get("/api/admin/shop/payment-anomalies")
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ---------------------------------------------------------------------------
# 三种异常都要落库


async def test_amount_mismatch_lands_in_the_ledger(client, shopper, callback_stub):
    """金额不符：要么建单时算错，要么有人在中间改了金额。
    拒绝迁移状态是对的，但这笔钱**已经到账了**，必须留下可查的记录。"""
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_ANOM_1",
        "amount": {"total": 1},
    }

    assert await notify() == "SUCCESS", "要让微信别再重投——重投结果一样"

    items = await open_anomalies(client)
    hit = [i for i in items if i["orderNo"] == order["orderNo"]]
    assert len(hit) == 1
    assert hit[0]["kind"] == "amount_mismatch"
    assert hit[0]["source"] == "notify"
    assert hit[0]["paidCents"] == 1
    assert hit[0]["expectedCents"] == 100000
    assert hit[0]["transactionId"] == "WX_ANOM_1"

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_pay", "金额不符不能置为已支付"


async def test_an_unknown_order_number_lands_in_the_ledger(client, callback_stub):
    """收到一笔支付成功，单号却不在库里——钱收了却对不上任何订单，
    是三种里最需要有人立刻去看的一种。"""
    callback_stub["resource"] = {
        "out_trade_no": "ZZTEST20260812999999",
        "trade_state": "SUCCESS",
        "transaction_id": "WX_ANOM_2",
        "amount": {"total": 5000},
    }

    assert await notify() == "SUCCESS"

    items = await open_anomalies(client)
    hit = [i for i in items if i["orderNo"] == "ZZTEST20260812999999"]
    assert len(hit) == 1
    assert hit[0]["kind"] == "order_not_found"
    assert hit[0]["paidCents"] == 5000
    assert hit[0]["orderId"] is None
    assert hit[0]["expectedCents"] is None, "没有这笔订单，也就没有「应付多少」"


async def test_a_missing_transaction_id_lands_in_the_ledger(client, shopper, callback_stub):
    """没有微信支付订单号就没法做幂等。

    更要命的是空串**会进** shop_orders.transaction_id 上那个部分唯一索引
    （条件是 IS NOT NULL），写进去之后第二笔同样缺号的单会撞唯一约束，
    在回调里表现为 500 + 微信 24 小时重投。所以这里必须挂起而不是硬写。
    """
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "",
        "amount": {"total": 100000},
    }

    assert await notify() == "SUCCESS"

    items = await open_anomalies(client)
    hit = [i for i in items if i["orderNo"] == order["orderNo"]]
    assert len(hit) == 1
    assert hit[0]["kind"] == "missing_transaction_id"

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT status, transaction_id FROM shop_orders WHERE id = %s",
                (int(order["id"]),),
            )
        ).fetchone()
    assert row["status"] == "pending_pay"
    assert row["transaction_id"] is None, "绝不能把空串写进唯一索引"


async def test_a_normal_payment_records_nothing(client, shopper, callback_stub):
    """正常支付不该留下任何异常记录——台账里有一条就该有人去看，
    掺进正常流水就等于没有台账。"""
    order = await an_order(client, shopper, price_cents=100000)
    callback_stub["resource"] = {
        "out_trade_no": order["orderNo"],
        "trade_state": "SUCCESS",
        "transaction_id": "WX_ANOM_OK",
        "amount": {"total": 100000},
    }

    assert await notify() == "SUCCESS"

    items = await open_anomalies(client)
    assert [i for i in items if i["orderNo"] == order["orderNo"]] == []

    r = await shopper.get(f"/api/shop/orders/{order['id']}")
    assert r.json()["order"]["status"] == "pending_ship"


# ---------------------------------------------------------------------------
# 后台


async def test_resolving_takes_it_off_the_open_list(client, callback_stub):
    callback_stub["resource"] = {
        "out_trade_no": "ZZTEST20260812999998",
        "trade_state": "SUCCESS",
        "transaction_id": "WX_ANOM_3",
        "amount": {"total": 1000},
    }
    await notify()

    items = await open_anomalies(client)
    hit = [i for i in items if i["orderNo"] == "ZZTEST20260812999998"][0]

    r = await client.post(
        f"/api/admin/shop/payment-anomalies/{hit['id']}/resolve",
        json={"note": "已在商户平台原路退回"},
    )
    assert r.status_code == 200, r.text

    still_open = await open_anomalies(client)
    assert [i for i in still_open if i["id"] == hit["id"]] == []

    r = await client.get("/api/admin/shop/payment-anomalies?resolved=done")
    done = [i for i in r.json()["items"] if i["id"] == hit["id"]]
    assert len(done) == 1
    assert done[0]["resolveNote"] == "已在商户平台原路退回"
    assert done[0]["resolvedBy"], "要记下是谁处理的"


async def test_resolve_requires_a_note(client, callback_stub):
    """几个月后回看时，「谁在什么时候标了已处理」远不如「当时怎么处理的」有用。"""
    callback_stub["resource"] = {
        "out_trade_no": "ZZTEST20260812999997",
        "trade_state": "SUCCESS",
        "transaction_id": "WX_ANOM_4",
        "amount": {"total": 1000},
    }
    await notify()
    hit = [i for i in await open_anomalies(client) if i["orderNo"] == "ZZTEST20260812999997"][0]

    r = await client.post(
        f"/api/admin/shop/payment-anomalies/{hit['id']}/resolve", json={"note": ""}
    )
    assert r.status_code == 400


async def test_resolving_twice_is_not_an_error(client, callback_stub):
    """重复点不该弹一个红色报错。"""
    callback_stub["resource"] = {
        "out_trade_no": "ZZTEST20260812999996",
        "trade_state": "SUCCESS",
        "transaction_id": "WX_ANOM_5",
        "amount": {"total": 1000},
    }
    await notify()
    hit = [i for i in await open_anomalies(client) if i["orderNo"] == "ZZTEST20260812999996"][0]

    body = {"note": "已处理"}
    assert (
        await client.post(f"/api/admin/shop/payment-anomalies/{hit['id']}/resolve", json=body)
    ).status_code == 200
    assert (
        await client.post(f"/api/admin/shop/payment-anomalies/{hit['id']}/resolve", json=body)
    ).status_code == 200


async def test_the_ledger_needs_admin_auth(callback_stub):
    """台账里有订单号、金额和微信支付订单号，不能免鉴权。"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/admin/shop/payment-anomalies")).status_code == 401


async def test_a_failing_ledger_write_does_not_break_the_transaction():
    """台账写失败时，**调用方的事务必须还能提交**。

    在一个已经开着的事务里跑一条失败的语句，会让整个事务进入 aborted 状态，
    之后连 COMMIT 都报错。那时光把异常吞掉是没用的——调用方仍然会在提交时炸，
    只是报错位置挪到了一个更难懂的地方。所以 record() 把 INSERT 套在
    savepoint 里，失败只回滚这一条。

    这里用一个必然违反 CHECK 的 kind 制造失败。
    """
    from app import payment_anomalies

    async with pool.connection() as conn:
        async with conn.transaction():
            # 故意写一个 CHECK 不允许的 kind，让 INSERT 必然失败
            await payment_anomalies.record(
                conn,
                kind="不存在的类型",  # type: ignore[arg-type]
                source="notify",
                order_no="ZZTEST20260812000001",
            )
            # 事务没有被毒化：同一个事务里还能继续执行别的语句
            row = await (await conn.execute("SELECT 1 AS ok")).fetchone()
            assert row["ok"] == 1
        # 走到这里说明 COMMIT 也成功了

    # 那条失败的记录确实没写进去
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT count(*) AS n FROM payment_anomalies WHERE order_no = %s",
                ("ZZTEST20260812000001",),
            )
        ).fetchone()
    assert row["n"] == 0

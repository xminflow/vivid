"""安玺·集 地址簿与下单。需要库可连。

隔离方式沿用 test_shop.py：分类名和商品标题带 ZZ测试 前缀，用户用
test_openid_ 前缀，clean() 按前缀删。订单和订单行不用单独删——
shop_orders.user_id 没有 CASCADE（订单是凭证，不该跟着用户走），
所以这里显式按测试用户删订单，再删用户。

**不测支付**：下单只验到 pending_pay。拉起支付、回调、主动查单是阶段三
（app/wxpay.py）的事，那些要打桩微信，属于另一个文件。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import users as users_module
from app.db import pool
from app.main import app

PREFIX = "ZZ测试"
TEST_OPENID = "test_openid_order"

IMAGE_A = "static/shop/20260101/testa.jpg"

ADDRESS = {
    "receiver": "陈女士",
    "phone": "13612345678",
    "province": "上海市",
    "city": "上海市",
    "district": "静安区",
    "detail": "南京西路 1000 号 20 楼",
}


async def clean() -> None:
    """按前缀清理。顺序要紧：

    订单行引用商品且是 ON DELETE RESTRICT，所以必须先删订单（连带 CASCADE 掉
    订单行），才能删商品——反过来会被外键挡住，而那正是这张表要保证的事。
    """
    async with pool.connection() as conn:
        await conn.execute(
            """
            DELETE FROM shop_orders
             WHERE user_id IN (SELECT id FROM users WHERE openid LIKE %s)
            """,
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
        # 地址簿是 ON DELETE CASCADE，跟着用户走，不用单独删
        await conn.execute("DELETE FROM users WHERE openid LIKE %s", (f"{TEST_OPENID}%",))


@pytest.fixture
async def client(auth_headers):
    """管理员客户端。上架商品要用它。"""
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


async def shopper_headers(monkeypatch, suffix: str = "1") -> dict:
    monkeypatch.setattr(users_module, "code2session", fake_session(f"{TEST_OPENID}{suffix}"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"code": "fake-code"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
async def shopper(client, monkeypatch):
    headers = await shopper_headers(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


async def a_product(client, title: str = "云朵沙发", price_cents: int = 1299900) -> str:
    """上架一件商品，返回它的 id。"""
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}沙发{title}"})
    assert r.status_code == 200, r.text
    category_id = r.json()["id"]

    r = await client.post(
        "/api/admin/shop/products",
        json={
            "categoryId": category_id,
            "title": f"{PREFIX}{title}",
            "summary": "一段简介",
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


async def an_address(shopper, **overrides) -> str:
    body = {**ADDRESS, **overrides}
    r = await shopper.post("/api/shop/addresses", json=body)
    assert r.status_code == 200, r.text
    return r.json()["item"]["id"]


# ---------------------------------------------------------------------------
# 地址簿


async def test_addresses_require_login(client):
    """client 带的是管理员 token，对用户接口无效。"""
    r = await client.get("/api/shop/addresses")
    assert r.status_code == 401


async def test_first_address_becomes_default_even_if_not_asked(shopper):
    """地址簿为空时新增的第一条强制为默认。

    否则用户新增了唯一一条地址，去结算页还是「请选择收货地址」，没人能理解。
    """
    r = await shopper.post("/api/shop/addresses", json={**ADDRESS, "isDefault": False})
    assert r.status_code == 200, r.text
    assert r.json()["item"]["isDefault"] is True


async def test_setting_a_new_default_clears_the_old_one(shopper):
    """库上有 (user_id) WHERE is_default 的部分唯一索引，两个默认地址插不进去。
    接口必须在同一个事务里先清旧的，否则第二条直接撞唯一索引报 500。"""
    first = await an_address(shopper, receiver="甲")
    second = await an_address(shopper, receiver="乙", detail="愚园路 2 号")

    r = await shopper.put(f"/api/shop/addresses/{second}/default")
    assert r.status_code == 200, r.text

    r = await shopper.get("/api/shop/addresses")
    items = {item["id"]: item["isDefault"] for item in r.json()["items"]}
    assert items[second] is True
    assert items[first] is False


async def test_default_address_sorts_first(shopper):
    """结算页默认选中第一条，所以默认地址必须排在最前。"""
    await an_address(shopper, receiver="甲")
    second = await an_address(shopper, receiver="乙", detail="愚园路 2 号")
    await shopper.put(f"/api/shop/addresses/{second}/default")

    r = await shopper.get("/api/shop/addresses")
    assert r.json()["items"][0]["id"] == second


async def test_phone_must_look_like_a_phone(shopper):
    r = await shopper.post("/api/shop/addresses", json={**ADDRESS, "phone": "12345"})
    assert r.status_code == 400


async def test_one_shopper_cannot_touch_another_shoppers_address(client, shopper, monkeypatch):
    """地址 id 会出现在下单请求里，是客户端可见的值。只按 id 查改删就等于
    谁拿到别人的 id 就能读到别人的收件人和手机号。"""
    address_id = await an_address(shopper)

    other = await shopper_headers(monkeypatch, "2")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=other) as c:
        assert (await c.put(f"/api/shop/addresses/{address_id}", json=ADDRESS)).status_code == 404
        assert (await c.delete(f"/api/shop/addresses/{address_id}")).status_code == 404
        assert (await c.put(f"/api/shop/addresses/{address_id}/default")).status_code == 404


async def test_deleting_the_default_does_not_promote_another(shopper):
    """默认地址是用户的选择，系统替他选一个，下次下单可能就寄错地方。"""
    first = await an_address(shopper, receiver="甲")
    second = await an_address(shopper, receiver="乙", detail="愚园路 2 号")
    await shopper.put(f"/api/shop/addresses/{second}/default")

    assert (await shopper.delete(f"/api/shop/addresses/{second}")).status_code == 200

    r = await shopper.get("/api/shop/addresses")
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == first
    assert items[0]["isDefault"] is False


# ---------------------------------------------------------------------------
# 下单


async def test_order_total_is_computed_by_the_server(client, shopper):
    """请求里压根没有金额字段，总额只能由服务端按当前价格算。"""
    product_id = await a_product(client, price_cents=1299900)
    address_id = await an_address(shopper)

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 3}],
        },
    )
    assert r.status_code == 200, r.text
    order = r.json()["order"]
    assert order["totalCents"] == 1299900 * 3
    assert order["status"] == "pending_pay"


async def test_order_no_has_the_agreed_shape(client, shopper):
    """环境前缀 + YYYYMMDD + 6 位。它同时是微信支付的 out_trade_no，
    格式变了会直接影响支付，库上也有 CHECK（`^[A-Z]{2}[0-9]{14}$`）。

    前缀按环境配置（生产 AX、开发 AD），所以断言的是「本环境的前缀」而不是写死
    AX——两个环境共用同一个微信商户号，前缀正是用来防止两边发出同名单号的，
    见 app/order_no.py。
    """
    import re

    from app.order_no import ORDER_NO_PREFIX

    product_id = await a_product(client)
    address_id = await an_address(shopper)
    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    order_no = r.json()["order"]["orderNo"]
    assert re.fullmatch(rf"{ORDER_NO_PREFIX}\d{{14}}", order_no), order_no
    # 库上那条 CHECK 的形状也一并钉住：前缀必须是两位大写字母
    assert re.fullmatch(r"[A-Z]{2}\d{14}", order_no), order_no


async def test_order_numbers_are_unique_under_concurrency(client, shopper):
    """取号靠 INSERT ... ON CONFLICT DO UPDATE RETURNING 一条语句，
    并发下由行锁串行。同时发十单，十个号必须互不相同。"""
    import asyncio

    product_id = await a_product(client)
    address_id = await an_address(shopper)
    body = {
        "addressId": address_id,
        "source": "direct",
        "items": [{"productId": product_id, "quantity": 1}],
    }

    results = await asyncio.gather(
        *(shopper.post("/api/shop/orders", json=body) for _ in range(10))
    )
    numbers = [r.json()["order"]["orderNo"] for r in results if r.status_code == 200]
    assert len(numbers) == 10, [r.text for r in results if r.status_code != 200]
    assert len(set(numbers)) == 10, numbers


async def test_order_snapshots_survive_a_price_change(client, shopper):
    """下单后运营改价改名，订单里必须还是下单那一刻的样子。
    这是订单行存快照、任何场景都不回查 shop_products 的整个理由。"""
    product_id = await a_product(client, price_cents=1000)
    address_id = await an_address(shopper)

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 2}],
        },
    )
    order_id = r.json()["order"]["id"]

    detail = await client.get(f"/api/admin/shop/products/{product_id}")
    payload = detail.json()["item"]
    r = await client.put(
        f"/api/admin/shop/products/{product_id}",
        json={
            "categoryId": payload["categoryId"],
            "title": f"{PREFIX}改过名的沙发",
            "summary": payload["summary"],
            "priceCents": 9999900,
            "images": [IMAGE_A],
            "detailImages": [],
            "params": [],
            "sortOrder": 0,
        },
    )
    assert r.status_code == 200, r.text

    r = await shopper.get(f"/api/shop/orders/{order_id}")
    item = r.json()["order"]["items"][0]
    assert item["priceCents"] == 1000
    assert item["title"] == f"{PREFIX}云朵沙发"
    assert r.json()["order"]["totalCents"] == 2000


async def test_address_snapshot_survives_address_deletion(client, shopper):
    """用户删了地址，历史订单还得知道当初寄到哪。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    order_id = r.json()["order"]["id"]

    assert (await shopper.delete(f"/api/shop/addresses/{address_id}")).status_code == 200

    r = await shopper.get(f"/api/shop/orders/{order_id}")
    assert r.json()["order"]["address"]["receiver"] == "陈女士"
    assert r.json()["order"]["address"]["detail"] == ADDRESS["detail"]


async def test_cannot_order_an_off_shelf_product(client, shopper):
    """用户停在结算页的那几分钟里，商品可能被运营下架了。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "off"})

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    assert r.status_code == 400
    assert "下架" in r.json()["message"]


async def test_cannot_order_with_someone_elses_address(client, shopper, monkeypatch):
    """地址查询带 user_id，所以「别人的地址」和「不存在」是同一个结果。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)

    other = await shopper_headers(monkeypatch, "2")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=other) as c:
        r = await c.post(
            "/api/shop/orders",
            json={
                "addressId": address_id,
                "source": "direct",
                "items": [{"productId": product_id, "quantity": 1}],
            },
        )
    assert r.status_code == 400
    assert "收货地址" in r.json()["message"]


async def test_cart_source_requires_the_items_to_be_in_the_cart(client, shopper):
    """声称从购物车结算，但那件商品从没加过购——放行的话订单的 source
    就不再可信，将来对账说不清这单从哪来。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "cart",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    assert r.status_code == 400
    assert "购物车" in r.json()["message"]


async def test_ordering_from_cart_does_not_clear_it(client, shopper):
    """清车要等**支付成功**。用户下了单没付，车里的东西必须还在。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    assert (
        await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 2})
    ).status_code == 200

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "cart",
            "items": [{"productId": product_id, "quantity": 2}],
        },
    )
    assert r.status_code == 200, r.text

    cart = await shopper.get("/api/shop/cart")
    assert cart.json()["count"] == 1
    assert cart.json()["quantity"] == 2


async def test_same_product_twice_in_one_order_is_rejected(client, shopper):
    """两行同款会让订单里出现两条一模一样的行、金额也翻倍。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)

    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [
                {"productId": product_id, "quantity": 1},
                {"productId": product_id, "quantity": 2},
            ],
        },
    )
    assert r.status_code == 400


async def test_empty_items_is_rejected(client, shopper):
    address_id = await an_address(shopper)
    r = await shopper.post(
        "/api/shop/orders", json={"addressId": address_id, "source": "cart", "items": []}
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 我的订单 / 取消


async def test_orders_are_scoped_to_their_owner(client, shopper, monkeypatch):
    """订单 id 是客户端可见的值。只按 id 查就等于谁拿到别人的单号，
    就能看到别人的收件人、手机号和买了什么。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    order_id = r.json()["order"]["id"]

    other = await shopper_headers(monkeypatch, "2")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=other) as c:
        assert (await c.get(f"/api/shop/orders/{order_id}")).status_code == 404
        assert (await c.post(f"/api/shop/orders/{order_id}/cancel")).status_code == 404
        assert (await c.get("/api/shop/orders")).json()["total"] == 0


async def test_order_list_filters_by_status(client, shopper):
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    body = {
        "addressId": address_id,
        "source": "direct",
        "items": [{"productId": product_id, "quantity": 1}],
    }
    first = (await shopper.post("/api/shop/orders", json=body)).json()["order"]["id"]
    await shopper.post("/api/shop/orders", json=body)
    await shopper.post(f"/api/shop/orders/{first}/cancel")

    r = await shopper.get("/api/shop/orders", params={"status": "pending_pay"})
    assert r.json()["total"] == 1
    r = await shopper.get("/api/shop/orders", params={"status": "closed"})
    assert r.json()["total"] == 1
    r = await shopper.get("/api/shop/orders")
    assert r.json()["total"] == 2


async def test_bad_status_is_rejected_not_silently_empty(shopper):
    """带着脏值查库会返回空列表，前端会以为「没有订单」。"""
    r = await shopper.get("/api/shop/orders", params={"status": "shipped"})
    assert r.status_code == 400


async def test_order_list_carries_its_items(client, shopper):
    """列表也带订单行：家具单一般就一两行，为了缩略图再发一轮请求不值当。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 2}],
        },
    )

    r = await shopper.get("/api/shop/orders")
    items = r.json()["items"][0]["items"]
    assert len(items) == 1
    assert items[0]["quantity"] == 2


async def test_cancel_only_works_on_unpaid_orders(client, shopper):
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    r = await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )
    order_id = r.json()["order"]["id"]

    assert (await shopper.post(f"/api/shop/orders/{order_id}/cancel")).status_code == 200

    r = await shopper.get(f"/api/shop/orders/{order_id}")
    assert r.json()["order"]["status"] == "closed"
    assert r.json()["order"]["closeReason"] == "user_cancel"

    # 重复点取消不算错，省得用户看到一个红色报错
    assert (await shopper.post(f"/api/shop/orders/{order_id}/cancel")).status_code == 200


async def test_paid_orders_cannot_be_cancelled_by_the_user(client, shopper):
    """已付款的单只能走退款，那要动真钱，只有超管在后台能做。

    这里直接把状态改成已付款来模拟——支付链路是阶段三的事。
    """
    product_id = await a_product(client)
    address_id = await an_address(shopper)
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
            "UPDATE shop_orders SET status = 'pending_ship', paid_at = now() WHERE id = %s",
            (int(order_id),),
        )

    r = await shopper.post(f"/api/shop/orders/{order_id}/cancel")
    assert r.status_code == 409
    assert "付款" in r.json()["message"]


async def test_a_sold_product_cannot_be_deleted(client, shopper):
    """订单行的 product_id 是 ON DELETE RESTRICT——卖过的商品永远只能下架。
    这条约束由数据库强制，不依赖应用层记得检查。"""
    product_id = await a_product(client)
    address_id = await an_address(shopper)
    await shopper.post(
        "/api/shop/orders",
        json={
            "addressId": address_id,
            "source": "direct",
            "items": [{"productId": product_id, "quantity": 1}],
        },
    )

    r = await client.delete(f"/api/admin/shop/products/{product_id}")
    assert r.status_code == 409, r.text

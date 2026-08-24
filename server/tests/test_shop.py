"""安玺·集 商品目录：后台写、小程序读。需要库可连。

隔离靠**名称前缀**：分类名和商品标题都以 ZZ测试 开头，clean() 按前缀删。
分类名有唯一约束，所以每个测试用例里的分类名还要各不相同，否则同一个文件里
前后两条用例撞名会 409——这一点和 test_admin_auth.py 用 `test.` 前缀开头的
用户名是同一个套路。

不测图片真传：COS 那条路径由 test_cos.py 覆盖，这里只验从对象键往后的落库、
校验、状态流转和出参。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import cos
from app import users as users_module
from app.db import pool
from app.main import app

PREFIX = "ZZ测试"
# 购物车测试要一个真实用户。openid 前缀与 test_users.py 一致，清理方式也一致
TEST_OPENID = "test_openid_shop"

# 合法的商品图对象键。前缀必须是 static/，见 models.check_static_key
IMAGE_A = "static/shop/20260101/testa.jpg"
IMAGE_B = "static/shop/20260101/testb.jpg"
IMAGE_C = "static/shop/20260101/testc.jpg"


async def clean() -> None:
    """先删商品再删分类：商品有外键指向分类，顺序反了删不掉。

    购物车行不用单独删——shop_cart_items.product_id 是 ON DELETE CASCADE，
    删商品时跟着走。测试用户则要显式删，它不挂在商品下面。
    """
    async with pool.connection() as conn:
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


async def make_category(client, name: str, sort_order: int = 0) -> str:
    r = await client.post(
        "/api/admin/shop/categories", json={"name": f"{PREFIX}{name}", "sortOrder": sort_order}
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def product_payload(category_id: str, title: str, **overrides) -> dict:
    body = {
        "categoryId": category_id,
        "title": f"{PREFIX}{title}",
        "summary": "一段简介",
        "priceCents": 1299900,
        "images": [IMAGE_A],
        "detailImages": [IMAGE_B],
        "params": [{"name": "材质", "value": "实木"}],
        "sortOrder": 0,
    }
    body.update(overrides)
    return body


async def make_product(client, category_id: str, title: str, **overrides) -> str:
    r = await client.post(
        "/api/admin/shop/products", json=product_payload(category_id, title, **overrides)
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def publish(client, product_id: str) -> None:
    r = await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "active"})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# 分类


async def test_category_names_must_be_unique(client):
    """重名要挡在接口上，否则后台会出现两个「灯具」，运营分不清往哪个里放商品。"""
    await make_category(client, "灯具")
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}灯具"})
    assert r.status_code == 409, r.text


async def test_category_list_counts_its_products(client):
    """列表要带商品数：停用分类会连带下架，运营点之前得知道影响多少件。"""
    category_id = await make_category(client, "沙发")
    first = await make_product(client, category_id, "云朵沙发")
    await make_product(client, category_id, "直角沙发")
    await publish(client, first)

    r = await client.get("/api/admin/shop/categories")
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["items"] if x["id"] == category_id)
    assert row["productCount"] == 2
    assert row["activeCount"] == 1


async def test_disabling_a_category_takes_its_products_off_the_shelf(client):
    """停用分类 = 批量下架。这是本板块最容易误操作的一步，必须有测试钉住。"""
    category_id = await make_category(client, "灯具")
    product_id = await make_product(client, category_id, "落地灯")
    await publish(client, product_id)

    r = await client.put(
        f"/api/admin/shop/categories/{category_id}/status", json={"status": "disabled"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["takenDown"] == 1

    detail = await client.get(f"/api/admin/shop/products/{product_id}")
    assert detail.json()["item"]["status"] == "off"


async def test_reenabling_a_category_does_not_put_products_back(client):
    """启用分类**不**自动恢复商品。

    这是刻意的：让 shop_products.status 保持「能否被购买」的唯一判据。
    恢复是一个显式动作，走 activate-products。
    """
    category_id = await make_category(client, "灯具")
    product_id = await make_product(client, category_id, "落地灯")
    await publish(client, product_id)
    await client.put(f"/api/admin/shop/categories/{category_id}/status", json={"status": "disabled"})

    await client.put(f"/api/admin/shop/categories/{category_id}/status", json={"status": "active"})
    detail = await client.get(f"/api/admin/shop/products/{product_id}")
    assert detail.json()["item"]["status"] == "off"

    r = await client.post(f"/api/admin/shop/categories/{category_id}/activate-products")
    assert r.status_code == 200, r.text
    assert r.json()["activated"] == 1
    detail = await client.get(f"/api/admin/shop/products/{product_id}")
    assert detail.json()["item"]["status"] == "active"


async def test_batch_activate_skips_products_without_an_image(client):
    """批量上架不能成为绕过「上架必须有图」的后门，跳过几件要如实报出来。"""
    category_id = await make_category(client, "配件")
    await make_product(client, category_id, "有图的", images=[IMAGE_A])
    await make_product(client, category_id, "没图的", images=[])

    r = await client.post(f"/api/admin/shop/categories/{category_id}/activate-products")
    assert r.status_code == 200, r.text
    assert r.json()["activated"] == 1
    assert r.json()["skippedWithoutImage"] == 1


async def test_batch_activate_refuses_while_the_category_is_disabled(client):
    category_id = await make_category(client, "餐桌")
    await make_product(client, category_id, "岩板餐桌")
    await client.put(f"/api/admin/shop/categories/{category_id}/status", json={"status": "disabled"})

    r = await client.post(f"/api/admin/shop/categories/{category_id}/activate-products")
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# 商品


async def test_new_products_start_off_the_shelf(client):
    """没有草稿态：新建即下架，运营填完再上架。"""
    category_id = await make_category(client, "沙发")
    product_id = await make_product(client, category_id, "云朵沙发")

    r = await client.get(f"/api/admin/shop/products/{product_id}")
    assert r.status_code == 200, r.text
    assert r.json()["item"]["status"] == "off"


async def test_products_cannot_be_created_in_a_disabled_category(client):
    """否则这些商品会永远上架不了，而运营在商品页上看不出原因。"""
    category_id = await make_category(client, "灯具")
    await client.put(f"/api/admin/shop/categories/{category_id}/status", json={"status": "disabled"})

    r = await client.post(
        "/api/admin/shop/products", json=product_payload(category_id, "落地灯")
    )
    assert r.status_code == 400, r.text


async def test_publishing_requires_a_cover_image(client):
    """图集第一张兼作列表页封面，没有封面的商品在列表里就是一块空白。"""
    category_id = await make_category(client, "配件")
    product_id = await make_product(client, category_id, "没图的", images=[])

    r = await client.put(
        f"/api/admin/shop/products/{product_id}/status", json={"status": "active"}
    )
    assert r.status_code == 400, r.text


async def test_taking_a_product_off_the_shelf_is_never_blocked(client):
    """下架无条件放行：出了问题要能立刻停售，任何校验都不该挡着这一步。"""
    category_id = await make_category(client, "沙发")
    product_id = await make_product(client, category_id, "云朵沙发")
    await publish(client, product_id)

    r = await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "off"})
    assert r.status_code == 200, r.text


async def test_an_active_product_cannot_have_all_its_images_removed(client):
    """否则它会在小程序列表里变成一块空白，而后台还标着「在售」。"""
    category_id = await make_category(client, "沙发")
    product_id = await make_product(client, category_id, "云朵沙发")
    await publish(client, product_id)

    r = await client.put(
        f"/api/admin/shop/products/{product_id}",
        json=product_payload(category_id, "云朵沙发", images=[]),
    )
    assert r.status_code == 400, r.text


async def test_product_limits_are_enforced(client):
    """上限在库和模型上各写了一份，这里验的是模型那份能回一句人话而不是 500。"""
    category_id = await make_category(client, "杂项")

    too_many_images = product_payload(
        category_id, "图太多", images=[f"static/shop/20260101/x{i}.jpg" for i in range(11)]
    )
    r = await client.post("/api/admin/shop/products", json=too_many_images)
    assert r.status_code == 400, r.text

    duplicate_params = product_payload(
        category_id,
        "参数重名",
        params=[{"name": "材质", "value": "实木"}, {"name": "材质", "value": "金属"}],
    )
    r = await client.post("/api/admin/shop/products", json=duplicate_params)
    assert r.status_code == 400, r.text

    bad_prefix = product_payload(category_id, "图前缀不对", images=["uploads/a.jpg"])
    r = await client.post("/api/admin/shop/products", json=bad_prefix)
    assert r.status_code == 400, r.text

    free_of_charge = product_payload(category_id, "零元购", priceCents=0)
    r = await client.post("/api/admin/shop/products", json=free_of_charge)
    assert r.status_code == 400, r.text


async def test_admin_product_list_filters_by_category_and_keyword(client):
    sofas = await make_category(client, "沙发")
    lamps = await make_category(client, "灯具")
    await make_product(client, sofas, "云朵沙发")
    await make_product(client, lamps, "落地灯")

    r = await client.get("/api/admin/shop/products", params={"categoryId": sofas})
    assert r.status_code == 200, r.text
    assert [x["title"] for x in r.json()["items"]] == [f"{PREFIX}云朵沙发"]

    r = await client.get("/api/admin/shop/products", params={"keyword": "落地"})
    assert [x["title"] for x in r.json()["items"]] == [f"{PREFIX}落地灯"]

    # 断言「我们造的两件都不在」，而不是「总数为 0」——这是跑在**共享开发库**上的
    # 集成测试，库里随时可能有别人上架的真实商品，断全局总数迟早误报
    r = await client.get("/api/admin/shop/products", params={"status": "active"})
    titles = [x["title"] for x in r.json()["items"]]
    assert f"{PREFIX}云朵沙发" not in titles
    assert f"{PREFIX}落地灯" not in titles


async def test_products_can_be_deleted_while_no_order_refers_to_them(client):
    """阶段一还没有订单表，删除是通的。阶段二起外键会把有订单的商品挡下来。"""
    category_id = await make_category(client, "杂项")
    product_id = await make_product(client, category_id, "录错的商品")

    r = await client.delete(f"/api/admin/shop/products/{product_id}")
    assert r.status_code == 200, r.text

    r = await client.delete(f"/api/admin/shop/products/{product_id}")
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------------------
# 小程序读


async def test_miniprogram_only_sees_products_on_the_shelf(client):
    """「在售」既是能否被购买的判据，也是能否被看到的判据，不存在中间态。"""
    category_id = await make_category(client, "沙发")
    listed = await make_product(client, category_id, "在售的")
    hidden = await make_product(client, category_id, "下架的")
    await publish(client, listed)

    r = await client.get("/api/shop/products")
    assert r.status_code == 200, r.text
    titles = [x["title"] for x in r.json()["items"]]
    assert f"{PREFIX}在售的" in titles
    assert f"{PREFIX}下架的" not in titles

    r = await client.get(f"/api/shop/products/{hidden}")
    assert r.status_code == 404, r.text


async def test_miniprogram_only_sees_enabled_categories(client):
    live = await make_category(client, "沙发")
    await make_category(client, "停用的")
    await client.put(
        f"/api/admin/shop/categories/{live}/status", json={"status": "active"}
    )
    disabled = await make_category(client, "要停的")
    await client.put(f"/api/admin/shop/categories/{disabled}/status", json={"status": "disabled"})

    r = await client.get("/api/shop/categories")
    assert r.status_code == 200, r.text
    ids = [x["id"] for x in r.json()["items"]]
    assert live in ids
    assert disabled not in ids


async def test_miniprogram_product_detail_carries_params_and_detail_images(client):
    """详情页三块内容：简介文案、参数表、详情图，缺一块页面就是残的。"""
    category_id = await make_category(client, "沙发")
    product_id = await make_product(client, category_id, "云朵沙发")
    await publish(client, product_id)

    r = await client.get(f"/api/shop/products/{product_id}")
    assert r.status_code == 200, r.text
    item = r.json()["item"]
    assert item["summary"] == "一段简介"
    assert item["params"] == [{"name": "材质", "value": "实木"}]
    assert item["priceCents"] == 1299900
    # 雪花 ID 必须是字符串，否则小程序侧 JSON.parse 会丢精度
    assert isinstance(item["id"], str)
    if cos.configured():
        assert len(item["images"]) == 1
        assert len(item["detailImages"]) == 1
        assert item["images"][0].startswith("http")


async def test_miniprogram_list_orders_by_sort_order(client):
    """后台看到的顺序就是用户看到的顺序，运营调完排序值能直接确认效果。"""
    category_id = await make_category(client, "沙发")
    low = await make_product(client, category_id, "排后面的", sortOrder=1, images=[IMAGE_A])
    high = await make_product(client, category_id, "排前面的", sortOrder=9, images=[IMAGE_C])
    await publish(client, low)
    await publish(client, high)

    r = await client.get("/api/shop/products", params={"categoryId": category_id})
    assert [x["title"] for x in r.json()["items"]] == [f"{PREFIX}排前面的", f"{PREFIX}排后面的"]


# ---------------------------------------------------------------------------
# 购物车
#
# 车是按人存的，所以这一节需要一个真实用户。不真连微信：code2session 打桩，
# 只验从 openid 往后的逻辑（同 test_users.py 的做法）。


def fake_session(openid: str):
    # 签名要跟真的一致：code2session 现在按小程序收 appid/secret（见 app/wechat.py）
    async def code2session(code: str, appid: str = "", secret: str = "") -> dict:
        return {"openid": openid, "unionid": None, "session_key": "fake-session-key"}

    return code2session


async def shopper_headers(monkeypatch, suffix: str = "1") -> dict:
    """登录一个测试用户，返回它的请求头。suffix 不同就是不同的人。"""
    monkeypatch.setattr(users_module, "code2session", fake_session(f"{TEST_OPENID}{suffix}"))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/auth/login", json={"code": "fake-code"})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
async def shopper(client, monkeypatch):
    """带用户登录态的客户端。client 那个是管理员的，两者不能混用。"""
    headers = await shopper_headers(monkeypatch)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as c:
        yield c


async def a_product_on_the_shelf(client) -> str:
    category_id = await make_category(client, "沙发")
    product_id = await make_product(client, category_id, "云朵沙发")
    await publish(client, product_id)
    return product_id


async def test_cart_requires_login(client):
    """车是按人存的，没登录态就无从谈起。client 带的是管理员 token，对用户接口无效。"""
    r = await client.get("/api/shop/cart", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401, r.text


async def test_adding_a_product_puts_it_in_the_cart(client, shopper):
    product_id = await a_product_on_the_shelf(client)

    r = await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 2})
    assert r.status_code == 200, r.text

    r = await shopper.get("/api/shop/cart")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    # 角标显示的是件数不是行数
    assert body["quantity"] == 2
    assert body["items"][0]["productId"] == product_id
    assert body["items"][0]["available"] is True


async def test_adding_the_same_product_twice_adds_up(client, shopper):
    """重复加购是累加数量，不是多出一行——库上的唯一约束也不允许多出一行。"""
    product_id = await a_product_on_the_shelf(client)

    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 2})
    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 3})

    body = (await shopper.get("/api/shop/cart")).json()
    assert body["count"] == 1
    assert body["items"][0]["quantity"] == 5


async def test_adding_beyond_the_cap_clamps_instead_of_failing(client, shopper):
    """连点加购不该弹「最多 99 件」的错误——封顶就好，返回真实数量。"""
    product_id = await a_product_on_the_shelf(client)

    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 99})
    r = await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 5})
    assert r.status_code == 200, r.text
    assert r.json()["quantity"] == 99


async def test_products_off_the_shelf_cannot_be_added(client, shopper):
    category_id = await make_category(client, "沙发")
    product_id = await make_product(client, category_id, "还没上架的")

    r = await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 1})
    assert r.status_code == 400, r.text


async def test_items_taken_off_the_shelf_stay_visible_but_unavailable(client, shopper):
    """下架的既不删也不藏：悄悄移除会让用户以为自己没加过，藏起来则解释不了件数对不上。"""
    product_id = await a_product_on_the_shelf(client)
    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 1})

    await client.put(f"/api/admin/shop/products/{product_id}/status", json={"status": "off"})

    body = (await shopper.get("/api/shop/cart")).json()
    assert body["count"] == 1
    assert body["items"][0]["available"] is False
    # 失效的不计进角标
    assert body["quantity"] == 0

    r = await shopper.delete("/api/shop/cart/invalid")
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 1
    assert (await shopper.get("/api/shop/cart")).json()["count"] == 0


async def test_quantity_can_be_changed_and_rows_removed(client, shopper):
    product_id = await a_product_on_the_shelf(client)
    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 1})
    item_id = (await shopper.get("/api/shop/cart")).json()["items"][0]["id"]

    r = await shopper.put(f"/api/shop/cart/{item_id}", json={"quantity": 7})
    assert r.status_code == 200, r.text
    assert (await shopper.get("/api/shop/cart")).json()["items"][0]["quantity"] == 7

    # 0 件不是删除，删除走 DELETE——前端少传一位不该把车清了
    assert (await shopper.put(f"/api/shop/cart/{item_id}", json={"quantity": 0})).status_code == 400

    assert (await shopper.delete(f"/api/shop/cart/{item_id}")).status_code == 200
    assert (await shopper.get("/api/shop/cart")).json()["count"] == 0


async def test_one_shopper_cannot_touch_another_shoppers_cart(client, shopper, monkeypatch):
    """只按行 id 改的话，拿到别人的 id 就能改别人的车，所以 WHERE 里必须带 user_id。"""
    product_id = await a_product_on_the_shelf(client)
    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 1})
    item_id = (await shopper.get("/api/shop/cart")).json()["items"][0]["id"]

    other = await shopper_headers(monkeypatch, "2")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test", headers=other) as c:
        assert (await c.put(f"/api/shop/cart/{item_id}", json={"quantity": 9})).status_code == 404
        assert (await c.delete(f"/api/shop/cart/{item_id}")).status_code == 404
        assert (await c.get("/api/shop/cart")).json()["count"] == 0

    # 原主人的车没被动过
    assert (await shopper.get("/api/shop/cart")).json()["items"][0]["quantity"] == 1


async def test_deleting_a_product_clears_it_from_carts(client, shopper):
    """商品硬删时购物车行跟着走（CASCADE），不是把车留成一条指向空气的记录。"""
    product_id = await a_product_on_the_shelf(client)
    await shopper.post("/api/shop/cart", json={"productId": product_id, "quantity": 1})

    assert (await client.delete(f"/api/admin/shop/products/{product_id}")).status_code == 200
    assert (await shopper.get("/api/shop/cart")).json()["count"] == 0

"""每日退款对账。解析部分是纯函数，同步部分需要库。**不连微信**。

这里的重点是**账单 CSV 的解析**：它有几个只在真实数据里才会出现的形状——
每个字段带反引号、末尾有汇总行、列顺序不保证——写错了不会报错，
只会静默地对错账或漏账，所以逐条钉住。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import users as users_module
from app import wxpay
from app.db import pool
from app.main import app
from app.order_no import ORDER_NO_PREFIX
from app.shop_reconcile import parse_refund_bill, reconcile_refunds

PREFIX = "ZZ测试"
TEST_OPENID = "test_openid_recon"
IMAGE_A = "static/shop/20260101/testa.jpg"

# 微信退款账单的真实形状：表头 + 数据行 + 两行汇总。
# 每个字段前有一个反引号——那是给 Excel 看的，防止 20 位订单号变成科学计数法
HEADER = (
    "`交易时间,`公众账号ID,`商户号,`微信订单号,`商户订单号,`用户标识,"
    "`交易类型,`交易状态,`付款银行,`货币种类,`应结订单金额,"
    "`微信退款单号,`商户退款单号,`退款金额,`退款类型,`退款状态,`商品名称"
)


def bill(*rows: str) -> str:
    """拼一份账单：表头 + 数据行 + 汇总行。"""
    summary = "总交易单数,总退款金额\n`2,`100.00"
    return "\n".join([HEADER, *rows, summary])


def ours(serial: str) -> str:
    """造一个**本环境**的订单号。

    不写死 AX：前缀按环境配置（生产 AX、开发 AD，见 app/order_no.py）。写死的话
    这几条用例只在生产配置下才通得过，而它们要验的恰恰是「只认本环境的号」——
    在开发配置下全部静默失败，等于把这条防线的测试废掉。
    """
    return f"{ORDER_NO_PREFIX}{serial}"


def foreign(serial: str) -> str:
    """造一个**另一个环境**的订单号（同一个商户号下的另一套前缀）。

    这是撞号那个问题的核心形状：账单是商户级的，里面同时有两个环境的退款。
    """
    other = "AD" if ORDER_NO_PREFIX == "AX" else "AX"
    return f"{other}{serial}"


def row(order_no: str, state: str = "SUCCESS") -> str:
    return (
        f"`2026-08-12 10:00:00,`wx123,`1600000000,`42000123,`{order_no},`oABC,"
        f"`JSAPI,`REFUND,`招商银行,`CNY,`1000.00,"
        f"`50300456,`RF789,`1000.00,`ORIGINAL,`{state},`云朵沙发"
    )


# ---------------------------------------------------------------------------
# 解析


def test_parses_successful_refunds():
    text = bill(row(ours("20260812000001")), row(ours("20260812000002")))
    assert parse_refund_bill(text) == [ours("20260812000001"), ours("20260812000002")]


def test_strips_the_backtick_wechat_adds_for_excel():
    """每个字段前的反引号是给 Excel 的，程序读的时候必须剥掉，
    否则订单号会带着 ` 去匹配，一笔都对不上。"""
    text = bill(row(ours("20260812000003")))
    assert parse_refund_bill(text) == [ours("20260812000003")]


def test_skips_the_summary_rows():
    """账单末尾是汇总行，列数与数据行不同。
    靠「取不到订单号就跳过」滤掉，不按行号——按行号在微信改汇总行数时会静默失效。"""
    assert parse_refund_bill(bill(row(ours("20260812000004")))) == [ours("20260812000004")]


def test_ignores_refunds_that_did_not_succeed():
    """申请中或失败的退款，钱还在我们这儿，不能置成已退款。"""
    text = bill(row(ours("20260812000005"), state="PROCESSING"), row(ours("20260812000006")))
    assert parse_refund_bill(text) == [ours("20260812000006")]


def test_ignores_order_numbers_that_are_not_ours():
    """同一个商户号可能还有别的系统在用，别人的单不该被我们改。"""
    text = bill(row("OTHER20260812001"), row(ours("20260812000007")))
    assert parse_refund_bill(text) == [ours("20260812000007")]


def test_ignores_the_other_environments_orders():
    """**撞号那个问题的回归**。

    开发环境与生产环境共用同一个微信商户号，所以这份商户级账单里同时有两边的
    退款记录。不按前缀过滤的话，开发环境退一笔测试单，生产上同号的那笔真实订单
    第二天就会被这个每日任务自动置成「已退款」——全自动，没有人工环节能拦。
    """
    text = bill(row(foreign("20260812000009")), row(ours("20260812000010")))
    assert parse_refund_bill(text) == [ours("20260812000010")]


def test_finds_columns_by_name_not_position():
    """列顺序变了也要对。按下标取的写法在微信增删列时会静默取错值，
    而那种错误没有任何报错——只会把别的列当成订单号。"""
    header = "`商户订单号,`交易时间,`退款状态,`退款金额"
    text = "\n".join(
        [header, f"`{ours('20260812000008')},`2026-08-12 10:00:00,`SUCCESS,`1000.00"]
    )
    assert parse_refund_bill(text) == [ours("20260812000008")]


def test_bails_out_when_the_columns_are_unrecognisable():
    """列名对不上说明微信改了格式。宁可整批不处理也不猜下标——
    猜错会把别的列当订单号，那比不对账更糟。"""
    text = "`some,`unknown,`format\n`a,`b,`c"
    assert parse_refund_bill(text) == []


def test_handles_a_bill_with_no_refunds():
    """绝大多数日子是这样：只有表头和汇总。"""
    assert parse_refund_bill(bill()) == []


def test_handles_an_empty_response():
    assert parse_refund_bill("") == []


# ---------------------------------------------------------------------------
# 同步


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
def bills(monkeypatch):
    """打桩账单下载。state['byDate'] 决定每一天返回什么。"""
    state: dict = {"byDate": {}, "requested": []}

    async def refund_bill(bill_date: str) -> str:
        state["requested"].append(bill_date)
        if bill_date not in state["byDate"]:
            raise wxpay.PayError("账单尚未生成")
        return state["byDate"][bill_date]

    async def jsapi_order(**kwargs):
        return {"timeStamp": "1", "nonceStr": "n", "package": "prepay_id=x",
                "signType": "RSA", "paySign": "s"}

    monkeypatch.setattr(wxpay, "configured", lambda: True)
    monkeypatch.setattr(wxpay, "refund_bill", refund_bill)
    monkeypatch.setattr(wxpay, "jsapi_order", jsapi_order)
    return state


async def a_paid_order(client, shopper) -> tuple[str, str]:
    """造一笔已支付（待发货）的订单，返回 (id, order_no)。"""
    r = await client.post("/api/admin/shop/categories", json={"name": f"{PREFIX}对账分类"})
    category_id = r.json()["id"]
    r = await client.post(
        "/api/admin/shop/products",
        json={"categoryId": category_id, "title": f"{PREFIX}对账沙发", "summary": "",
              "priceCents": 100000, "images": [IMAGE_A], "detailImages": [], "params": [],
              "sortOrder": 0},
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
    order = r.json()["order"]
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET status='pending_ship', paid_at=now(), transaction_id=%s"
            " WHERE id = %s",
            (f"WX{order['id']}", int(order["id"])),
        )
    return order["id"], order["orderNo"]


async def test_reconcile_syncs_an_out_of_band_refund(client, shopper, bills):
    from datetime import date, timedelta

    order_id, order_no = await a_paid_order(client, shopper)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bills["byDate"][yesterday] = bill(row(order_no))

    n = await reconcile_refunds()
    assert n == 1

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    body = detail.json()["order"]
    assert body["status"] == "refunded"
    assert "商户平台" in body["refund"]["reason"]


async def test_reconcile_covers_three_days(client, shopper, bills):
    """三天窗口：任务错过一天、或账单当时还没生成，后面几次要能补上。"""
    from datetime import date, timedelta

    order_id, order_no = await a_paid_order(client, shopper)
    # 只有前天的账单里有这笔——昨天的还没生成
    day_before = (date.today() - timedelta(days=2)).isoformat()
    bills["byDate"][day_before] = bill(row(order_no))

    n = await reconcile_refunds()
    assert n == 1
    assert len(bills["requested"]) == 3, "每次跑要覆盖三天"

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["status"] == "refunded"


async def test_reconcile_is_idempotent(client, shopper, bills):
    """窗口重叠意味着同一天会被处理多次。第二次必须是空操作，
    否则重复的 error 日志会把真正的新事件淹掉。"""
    from datetime import date, timedelta

    _, order_no = await a_paid_order(client, shopper)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bills["byDate"][yesterday] = bill(row(order_no))

    assert await reconcile_refunds() == 1
    assert await reconcile_refunds() == 0, "已经是已退款的单不该再被同步一次"


async def test_reconcile_skips_orders_refunded_through_the_admin(client, shopper, bills):
    """走后台按钮退的单也会出现在账单里，但它已经是 refunded 了，
    不该被当成「系统外退款」再记一次原因。"""
    from datetime import date, timedelta

    order_id, order_no = await a_paid_order(client, shopper)
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET status='refunded', refunded_at=now(), refund_reason=%s"
            " WHERE id = %s",
            ("客户取消订单", int(order_id)),
        )

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bills["byDate"][yesterday] = bill(row(order_no))

    assert await reconcile_refunds() == 0

    detail = await client.get(f"/api/admin/shop/orders/{order_id}")
    assert detail.json()["order"]["refund"]["reason"] == "客户取消订单", "原有原因不该被覆盖"


async def test_reconcile_survives_a_missing_bill(client, shopper, bills):
    """某天的账单取不到（还没生成、网络失败）不该影响其余几天。"""
    assert await reconcile_refunds() == 0
    assert len(bills["requested"]) == 3


# ---------------------------------------------------------------------------
# 账单下载的解码
#
# 这三条对应线上实测出来的三个真问题（在开发环境对真实微信接口验过）：
#   1. tar_type=None 被 SDK 拼成字面量 "None"，微信 400 PARAM_ERROR
#   2. download_bill 返回的是 bytes 不是 str，直接进 csv 会 TypeError
#   3. 账单带 BOM，按列名找列会因为多一个 ﻿ 而整批跳过


def test_decode_bill_unpacks_gzip():
    """tar_type=GZIP 时下载回来的是 gzip 字节流。"""
    import gzip

    from app.wxpay import decode_bill

    raw = "商户订单号,退款状态\nAX20260812000001,SUCCESS\n"
    assert decode_bill(gzip.compress(raw.encode("utf-8"))) == raw


def test_decode_bill_handles_plain_bytes():
    """留一条不压缩的路：微信哪天改了默认行为不该整批解不开。"""
    from app.wxpay import decode_bill

    assert decode_bill("表头\n一行\n".encode("utf-8")) == "表头\n一行\n"


def test_decode_bill_strips_the_bom():
    """**最阴的一条**。微信的账单带 BOM，用 utf-8 解出来第一列列名会多一个
    \\ufeff，而 shop_reconcile 是按列名找列的（header.index('商户订单号')），
    差这一个看不见的字符就判定「列名对不上」而整批跳过——不报错，只是永远不对账。"""
    import gzip

    from app.wxpay import decode_bill

    raw = "﻿商户订单号,退款状态\nAX20260812000001,SUCCESS\n"
    decoded = decode_bill(gzip.compress(raw.encode("utf-8")))
    assert not decoded.startswith("﻿")
    assert decoded.splitlines()[0].split(",")[0] == "商户订单号"


def test_decode_bill_passes_str_through():
    from app.wxpay import decode_bill

    assert decode_bill("已经是文本") == "已经是文本"


def test_parse_survives_a_bom_that_reached_the_parser():
    """第二道防线：BOM 万一漏到解析这一层，也不能把整批账单丢掉。"""
    text = "﻿`商户订单号,`退款状态\n`" + ours("20260812000011") + ",`SUCCESS"
    assert parse_refund_bill(text) == [ours("20260812000011")]


async def test_a_day_without_refunds_is_not_an_error(client, shopper, bills, caplog):
    """微信对「当天没有退款」返回 400 NO_STATEMENT_EXIST，不是一份空表头文件。

    这是**绝大多数日子的常态**，不能当成错误——否则每天都会有 warning，
    真正的失败反而被淹掉。
    """
    import logging

    from app import wxpay

    async def refund_bill(bill_date: str) -> str:
        raise wxpay.BillNotExist(f"{bill_date} 没有退款账单")

    bills["byDate"] = {}
    import app.wxpay as wxpay_module

    original = wxpay_module.refund_bill
    wxpay_module.refund_bill = refund_bill
    try:
        with caplog.at_level(logging.WARNING, logger="app.shop_reconcile"):
            assert await reconcile_refunds() == 0
    finally:
        wxpay_module.refund_bill = original

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "没有退款的日子不该产生 warning"
    )


async def test_a_real_failure_is_visible(client, shopper, bills, caplog):
    """真出错（网络、鉴权、参数）要留下 warning——
    连续几天都失败就说明对账其实一直没跑成，那必须看得见。"""
    import logging

    from app import wxpay

    async def refund_bill(bill_date: str) -> str:
        raise wxpay.PayError("申请退款账单失败")

    import app.wxpay as wxpay_module

    original = wxpay_module.refund_bill
    wxpay_module.refund_bill = refund_bill
    try:
        with caplog.at_level(logging.WARNING, logger="app.shop_reconcile"):
            assert await reconcile_refunds() == 0
    finally:
        wxpay_module.refund_bill = original

    assert [r for r in caplog.records if r.levelno >= logging.WARNING], "真失败必须留下 warning"

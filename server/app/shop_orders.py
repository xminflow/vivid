"""安玺·集 订单 —— 下单、我的订单、详情、取消。

设计见 docs/superpowers/specs/2026-08-10-anxi-ji-design.md。
后台的订单管理（发货、退款、备注）在 app/shop_admin.py。

本模块**不含支付**。下单只把订单建到 pending_pay 就结束，拉起支付、回调、
主动查单是阶段三的事（app/wxpay.py）。这样切是有意的：订单能不能正确地建出来、
金额对不对、快照全不全，这些在没有支付的情况下就该验完——等接上真钱再发现建单
逻辑有问题，代价完全不同。

三条贯穿全模块的规矩：

1. **金额一律服务端算**。请求里压根没有金额字段，客户端传什么都不看。
   订单行的单价取自下单那一刻 shop_products 的当前价格。
2. **一切都存快照**。标题、单价、封面、收货地址六列，全部落在订单上。
   之后商品改价改名、用户改地址删地址，都不影响已下的单。
3. **每条 SQL 都带 user_id**。订单 id 是客户端可见的值，只按 id 查就等于
   谁拿到别人的单号就能看到别人的收件人、手机号和买了什么。
"""

import logging
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from . import cos, snowflake, wxpay
from .db import pool
from .models import MAX_ORDER_TOTAL_CENTS, ORDER_STATUSES, OrderCreateIn
from .order_no import next_order_no
from .paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page
from .shop_pay import start_payment
from .users import current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shop"], dependencies=[Depends(current_user)])


def _cover(keys: list[str] | None) -> str:
    """商品图集的第一张兼作封面，存的是 COS 对象键。

    取不到就存空串而不是 None：订单行的 image_snapshot 是 NOT NULL，
    而「商品当时没有图」是完全可能的（运营先建了商品再补图，随后就被买走了）。
    """
    return (keys or [""])[0] if keys else ""


def _cover_url(key: str) -> str | None:
    """快照里的对象键换成公开直链。COS 没配时返回 None，小程序渲染占位图。"""
    if not key or not cos.configured():
        return None
    return cos.object_url(key)


def _order_out(row: dict) -> dict:
    """订单出接口的公共字段。金额一律用「分」，前端自己换算成元。"""
    return {
        "id": str(row["id"]),
        "orderNo": row["order_no"],
        "status": row["status"],
        "totalCents": row["total_cents"],
        "createdAt": row["created_at"].isoformat(),
        "paidAt": row["paid_at"].isoformat() if row.get("paid_at") else None,
        "shippedAt": row["shipped_at"].isoformat() if row.get("shipped_at") else None,
        "receivedAt": row["received_at"].isoformat() if row.get("received_at") else None,
    }


def _item_out(row: dict) -> dict:
    """订单行。一律取快照列，**任何场景都不回查 shop_products 的当前值**——
    用户看到的必须是他下单时看到的那个标题和价格。"""
    return {
        "productId": str(row["product_id"]),
        "title": row["title_snapshot"],
        "priceCents": row["price_cents_snapshot"],
        "cover": _cover_url(row["image_snapshot"]),
        "quantity": row["quantity"],
    }


@router.post("/api/shop/orders")
async def create_order(body: OrderCreateIn, user: Annotated[dict, Depends(current_user)]) -> dict:
    """下单。建到 pending_pay 为止，不拉起支付（阶段三接）。

    整个过程在一个事务里：取号、写订单、写订单行。中间任何一步失败，
    号也一起回滚——所以单号会有空洞，这是可以接受的（单号只要唯一，不要求连续）。

    **不清购物车**。清车要等支付成功，见 shop_orders.source 那一列的注释。
    用户下了单没付，车里的东西必须还在。
    """
    address_id = int(body.address_id)
    wanted = {int(item.product_id): item.quantity for item in body.items}

    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                address = await (
                    await conn.execute(
                        """
                        SELECT receiver, phone, province, city, district, detail
                          FROM shop_addresses WHERE id = %s AND user_id = %s
                        """,
                        (address_id, user["id"]),
                    )
                ).fetchone()
                if address is None:
                    # 带 user_id 查，所以「别人的地址」和「不存在」在这里是同一个结果，
                    # 这正是想要的：不能让人拿别人的地址 id 试出存在性
                    raise HTTPException(status_code=400, detail="请选择收货地址")

                # 一次查完所有商品，不在循环里逐个查。ANY 而不是 IN 是 psycopg 的写法
                products = await (
                    await conn.execute(
                        """
                        SELECT id, title, price_cents, images, status
                          FROM shop_products WHERE id = ANY(%s)
                        """,
                        (list(wanted),),
                    )
                ).fetchall()
                by_id = {row["id"]: row for row in products}

                # 缺的和下架的一起报，措辞不区分：对用户来说都是「这件买不了了」。
                # 这里必须逐件校验而不是信任前端——商品可能在用户停留在结算页的
                # 那几分钟里被运营下架了
                unavailable = [
                    pid for pid in wanted if pid not in by_id or by_id[pid]["status"] != "active"
                ]
                if unavailable:
                    logger.info(
                        "下单被拒：商品不可购 user_id=%s product_ids=%s", user["id"], unavailable
                    )
                    raise HTTPException(
                        status_code=400, detail="部分商品已下架，请返回购物车确认后再试"
                    )

                # 来源是购物车时，逐件确认它真的在这个人的车里。
                # 不确认的话，前端传 source='cart' 加一个从没加过购的商品 id，
                # 支付成功后那次「清车」就成了无意义的删除——不会出错，但订单的
                # source 字段就不再可信，将来对账时说不清这单到底从哪来
                if body.source == "cart":
                    in_cart = await (
                        await conn.execute(
                            "SELECT product_id FROM shop_cart_items "
                            " WHERE user_id = %s AND product_id = ANY(%s)",
                            (user["id"], list(wanted)),
                        )
                    ).fetchall()
                    missing = set(wanted) - {row["product_id"] for row in in_cart}
                    if missing:
                        logger.warning(
                            "下单被拒：声称来自购物车但车里没有 user_id=%s product_ids=%s",
                            user["id"],
                            sorted(missing),
                        )
                        raise HTTPException(
                            status_code=400, detail="购物车已变化，请返回购物车重新结算"
                        )

                # 金额服务端算。请求里没有金额字段，无从被篡改
                total_cents = sum(by_id[pid]["price_cents"] * qty for pid, qty in wanted.items())
                if total_cents > MAX_ORDER_TOTAL_CENTS:
                    # 库上的 CHECK 只管 > 0，上限在这里挡，为的是能回一句人话
                    raise HTTPException(status_code=400, detail="订单金额超出上限，请分几单下")

                order_id = snowflake.next_id()
                order_no = await next_order_no(conn)

                order = await (
                    await conn.execute(
                        """
                        INSERT INTO shop_orders
                            (id, order_no, user_id, source, total_cents,
                             receiver, phone, province, city, district, detail)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                     RETURNING id, order_no, status, total_cents, created_at
                        """,
                        (
                            order_id,
                            order_no,
                            user["id"],
                            body.source,
                            total_cents,
                            address["receiver"],
                            address["phone"],
                            address["province"],
                            address["city"],
                            address["district"],
                            address["detail"],
                        ),
                    )
                ).fetchone()

                # 订单行按请求里的顺序写，展示顺序就是用户勾选的顺序
                await conn.cursor().executemany(
                    """
                    INSERT INTO shop_order_items
                        (id, order_id, product_id, title_snapshot,
                         price_cents_snapshot, image_snapshot, quantity)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            snowflake.next_id(),
                            order_id,
                            pid,
                            by_id[pid]["title"],
                            by_id[pid]["price_cents"],
                            _cover(by_id[pid]["images"]),
                            qty,
                        )
                        for pid, qty in wanted.items()
                    ],
                )
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("下单失败 user_id=%s", user["id"])
        raise HTTPException(status_code=500, detail="下单失败，请稍后再试") from None

    logger.info(
        "下单成功 user_id=%s order_no=%s source=%s 金额=%d 分 行数=%d",
        user["id"],
        order_no,
        body.source,
        total_cents,
        len(wanted),
    )

    # 建单已经提交了，接下来向微信下单。**这一步失败不回滚订单**：
    # 订单本身是有效的，用户可以在订单详情里点「去支付」重试（POST /orders/{id}/pay）。
    # 把它绑进同一个事务的话，微信抖一下就会让用户白填一遍地址
    pay_params = None
    if wxpay.configured():
        try:
            pay_params = await start_payment(order, user["id"])
        except (wxpay.PayNotConfigured, wxpay.PayError):
            logger.exception("下单后拉起支付失败，订单保留待付款 order_no=%s", order_no)
        except HTTPException:
            # start_payment 对「订单没有明细」「用户没有 openid」这类情况抛 HTTPException。
            # 建单已经提交了，这一步失败不该让整个下单变成错误响应——
            # 订单是有效的，用户可以在详情页点「去支付」重试
            logger.exception("下单后拉起支付失败，订单保留待付款 order_no=%s", order_no)
        except psycopg.Error:
            logger.exception("下单后拉起支付失败（库） order_no=%s", order_no)
    else:
        # 没配支付时订单照样建得出来，停在待付款。这样不涉及交易的功能
        # 在没有支付配置的机器上仍然可以开发和验收
        logger.warning("支付未配置，订单停在待付款 order_no=%s", order_no)

    return {"ok": True, "order": _order_out(order), "payParams": pay_params}


@router.get("/api/shop/orders")
async def list_orders(
    user: Annotated[dict, Depends(current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")] = DEFAULT_PAGE_SIZE,
    status: Annotated[str, Query(max_length=20)] = "",
) -> dict:
    """我的订单。status 留空是全部，对应小程序订单页的「全部」tab。

    列表也带订单行：家具单一般就一两行，为了列表页显示缩略图再发一轮请求不值当。
    """
    conditions = ["user_id = %s"]
    params: list[Any] = [user["id"]]

    if status:
        if status not in ORDER_STATUSES:
            # 枚举传错直接 400，不带着脏值查库返回空列表——那样前端会以为「没有订单」
            raise HTTPException(status_code=400, detail="订单状态不合法")
        conditions.append("status = %s")
        params.append(status)

    total, rows = await count_and_page(
        "shop_orders",
        "id, order_no, status, total_cents, created_at, paid_at, shipped_at, received_at",
        conditions,
        params,
        page,
        page_size,
        order_by="created_at DESC, id DESC",
    )

    items = [_order_out(row) for row in rows]
    if items:
        try:
            async with pool.connection() as conn:
                lines = await (
                    await conn.execute(
                        """
                        SELECT order_id, product_id, title_snapshot,
                               price_cents_snapshot, image_snapshot, quantity
                          FROM shop_order_items
                         WHERE order_id = ANY(%s)
                         ORDER BY order_id, id
                        """,
                        ([int(item["id"]) for item in items],),
                    )
                ).fetchall()
        except psycopg.Error:
            logger.exception("查询订单行失败 user_id=%s", user["id"])
            raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

        grouped: dict[int, list[dict]] = {}
        for line in lines:
            grouped.setdefault(line["order_id"], []).append(_item_out(line))
        for item in items:
            item["items"] = grouped.get(int(item["id"]), [])

    return {"ok": True, "total": total, "page": page, "pageSize": page_size, "items": items}


@router.get("/api/shop/orders/{order_id}")
async def get_order(order_id: int, user: Annotated[dict, Depends(current_user)]) -> dict:
    """订单详情。比列表多出地址快照与物流信息。"""
    try:
        async with pool.connection() as conn:
            order = await (
                await conn.execute(
                    """
                    SELECT id, order_no, status, source, total_cents, created_at,
                           receiver, phone, province, city, district, detail,
                           paid_at, shipping_type, shipping_company, tracking_no,
                           shipped_at, received_at, closed_at, close_reason,
                           refunded_at, refund_reason
                      FROM shop_orders WHERE id = %s AND user_id = %s
                    """,
                    (order_id, user["id"]),
                )
            ).fetchone()
            if order is None:
                raise HTTPException(status_code=404, detail="订单不存在")

            lines = await (
                await conn.execute(
                    """
                    SELECT product_id, title_snapshot, price_cents_snapshot,
                           image_snapshot, quantity
                      FROM shop_order_items WHERE order_id = %s ORDER BY id
                    """,
                    (order_id,),
                )
            ).fetchall()
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("查询订单详情失败 user_id=%s order_id=%s", user["id"], order_id)
        raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

    detail = _order_out(order)
    detail.update(
        {
            "source": order["source"],
            "address": {
                "receiver": order["receiver"],
                "phone": order["phone"],
                "province": order["province"],
                "city": order["city"],
                "district": order["district"],
                "detail": order["detail"],
            },
            "shipping": {
                "type": order["shipping_type"],
                "company": order["shipping_company"],
                "trackingNo": order["tracking_no"],
            },
            "closeReason": order["close_reason"],
            # 退款原因给用户看，他有权知道为什么被退了钱
            "refundReason": order["refund_reason"],
            "refundedAt": order["refunded_at"].isoformat() if order["refunded_at"] else None,
            "items": [_item_out(line) for line in lines],
        }
    )
    return {"ok": True, "order": detail}


@router.post("/api/shop/orders/{order_id}/cancel")
async def cancel_order(order_id: int, user: Annotated[dict, Depends(current_user)]) -> dict:
    """用户取消订单，**只允许待付款**。

    已付款的单不能由用户自己取消——那是退款，退款要动真钱，只有超管在后台能做。

    状态判断写进 WHERE 而不是「先查再改」：查到改之间用户可能刚好付款成功，
    回调把状态迁走了，那时再改就会把一笔已付款的订单置成已关闭。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE shop_orders
                       SET status = 'closed', closed_at = now(), close_reason = 'user_cancel'
                     WHERE id = %s AND user_id = %s AND status = 'pending_pay'
                 RETURNING order_no
                    """,
                    (order_id, user["id"]),
                )
            ).fetchone()
            if row is not None:
                logger.info("用户取消订单 user_id=%s order_no=%s", user["id"], row["order_no"])
                return {"ok": True}

            # 没改到行，分清是「没有这单」还是「状态不对」——两者给用户的话不一样
            current = await (
                await conn.execute(
                    "SELECT status FROM shop_orders WHERE id = %s AND user_id = %s",
                    (order_id, user["id"]),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("取消订单失败 user_id=%s order_id=%s", user["id"], order_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    if current is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    if current["status"] == "closed":
        # 重复点取消不算错，当成已完成处理，省得用户看到一个红色报错
        return {"ok": True}
    raise HTTPException(status_code=409, detail="这笔订单已经付款，不能直接取消")

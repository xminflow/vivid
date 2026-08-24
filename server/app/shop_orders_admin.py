"""安玺·集 后台接口 —— 订单。

与 app/shop_admin.py 分开：那边是**商品目录**（改错了顶多显示不对），
这边是**交易凭证**（改错了牵涉真钱和客户信息）。将来要给「只管商品的运营」
和「能看订单的人」分权限，也只需要换这一个 router 上的依赖。

小程序侧的订单接口在 app/shop_orders.py。设计见
docs/superpowers/specs/2026-08-10-anxi-ji-design.md。

本文件覆盖阶段二能做的部分：**列表、详情、备注**。发货、退款要调微信，
属于阶段四，届时补在这里（退款还要额外挂 current_super）。

出参里包含收件人姓名、手机号和详细地址——这是客户隐私。整个 router 挂
current_admin，且不提供任何免鉴权的导出口子。
"""

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from . import cos, wechat_token, wxnotice, wxpay, wxship
from .admin_auth import current_admin, current_super
from .db import pool
from .models import (
    ORDER_STATUSES,
    ShopOrderRefundIn,
    ShopOrderRemarkIn,
    ShopOrderShipIn,
    ShopOrderTrackingIn,
)
from .paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page, like_pattern
from .shop_pay import new_refund_no, sync_refund_state

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/shop", tags=["shop-admin"], dependencies=[Depends(current_admin)]
)


def _cover_url(key: str) -> str | None:
    if not key or not cos.configured():
        return None
    return cos.object_url(key)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _day_bounds(value: str, *, end: bool) -> datetime:
    """把 'YYYY-MM-DD' 变成时间区间的端点。

    筛的是「哪天下的单」，而 created_at 是 timestamptz，直接和日期比会把当天
    00:00 之后的全部算进去、却把当天漏在外面。所以起点取当天 00:00，
    终点取**次日** 00:00 再用 < 比较——含当天，这与服务申请那边的做法一致。
    """
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式不正确") from None
    if end:
        parsed = parsed + timedelta(days=1)
    # 服务器与客户都在国内，不做时区换算，按本地时区解释这个日期
    return datetime.combine(parsed, time.min).astimezone(timezone.utc)


@router.get("/orders")
async def list_orders(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=60)] = "",
    status: Annotated[str, Query(max_length=20)] = "",
    created_from: Annotated[str, Query(max_length=10, alias="createdFrom")] = "",
    created_to: Annotated[str, Query(max_length=10, alias="createdTo")] = "",
) -> dict:
    """订单列表。关键词搜**单号或收件人**，日期筛的是下单日期（含当天）。

    列表不带订单行：一页 20 单、每单几行，多出来的那几十条查询只为了列表页
    显示一个缩略图不值当。要看明细点开详情。
    """
    conditions: list[str] = []
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        # 单号是精确的、收件人是模糊的，两者用 OR。% 和 _ 按字面量处理
        conditions.append("(order_no ILIKE %s ESCAPE '\\' OR receiver ILIKE %s ESCAPE '\\')")
        params.extend([like_pattern(keyword), like_pattern(keyword)])

    if status:
        if status not in ORDER_STATUSES:
            # 枚举传错直接 400，不带着脏值查库返回空列表——那样运营会以为「没有订单」
            raise HTTPException(status_code=400, detail="订单状态不合法")
        conditions.append("status = %s")
        params.append(status)

    if created_from:
        conditions.append("created_at >= %s")
        params.append(_day_bounds(created_from, end=False))
    if created_to:
        conditions.append("created_at < %s")
        params.append(_day_bounds(created_to, end=True))

    total, rows = await count_and_page(
        "shop_orders",
        """id, order_no, status, source, total_cents, receiver, phone,
           created_at, paid_at, shipped_at, shipping_type, tracking_no, remark""",
        conditions,
        params,
        page,
        page_size,
        order_by="created_at DESC, id DESC",
    )

    items = [
        {
            "id": str(row["id"]),
            "orderNo": row["order_no"],
            "status": row["status"],
            "source": row["source"],
            "totalCents": row["total_cents"],
            "receiver": row["receiver"],
            "phone": row["phone"],
            "createdAt": _iso(row["created_at"]),
            "paidAt": _iso(row["paid_at"]),
            "shippedAt": _iso(row["shipped_at"]),
            "shippingType": row["shipping_type"],
            "trackingNo": row["tracking_no"],
            "remark": row["remark"],
        }
        for row in rows
    ]
    return {"ok": True, "total": total, "page": page, "pageSize": page_size, "items": items}


@router.get("/orders/{order_id}")
async def get_order(order_id: int) -> dict:
    """订单详情。比列表多出地址快照、订单行、支付与退款信息。

    **不带 user_id 过滤**——这里是后台，本来就要能看所有人的订单。
    小程序侧那个接口每条 SQL 都带 user_id，两者的读者不同，不要照抄。
    """
    try:
        async with pool.connection() as conn:
            order = await (
                await conn.execute(
                    """
                    SELECT o.id, o.order_no, o.status, o.source, o.total_cents,
                           o.receiver, o.phone, o.province, o.city, o.district, o.detail,
                           o.transaction_id, o.paid_at,
                           o.shipping_type, o.shipping_company, o.tracking_no, o.shipped_at,
                           o.received_at, o.closed_at, o.close_reason,
                           o.refunded_at, o.refund_reason, o.refund_id,
                           o.remark, o.created_at,
                           u.nickname, u.member_name
                      FROM shop_orders o
                      JOIN users u ON u.id = o.user_id
                     WHERE o.id = %s
                    """,
                    (order_id,),
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
        logger.exception("查询订单详情失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

    return {
        "ok": True,
        "order": {
            "id": str(order["id"]),
            "orderNo": order["order_no"],
            "status": order["status"],
            "source": order["source"],
            "totalCents": order["total_cents"],
            # 下单人在小程序里的资料，和收件人不一定是同一个人
            "buyer": order["member_name"] or order["nickname"] or "",
            "address": {
                "receiver": order["receiver"],
                "phone": order["phone"],
                "province": order["province"],
                "city": order["city"],
                "district": order["district"],
                "detail": order["detail"],
            },
            "payment": {
                # 微信支付订单号。对账时要拿它去商户平台查
                "transactionId": order["transaction_id"],
                "paidAt": _iso(order["paid_at"]),
            },
            "shipping": {
                "type": order["shipping_type"],
                "company": order["shipping_company"],
                "trackingNo": order["tracking_no"],
                "shippedAt": _iso(order["shipped_at"]),
            },
            "receivedAt": _iso(order["received_at"]),
            "closedAt": _iso(order["closed_at"]),
            "closeReason": order["close_reason"],
            "refund": {
                "refundedAt": _iso(order["refunded_at"]),
                "reason": order["refund_reason"],
                "refundId": order["refund_id"],
            },
            "remark": order["remark"],
            "createdAt": _iso(order["created_at"]),
            "items": [
                {
                    "productId": str(line["product_id"]),
                    "title": line["title_snapshot"],
                    "priceCents": line["price_cents_snapshot"],
                    "cover": _cover_url(line["image_snapshot"]),
                    "quantity": line["quantity"],
                }
                for line in lines
            ],
        },
    }


async def _upload_shipping(order_id: int) -> str:
    """把某笔订单的物流信息回传给微信，成功则记 shipping_uploaded_at。

    返回一句给运营看的话（空串表示一切正常）。**任何失败都不抛**——
    货可能已经交给快递了，不能因为回传失败就把发货回滚。失败的单靠
    shipping_uploaded_at IS NULL 查得出来，也重试得了。

    **自己管连接，且调微信时不持有连接**。连接池只有 5 条（db.py），
    回传是一次会超时的网络调用，占着连接做它的话，几笔并发发货就能把池抽干，
    连不相干的接口一起 503。
    """
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT o.transaction_id, o.shipping_type, o.shipping_company, o.tracking_no,
                       u.openid,
                       (SELECT title_snapshot FROM shop_order_items
                         WHERE order_id = o.id ORDER BY id LIMIT 1) AS title
                  FROM shop_orders o JOIN users u ON u.id = o.user_id
                 WHERE o.id = %s
                """,
                (order_id,),
            )
        ).fetchone()

    if row is None or not row["transaction_id"]:
        # 没有微信支付订单号 = 这笔单没真的付过钱，本来就不该出现在发货流程里
        logger.error("回传物流信息失败：订单没有 transaction_id order_id=%s", order_id)
        return "这笔订单没有支付记录，物流信息未回传微信"

    if not wxship.configured():
        logger.error("回传物流信息失败：WX_APPID / WX_SECRET 未配置 order_id=%s", order_id)
        return "微信接口凭证未配置，物流信息未回传——请尽快处理，超时会影响交易权限"

    try:
        await wxship.upload_shipping_info(
            transaction_id=row["transaction_id"],
            openid=row["openid"],
            shipping_type=row["shipping_type"],
            item_desc=row["title"] or "商品",
            tracking_no=row["tracking_no"],
            express_company=row["shipping_company"],
        )
    except (wxship.ShipError, wechat_token.TokenError) as exc:
        # 已经在 wxship 里记过完整的 errcode/errmsg，这里只给运营一句话
        return f"发货已记录，但物流信息回传微信失败（{exc}）。请联系技术处理，超时会影响交易权限"
    except Exception:
        logger.exception("回传物流信息时发生意外 order_id=%s", order_id)
        return "发货已记录，但物流信息回传微信失败。请联系技术处理"

    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE shop_orders SET shipping_uploaded_at = now() WHERE id = %s", (order_id,)
        )
    return ""


async def _notify_shipped(order_id: int) -> None:
    """给用户发一条发货通知。**任何失败都不影响发货**，也不进返回值。

    与回传物流信息的区别：那件事是微信平台的硬要求（不做会影响交易权限），
    所以失败要在界面上警告运营；这件事只是用户体验，失败记日志就够，
    不该在运营的操作结果里多一句他也做不了什么的话。

    连接的处理同 _upload_shipping：读完就归还，再去调微信。
    """
    if not wxnotice.configured():
        return

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT o.order_no, o.shipping_company, o.tracking_no, o.shipped_at, u.openid,
                       (SELECT title_snapshot FROM shop_order_items
                         WHERE order_id = o.id ORDER BY id LIMIT 1) AS title
                  FROM shop_orders o JOIN users u ON u.id = o.user_id
                 WHERE o.id = %s
                """,
                (order_id,),
            )
        ).fetchone()
    if row is None:
        return

    await wxnotice.send_ship_notice(
        openid=row["openid"],
        order_id=str(order_id),
        order_no=row["order_no"],
        title=row["title"] or "商品",
        company=row["shipping_company"] or "",
        tracking_no=row["tracking_no"] or "",
        # 用库里的发货时刻而不是「现在」：两者通常只差不到一秒，但通知里显示的
        # 时间应当是这一单真实的发货时间，将来补发通知时也才是对的
        shipped_at=row["shipped_at"],
    )


@router.get("/express-companies")
async def list_express_companies() -> dict:
    """快递公司编码。发货表单的下拉用。

    内置一份常用的，不动态拉微信的全量列表：拉全量要额外一次 access_token 调用
    和一份缓存，而运营实际会用到的就这十几家。不在列表里的允许手填编码。
    """
    return {
        "ok": True,
        "items": [{"code": code, "name": name} for code, name in wxship.EXPRESS_COMPANIES],
    }


@router.post("/orders/{order_id}/ship")
async def ship_order(order_id: int, body: ShopOrderShipIn) -> dict:
    """发货。只能对已付款（待发货）的订单操作。

    两步：先把发货落库，再把物流信息回传微信。**顺序不能反**——
    先回传后落库的话，回传成功而落库失败时，微信认为已发货、我们不知道，
    对不上账；反过来则是「我们记了、微信还没收到」，那个状态查得出来也补得上。

    回传失败不影响发货本身的成功，但会在返回里带一句警告：微信对实物交易有
    发货时限，超时会判发货延迟并影响交易权限，运营必须知道这件事没做完。
    """
    try:
        async with pool.connection() as conn:
            precheck = await (
                await conn.execute(
                    "SELECT status, order_no FROM shop_orders WHERE id = %s", (order_id,)
                )
            ).fetchone()
            if precheck is None:
                raise HTTPException(status_code=404, detail="订单不存在")
            if precheck["status"] != "pending_ship":
                raise HTTPException(status_code=409, detail="只有待发货的订单可以发货")

        # 发货前向微信查一次这笔单是否已在系统外退款（商户超管、微信客诉、
        # 我们故障期间的应急操作都可能绕过后台）。**这是唯一会造成实物损失的场景**：
        # 一笔已退款的订单被发出去就是白送一件货。
        #
        # 放在这里而不是定时轮询：调用量 = 发货次数，与订单存量无关，
        # 而且精确挡在损失发生前一秒。查询失败按未退款处理，不阻断发货
        if await sync_refund_state(order_id, precheck["order_no"]):
            raise HTTPException(
                status_code=409,
                detail="这笔订单已在微信商户平台退款，已同步为已退款，请勿发货",
            )

        async with pool.connection() as conn:
            async with conn.transaction():
                current = await (
                    await conn.execute(
                        "SELECT status, order_no FROM shop_orders WHERE id = %s FOR UPDATE",
                        (order_id,),
                    )
                ).fetchone()
                if current is None:
                    raise HTTPException(status_code=404, detail="订单不存在")
                if current["status"] != "pending_ship":
                    raise HTTPException(
                        status_code=409, detail="只有待发货的订单可以发货"
                    )

                await conn.execute(
                    """
                    UPDATE shop_orders
                       SET status = 'pending_receive', shipped_at = now(),
                           shipping_type = %s, shipping_company = %s, tracking_no = %s
                     WHERE id = %s
                    """,
                    (
                        body.shipping_type,
                        body.shipping_company or None,
                        body.tracking_no or None,
                        order_id,
                    ),
                )

        # 事务外、**连接外**回传：网络调用不该占着行锁，也不该占着连接池里的一条，
        # 而且失败不该回滚发货
        warning = await _upload_shipping(order_id)
        # 通知用户。放在回传之后：回传是平台的硬要求，优先级更高
        await _notify_shipped(order_id)
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("发货失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="发货失败，请稍后再试") from None

    if body.shipping_type == "express" and not wxship.known_company(body.shipping_company):
        logger.warning("发货用了内置列表之外的物流公司编码：%s", body.shipping_company)

    logger.info(
        "发货 order_no=%s 方式=%s 运单号=%s 回传微信=%s",
        current["order_no"],
        body.shipping_type,
        body.tracking_no or "-",
        "失败" if warning else "成功",
    )
    return {"ok": True, "warning": warning}


@router.put("/orders/{order_id}/ship")
async def update_tracking(order_id: int, body: ShopOrderTrackingIn) -> dict:
    """改运单号。填错了要能改，改完必须**重新回传微信**——
    微信那边存的还是旧单号，不重传等于用户查不到物流。"""
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE shop_orders
                       SET shipping_company = %s, tracking_no = %s, shipping_uploaded_at = NULL
                     WHERE id = %s AND status IN ('pending_receive', 'completed')
                       AND shipping_type = 'express'
                 RETURNING order_no
                    """,
                    (body.shipping_company, body.tracking_no, order_id),
                )
            ).fetchone()
            if row is None:
                raise HTTPException(
                    status_code=409, detail="只有已用快递发出的订单可以改运单号"
                )
        # 连接外重传，理由同 ship_order
        warning = await _upload_shipping(order_id)
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("修改运单号失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    logger.info("修改运单号 order_no=%s 新单号=%s", row["order_no"], body.tracking_no)
    return {"ok": True, "warning": warning}


@router.post("/orders/{order_id}/refund", dependencies=[Depends(current_super)])
async def refund_order(order_id: int, body: ShopOrderRefundIn) -> dict:
    """整单退款。**仅超管**——这是不可逆的资金操作。

    只做整退，金额取订单的 total_cents，请求里没有金额字段。

    ## 三步，退款单号在第一步就定死

    1. 事务内锁行、校验状态、**取一个退款单号**（已经有就沿用），提交
    2. 连接归还之后调微信
    3. 事务内迁移状态

    重复退款靠的是「**同一笔订单永远只用一个 out_refund_no**」这一条不变量：
    微信对相同的 out_refund_no + 相同金额是幂等的，返回的是同一笔退款而不是
    第二笔。两个超管同时点、或者失败后重试，都只会退出去一次。

    这比「加个行锁」更彻底：锁只能覆盖库操作，而真正要防重的动作发生在
    微信那一侧，跨着一次网络调用——持锁跨越网络调用是更糟的选择（见
    shop_pay.sweep_expired_orders 里的同类取舍）。

    先调微信再改状态：反过来的话，微信退款失败而我们已经置成已退款，
    用户看到「已退款」却没收到钱，那是最难解释的一种故障。
    """
    if not wxpay.configured():
        raise HTTPException(status_code=503, detail="支付未配置，无法退款")

    refundable = ("pending_ship", "pending_receive", "completed")

    try:
        # ---- 第一步：锁行、校验、认领退款单号 ----
        async with pool.connection() as conn:
            async with conn.transaction():
                order = await (
                    await conn.execute(
                        """
                        SELECT order_no, status, total_cents, transaction_id, refund_id
                          FROM shop_orders WHERE id = %s FOR UPDATE
                        """,
                        (order_id,),
                    )
                ).fetchone()
                if order is None:
                    raise HTTPException(status_code=404, detail="订单不存在")
                # 未付款的单没有钱可退，已退款的不能再退一次
                if order["status"] not in refundable:
                    raise HTTPException(status_code=409, detail="这笔订单当前状态不能退款")
                if not order["transaction_id"]:
                    raise HTTPException(status_code=409, detail="这笔订单没有支付记录")

                # 已经有单号说明上一次退款调用已经发出去过（可能失败在网络上，
                # 也可能是并发的另一个请求刚认领）。沿用它——换一个新号就等于
                # 向微信发起第二笔退款，那才是真的重复退款
                refund_no = order["refund_id"] or new_refund_no()
                await conn.execute(
                    "UPDATE shop_orders SET refund_id = %s WHERE id = %s",
                    (refund_no, order_id),
                )

        # ---- 第二步：调微信。此时不持有任何连接，也不持有行锁 ----
        result = await wxpay.refund(
            out_trade_no=order["order_no"],
            out_refund_no=refund_no,
            total_cents=order["total_cents"],
            reason=body.reason,
        )

        # ---- 第三步：迁移状态 ----
        # WHERE 带状态判断：第二步耗时期间，对账任务可能已经把它同步成 refunded 了
        async with pool.connection() as conn:
            await conn.execute(
                """
                UPDATE shop_orders
                   SET status = 'refunded', refunded_at = now(),
                       refund_reason = %s, refund_id = %s
                 WHERE id = %s AND status IN ('pending_ship', 'pending_receive', 'completed')
                """,
                (body.reason, result.get("refund_id") or refund_no, order_id),
            )
    except HTTPException:
        raise
    except wxpay.PayError:
        # 微信那边没退成功，状态不动。日志里已经有微信的原始应答。
        # refund_id 保留着——重试时会沿用同一个号，微信侧幂等
        raise HTTPException(status_code=502, detail="向微信发起退款失败，请稍后再试") from None
    except psycopg.Error:
        logger.exception("退款失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="退款失败，请稍后再试") from None

    logger.info(
        "退款成功 order_no=%s 金额=%d 分 退款单号=%s 原因=%s",
        order["order_no"],
        order["total_cents"],
        refund_no,
        body.reason,
    )
    return {"ok": True}


@router.put("/orders/{order_id}/remark")
async def set_remark(order_id: int, body: ShopOrderRemarkIn) -> dict:
    """后台备注。只有运营看得到，用户端任何接口都不返回它。

    任何状态的订单都能记备注，包括已关闭和已退款的——那些恰恰是最需要
    写一句「为什么」的。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "UPDATE shop_orders SET remark = %s WHERE id = %s RETURNING order_no",
                    (body.remark, order_id),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("保存订单备注失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="订单不存在")
    logger.info("修改订单备注 order_no=%s 长度=%d", row["order_no"], len(body.remark))
    return {"ok": True}

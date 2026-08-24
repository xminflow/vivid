"""安玺·集 支付编排 —— 拉起支付、回调、主动查单、超时关单。

微信那一层在 app/wxpay.py，这里负责「什么时候调它、结果怎么落库」。

## 一段状态迁移，三个入口

支付成功这件事有两条通道会告诉我们，加上定时任务共三个入口：

  1. 回调   POST /api/shop/pay/notify   微信主动推，**无鉴权**，靠验签
  2. 查单   POST /api/shop/orders/{id}/sync-pay   小程序在 requestPayment 成功后轮询
  3. 扫描   定时任务里关超时单之前，会先查一次微信

三者最终都走 `_settle_paid()`。写成一段而不是三段，是因为「支付成功要做哪些事」
（置状态、记支付单号、清购物车）一旦分散，迟早有一条路少做一件。

## 幂等

`_settle_paid()` 先 `SELECT ... FOR UPDATE` 锁订单行，已经是 pending_ship 就直接
返回。这挡得住同进程内的并发；跨进程/跨副本靠 `shop_orders.transaction_id` 上的
唯一索引兜底——微信会重投回调，用户也会同时下拉刷新，两者撞在一起是常态而非意外。

## 小程序把 requestPayment 成功当作「已提交」而不是「已支付」

真正算数的是这里的状态。所以 sync-pay 是轮询接口，不是「告诉服务端我付好了」——
客户端说的话在钱这件事上没有任何权重。
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request

from . import payment_anomalies, wxpay
from .db import pool
from .order_no import ORDER_NO_PREFIX
from .users import current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shop"])

# 微信支付的交易状态。只有 SUCCESS 算付成功；REFUND 是已退款，
# 其余（NOTPAY / CLOSED / REVOKED / USERPAYING / PAYERROR）都不迁移状态
TRADE_SUCCESS = "SUCCESS"

# 剩余可支付时间不足这么久时，不再向微信下单。
#
# 微信要求 time_expire 是个未来时刻，而我们的 time_expire 锚定订单的 created_at
# （见 wxpay.expire_at_of）。用户在第 29 分钟点「去支付」，剩余窗口只有 1 分钟：
# 下单要么被微信拒，要么用户刚打开收银台就被我们的超时扫描关掉。
# 与其给他一个必然失败的收银台，不如直接说清楚
MIN_PAY_WINDOW_SECONDS = 120

# 超时关单扫描对同一笔单连续处理失败多少次之后放弃。
#
# 没有这个上限的话，永远处理不掉的单（微信侧不存在、金额对不上）会一直占着
# `ORDER BY created_at LIMIT 50` 的队头——它们恰恰是最老的，每轮都优先选中——
# 攒够一屏就让整个超时关单停摆，之后所有超时单永远停在待付款。
#
# 放弃之后靠这句查出来人工处理：
#   SELECT order_no, sweep_attempts FROM shop_orders
#    WHERE status = 'pending_pay' AND sweep_attempts >= 5;
SWEEP_MAX_ATTEMPTS = 5


def _description(title: str, count: int) -> str:
    """微信账单上显示的商品描述。太长会被截断，所以只带第一件 + 件数。"""
    head = title[:20]
    return f"{head} 等{count}件" if count > 1 else head


async def _settle_paid(
    conn: psycopg.AsyncConnection,
    order_id: int,
    transaction_id: str,
    paid_cents: int,
    *,
    source: payment_anomalies.AnomalySource,
) -> bool:
    """把一笔订单置为已支付。返回 True 表示这次调用真的迁移了状态。

    调用方必须已经开好事务——三个入口的事务边界不同（回调要尽快提交，
    查单可能还要接着做别的），所以事务由调用方管。source 标明是哪个入口，
    只用于异常台账：同一种异常从回调发现还是从扫描发现，排查方向不一样。

    **拒绝迁移的几种情况都会落进 payment_anomalies**。它们的共同点是「微信说钱
    收了，但我们没法把它落到这笔订单上」，而且三个入口最终都要向微信回 SUCCESS
    （重投一百次结果一样）。不落库的话，一笔已经到账的钱就只剩一行没人看的日志。
    """
    row = await (
        await conn.execute(
            """
            SELECT order_no, status, total_cents, user_id, source
              FROM shop_orders WHERE id = %s FOR UPDATE
            """,
            (order_id,),
        )
    ).fetchone()
    if row is None:
        logger.error("支付成功但订单不存在 order_id=%s transaction_id=%s", order_id, transaction_id)
        return False

    if row["status"] != "pending_pay":
        # 已经处理过了。回调重投、或回调与查单同时到达时走这里，是正常情况
        logger.info(
            "订单已不在待付款状态，跳过 order_id=%s status=%s", order_id, row["status"]
        )
        return False

    # 没有微信支付订单号就不能迁移状态。
    #
    # 它是幂等的依据（shop_orders.transaction_id 上有唯一索引），而空串**是会进
    # 那个部分索引的**（索引条件是 IS NOT NULL）——写进去之后第二笔同样缺号的单
    # 会撞唯一约束，在回调里表现为 500 加微信 24 小时重投。
    # 正常情况下微信必然带这个字段，缺了就是异常，按异常处理
    if not transaction_id:
        await payment_anomalies.record(
            conn,
            kind="missing_transaction_id",
            source=source,
            order_no=row["order_no"],
            order_id=order_id,
            paid_cents=paid_cents,
            expected_cents=row["total_cents"],
        )
        return False

    # 金额对不上是**严重事件**：要么我们建单时算错了，要么有人在中间改了金额。
    # 不迁移状态，留给人工查——自动放行等于认了一笔金额不符的支付
    if paid_cents != row["total_cents"]:
        await payment_anomalies.record(
            conn,
            kind="amount_mismatch",
            source=source,
            order_no=row["order_no"],
            order_id=order_id,
            transaction_id=transaction_id,
            paid_cents=paid_cents,
            expected_cents=row["total_cents"],
        )
        return False

    await conn.execute(
        """
        UPDATE shop_orders
           SET status = 'pending_ship', paid_at = now(), transaction_id = %s
         WHERE id = %s AND status = 'pending_pay'
        """,
        (transaction_id, order_id),
    )

    # 清购物车。**只有购物车来源的单才清**——详情页「立即购买」不该动用户的车。
    # 放在支付成功这一刻而不是下单时：下了单没付，车里的东西必须还在
    if row["source"] == "cart":
        removed = await conn.execute(
            """
            DELETE FROM shop_cart_items
             WHERE user_id = %s
               AND product_id IN (SELECT product_id FROM shop_order_items WHERE order_id = %s)
            """,
            (row["user_id"], order_id),
        )
        logger.info("支付成功后清理购物车 order_id=%s 清掉 %d 行", order_id, removed.rowcount)

    logger.info(
        "订单已支付 order_id=%s transaction_id=%s 金额=%d 分", order_id, transaction_id, paid_cents
    )
    return True


async def start_payment(order: dict, user_id: int) -> dict:
    """向微信下单并返回 wx.requestPayment 的参数。

    order 至少要有 id / order_no / total_cents / created_at。description 需要商品
    标题，所以这里顺手查一次订单行——不从调用方传进来，免得两个入口拼出不同的描述。

    **openid 在这里现查**，不从 current_user 那个 dict 拿：`users.USER_COLUMNS`
    刻意不含 openid（它和 session_key 一样只留在库里，任何接口都不返回）。
    往那个 dict 里加一个 openid，就等于让每个回显用户资料的接口都有机会漏出去。

    **自己管连接，而且在调微信之前就归还**。连接池只有 5 条（db.py），
    向微信下单是一次几百毫秒起步的网络调用，占着连接做这件事的话，
    微信慢一次就能让几笔并发下单把池抽干，连不相干的接口一起 503。
    """
    async with pool.connection() as conn:
        owner = await (
            await conn.execute("SELECT openid FROM users WHERE id = %s", (user_id,))
        ).fetchone()
        if owner is None or not owner["openid"]:
            logger.error("拉起支付失败：用户没有 openid user_id=%s", user_id)
            raise HTTPException(status_code=500, detail="拉起支付失败")
        openid = owner["openid"]

        lines = await (
            await conn.execute(
                "SELECT title_snapshot, quantity FROM shop_order_items"
                " WHERE order_id = %s ORDER BY id",
                (order["id"],),
            )
        ).fetchall()
        if not lines:
            raise HTTPException(status_code=500, detail="订单没有商品明细")

    # 这里**不记 last_pay_at**。那是 pay_order 那个重试接口的节流字段，
    # 由它自己在抢占时写。建单顺带的这一次下单记上去的话，用户下完单在收银台
    # 点了取消、马上又点「去支付」就会撞上节流——而那是完全正常的操作。
    # 要防的是循环调用 /pay，不是「下单之后紧接着付一次」

    # 支付截止时刻锚定订单的 created_at，与超时关单用的是同一个（wxpay.expire_at_of）。
    # 剩余窗口太短就别下单了：微信要求 time_expire 是未来时刻，而且用户刚打开
    # 收银台就会被我们的超时扫描关掉——给他一句人话，比给一个必然失败的收银台强
    expire_at = wxpay.expire_at_of(order["created_at"])
    remaining = (expire_at - datetime.now(timezone.utc)).total_seconds()
    if remaining < MIN_PAY_WINDOW_SECONDS:
        logger.info(
            "拒绝拉起支付：订单即将超时 order_no=%s 剩余=%d 秒", order["order_no"], int(remaining)
        )
        raise HTTPException(status_code=409, detail="这笔订单即将超时关闭，请重新下单")

    count = sum(line["quantity"] for line in lines)
    return await wxpay.jsapi_order(
        out_trade_no=order["order_no"],
        description=_description(lines[0]["title_snapshot"], count),
        total_cents=order["total_cents"],
        openid=openid,
        notify_url=wxpay.NOTIFY_URL,
        expire_at=expire_at,
    )


@router.post("/api/shop/orders/{order_id}/pay")
async def pay_order(order_id: int, user: Annotated[dict, Depends(current_user)]) -> dict:
    """重新拉起支付。用户下了单没付、退出去又回来时走这条。

    每次都向微信重新下单：prepay_id 有效期只有 2 小时，存下来复用会在过期后
    得到一个「支付失败」，而用户看不出为什么。重复下单同一个 out_trade_no
    微信是允许的（未支付时返回新的 prepay_id）。

    **带节流**（wxpay.ORDER_THROTTLE_SECONDS）。每次调用都真的向微信下一次单，
    没有节流的话，循环调这个接口等于无限消耗微信的下单频率配额，还各占一条
    数据库连接和一个阻塞线程。节流用一条**带条件的 UPDATE** 抢占而不是「先查再判」：
    并发的两个请求会都读到同一个旧的 last_pay_at，各自认为自己可以走。
    """
    if not wxpay.configured():
        logger.error("下单支付被拒：支付未配置，缺 %s", "、".join(wxpay.missing_config()))
        raise HTTPException(status_code=503, detail="支付暂未开通，请稍后再试")

    try:
        async with pool.connection() as conn:
            order = await (
                await conn.execute(
                    """
                    UPDATE shop_orders SET last_pay_at = now()
                     WHERE id = %s AND user_id = %s AND status = 'pending_pay'
                       AND (last_pay_at IS NULL
                            OR last_pay_at < now() - (%s * interval '1 second'))
                 RETURNING id, order_no, total_cents, created_at
                    """,
                    (order_id, user["id"], wxpay.ORDER_THROTTLE_SECONDS),
                )
            ).fetchone()

            if order is None:
                # 没抢到。分清三种原因，给用户的话完全不一样
                current = await (
                    await conn.execute(
                        "SELECT status FROM shop_orders WHERE id = %s AND user_id = %s",
                        (order_id, user["id"]),
                    )
                ).fetchone()
                if current is None:
                    raise HTTPException(status_code=404, detail="订单不存在")
                if current["status"] != "pending_pay":
                    raise HTTPException(status_code=409, detail="这笔订单不是待付款状态")
                raise HTTPException(status_code=429, detail="操作太频繁，请稍后再试")
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("拉起支付失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    # 抢占已经提交，连接也归还了，再去调微信。
    # 顺序不能反：把下单裹在同一个事务里的话，微信失败会连抢占一起回滚，
    # 于是「制造失败」就成了绕过节流的办法
    try:
        params = await start_payment(order, user["id"])
    except HTTPException:
        raise
    except wxpay.PayNotConfigured:
        raise HTTPException(status_code=503, detail="支付暂未开通，请稍后再试") from None
    except wxpay.PayError:
        raise HTTPException(status_code=502, detail="拉起支付失败，请稍后再试") from None
    except psycopg.Error:
        logger.exception("拉起支付失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    return {"ok": True, "payParams": params}


@router.post("/api/shop/orders/{order_id}/sync-pay")
async def sync_payment(order_id: int, user: Annotated[dict, Depends(current_user)]) -> dict:
    """主动向微信查单。掉单兜底。

    小程序在 wx.requestPayment 成功后轮询订单详情，详情接口若发现还是待付款
    就会引导到这里。**带节流**：距上次查微信不到 30 秒直接返回当前状态，
    否则用户反复下拉会把微信的查单接口打爆。
    """
    if not wxpay.configured():
        raise HTTPException(status_code=503, detail="支付暂未开通，请稍后再试")

    try:
        async with pool.connection() as conn:
            order = await (
                await conn.execute(
                    """
                    SELECT id, order_no, status, total_cents, last_query_at
                      FROM shop_orders WHERE id = %s AND user_id = %s
                    """,
                    (order_id, user["id"]),
                )
            ).fetchone()
            if order is None:
                raise HTTPException(status_code=404, detail="订单不存在")
            if order["status"] != "pending_pay":
                # 回调已经先到了，不用查
                return {"ok": True, "status": order["status"]}

            now = datetime.now(timezone.utc)
            last = order["last_query_at"]
            if last and (now - last).total_seconds() < wxpay.QUERY_THROTTLE_SECONDS:
                return {"ok": True, "status": order["status"], "throttled": True}

            await conn.execute(
                "UPDATE shop_orders SET last_query_at = now() WHERE id = %s", (order_id,)
            )

        # 查微信放在连接之外：网络调用可能要几百毫秒，不占着连接池
        result = await wxpay.query_order(order["order_no"])
        status = result.get("trade_state")

        if status != TRADE_SUCCESS:
            logger.info("主动查单：尚未支付 order_no=%s trade_state=%s", order["order_no"], status)
            return {"ok": True, "status": "pending_pay", "tradeState": status}

        async with pool.connection() as conn:
            async with conn.transaction():
                await _settle_paid(
                    conn,
                    order_id,
                    result.get("transaction_id", ""),
                    (result.get("amount") or {}).get("total", 0),
                    source="sync_pay",
                )
            row = await (
                await conn.execute("SELECT status FROM shop_orders WHERE id = %s", (order_id,))
            ).fetchone()
    except HTTPException:
        raise
    except wxpay.PayError:
        # 查不到不等于没付成功，让小程序继续轮询
        raise HTTPException(status_code=502, detail="查询支付结果失败，请稍后刷新") from None
    except psycopg.Error:
        logger.exception("主动查单失败 order_id=%s", order_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    return {"ok": True, "status": row["status"]}


@router.post("/api/shop/pay/notify")
async def pay_notify(request: Request) -> dict:
    """微信支付回调。**无鉴权**——它是微信服务器发来的，靠验签认身份。

    这条路由不挂 current_user，也不能挂：微信不会带我们的 token。
    安全性完全由 wxpay.verify_callback() 里的验签保证。

    返回体的形状是微信规定的：`{"code": "SUCCESS"}` 表示已受理，微信不再重投；
    任何其他响应都会让微信按退避策略重投（最长 24 小时）。所以**处理失败时要
    返回失败**，让它重投，而不是吞掉——吞掉就是订单永远停在待付款。
    """
    body = (await request.body()).decode("utf-8")
    # 验签必须用原始请求体字节，不能是解析后重新序列化的 JSON
    headers = dict(request.headers)

    try:
        resource = await wxpay.verify_callback(headers, body)
    except wxpay.PayNotConfigured:
        logger.error("收到支付回调但本机未配置支付，无法验签")
        return {"code": "FAIL", "message": "未配置"}
    except Exception:
        logger.exception("支付回调验签异常")
        return {"code": "FAIL", "message": "验签失败"}

    if resource is None:
        # 验签没过。可能是有人在打这个口子，也可能是配置错了
        return {"code": "FAIL", "message": "验签失败"}

    out_trade_no = resource.get("out_trade_no", "")
    trade_state = resource.get("trade_state")
    transaction_id = resource.get("transaction_id", "")
    paid_cents = (resource.get("amount") or {}).get("total", 0)

    if trade_state != TRADE_SUCCESS:
        # 非成功状态的通知照常受理，不让微信重投
        logger.info("支付回调：非成功状态 out_trade_no=%s trade_state=%s", out_trade_no, trade_state)
        return {"code": "SUCCESS"}

    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                order = await (
                    await conn.execute(
                        "SELECT id FROM shop_orders WHERE order_no = %s", (out_trade_no,)
                    )
                ).fetchone()
                if order is None:
                    # 单号不认识。返回 SUCCESS 让微信别再投——重投也不会认识，
                    # 但这说明有笔钱收了却对不上订单，必须落进台账而不只是日志
                    await payment_anomalies.record(
                        conn,
                        kind="order_not_found",
                        source="notify",
                        order_no=out_trade_no,
                        transaction_id=transaction_id,
                        paid_cents=paid_cents,
                    )
                    return {"code": "SUCCESS"}
                await _settle_paid(
                    conn, order["id"], transaction_id, paid_cents, source="notify"
                )
    except psycopg.Error:
        # 库出问题就让微信重投，不能吞
        logger.exception("支付回调落库失败 out_trade_no=%s", out_trade_no)
        return {"code": "FAIL", "message": "处理失败"}

    return {"code": "SUCCESS"}


async def _give_up_later(order_id: int, order_no: str) -> None:
    """这一轮没能处理掉，失败计数 +1；到上限就记一条 error 让人来看。

    没有这个计数的话，永远处理不掉的单会一直占着取数窗口的队头，见
    SWEEP_MAX_ATTEMPTS 的注释。
    """
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                UPDATE shop_orders SET sweep_attempts = sweep_attempts + 1
                 WHERE id = %s AND status = 'pending_pay'
             RETURNING sweep_attempts
                """,
                (order_id,),
            )
        ).fetchone()

    if row is not None and row["sweep_attempts"] >= SWEEP_MAX_ATTEMPTS:
        # error 级别：这笔单从此不再被自动处理，只能人工介入
        logger.error(
            "超时扫描已放弃这笔单，请人工处理 order_no=%s 连续失败=%d 次",
            order_no,
            row["sweep_attempts"],
        )


async def _close_locally(order_id: int, order_no: str, reason: str) -> bool:
    """只改我们自己的状态，不碰微信。返回是否真的改了。"""
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            UPDATE shop_orders
               SET status = 'closed', closed_at = now(), close_reason = %s
             WHERE id = %s AND status = 'pending_pay'
            """,
            (reason, order_id),
        )
    if result.rowcount:
        logger.info("超时关单 order_no=%s 原因=%s", order_no, reason)
    return bool(result.rowcount)


async def sweep_expired_orders() -> int:
    """把超时未付的订单关掉。定时任务调用，返回关掉的笔数。

    **先关微信再改自己的状态**：反过来的话，用户可能在我们置成已关闭之后、
    微信关单之前完成支付，钱收了但订单是关的。

    关单前还要先查一次：用户可能刚好在超时那一刻付了，查到已支付就走支付成功
    的分支而不是关单。

    ## 取数条件里的两个限定

    **只扫本环境前缀的单**。close_order 是按 out_trade_no 关的，而开发环境与
    生产环境共用同一个商户号（微信支付没有沙箱，见 app/order_no.py）。不限定前缀
    的话，一笔历史遗留的同号单会让我们去关**另一个环境里客户正在支付的那一单**。

    **跳过连续失败到上限的单**。有两类单永远处理不掉：微信侧根本不存在的
    （下单那一刻就没提交成功）、金额对不上被拒绝迁移的。它们又恰恰是最老的，
    `ORDER BY created_at` 保证每轮都优先选中——不设上限的话，攒够一屏
    就把整个扫描堵死，之后所有超时单永远停在待付款。

    ⚠️ 这里的 FOR UPDATE SKIP LOCKED **不构成跨副本的互斥**：连接在 SELECT 之后
    就归还了，事务提交、行锁随即释放，后面的查单和更新都在新连接里。多副本时
    两个 worker 可能选到同一笔单，各自去问一次微信。这不会出错——
    `UPDATE ... WHERE status = 'pending_pay'` 保证状态迁移只成功一次——
    代价仅仅是重复问一次微信。要真正互斥就得持锁跨越网络调用，那更糟。
    """
    if not wxpay.configured():
        return 0

    deadline = datetime.now(timezone.utc) - timedelta(minutes=wxpay.PAY_TIMEOUT_MINUTES)
    closed = 0

    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id, order_no FROM shop_orders
                 WHERE status = 'pending_pay'
                   AND created_at < %s
                   AND order_no LIKE %s
                   AND sweep_attempts < %s
                 ORDER BY created_at
                 LIMIT 50
                   FOR UPDATE SKIP LOCKED
                """,
                (deadline, f"{ORDER_NO_PREFIX}%", SWEEP_MAX_ATTEMPTS),
            )
        ).fetchall()

    for row in rows:
        try:
            result = await wxpay.query_order(row["order_no"])
        except wxpay.PayOrderNotExist:
            # 微信从来没见过这笔单——下单那一刻就没提交成功（订单按设计仍保留为
            # 待付款，见 shop_orders.create_order）。重试多少次都是这个答案，
            # 所以直接本地关掉，不去调 close_order（关一个不存在的单必然失败）
            if await _close_locally(row["id"], row["order_no"], "never_submitted"):
                closed += 1
            continue
        except wxpay.PayError:
            # 可以重试的失败（网络抖动、微信 5xx）。这一轮先跳过，下一轮再来。
            # 不能凭「查不到」就关单
            logger.warning("超时扫描：查单失败，跳过 order_no=%s", row["order_no"])
            await _give_up_later(row["id"], row["order_no"])
            continue

        if result.get("trade_state") == TRADE_SUCCESS:
            # 卡在超时边界上付成功的，按支付成功处理
            async with pool.connection() as conn:
                async with conn.transaction():
                    settled = await _settle_paid(
                        conn,
                        row["id"],
                        result.get("transaction_id", ""),
                        (result.get("amount") or {}).get("total", 0),
                        source="sweep",
                    )
            if not settled:
                # 已经付了，但我们不能认（金额不符 / 没有支付单号）。台账里已经记了，
                # 这里只负责别让它永远留在取数窗口里——状态仍是待付款，
                # 每轮都会被重新选中，而结果永远一样
                await _give_up_later(row["id"], row["order_no"])
            continue

        try:
            await wxpay.close_order(row["order_no"])
        except wxpay.PayOrderNotExist:
            # 查单说没付、关单说没这笔单：同上，本地关掉即可
            if await _close_locally(row["id"], row["order_no"], "never_submitted"):
                closed += 1
            continue
        except wxpay.PayError:
            # 微信关单失败就别改自己的状态，下一轮再试
            logger.warning("超时扫描：关单失败，跳过 order_no=%s", row["order_no"])
            await _give_up_later(row["id"], row["order_no"])
            continue

        if await _close_locally(row["id"], row["order_no"], "timeout"):
            closed += 1

    return closed


async def sync_refund_state(order_id: int, order_no: str) -> bool:
    """向微信查这一单，若它其实已退款就把状态同步过来。返回是否同步了。

    ## 为什么是「按需查一次」而不是轮询

    最早的写法是定时扫描：每分钟挑 20 笔待发货的单去问微信，每笔每小时一次。
    那个设计有个致命的量级问题——调用量正比于**订单存量**。只查待发货时还行
    （待发货是个短暂状态，稳态积压约等于两天的单量），可一旦想把已发货、已完成
    也纳入对账，条件就变成「近 30 天所有订单」：100 单/月是 7.2 万次调用/月，
    1000 单/月就是 72 万次，而且随业务增长没有上限。

    真正要防的损失只有一个：**运营把一笔已退款的订单发出去**。那就在发货那一刻
    查，不要全天候轮询：

      * 调用量 = 发货次数，每笔单最多一次，与订单存量无关
      * 精确挡在损失发生前一秒；轮询最多有一小时的窗口，挡不住这一秒

    账面准确性（已发货/已完成的单在平台被退款）不靠这里，靠每日账单对账——
    微信的交易账单接口一天一次就覆盖全部订单，与单量无关。见 README 的「对账」。
    """
    if not wxpay.configured():
        return False

    # 只查本环境的号。理由同 sweep_expired_orders：两个环境共用同一个商户号，
    # 拿一个别的环境的单号去查，得到的是**对方那笔订单**的状态——据此决定
    # 我们这边能不能发货是完全错误的。查不了就按未退款处理（与查单失败同一条路径），
    # 账面准确性还有每日对账兜底
    if not order_no.startswith(ORDER_NO_PREFIX):
        logger.warning("发货前查单跳过：订单号不属于本环境 order_no=%s", order_no)
        return False

    try:
        result = await wxpay.query_order(order_no)
    except wxpay.PayError:
        # 查不到不阻断发货：微信抖一下不该让运营发不了货。
        # 真有系统外退款而这次没查到，还有每日账单兜底
        logger.warning("发货前查单失败，按未退款处理 order_no=%s", order_no)
        return False

    if result.get("trade_state") != "REFUND":
        return False

    async with pool.connection() as conn:
        updated = await conn.execute(
            """
            UPDATE shop_orders
               SET status = 'refunded', refunded_at = now(),
                   refund_reason = COALESCE(NULLIF(refund_reason, ''), %s)
             WHERE id = %s AND status IN ('pending_ship', 'pending_receive')
            """,
            ("在微信商户平台退款（系统外操作，请到商户平台核对）", order_id),
        )

    if not updated.rowcount:
        return False

    # error 级别：这说明有人绕过了后台退款，是需要被看见的运营事件
    logger.error(
        "发货前发现这笔单已在系统外退款，已拦下并同步状态 order_no=%s。"
        "退款的正常入口是后台的退款按钮，商户平台操作不会回写本系统",
        order_no,
    )
    return True


async def sweep_auto_receipt() -> int:
    """发货满 10 天自动确认收货。定时任务调用，返回置为已完成的笔数。

    不与微信交互，纯粹是我们这边的状态迁移——所以支付没配也照跑。

    为什么要有这一步：用户很少主动点「确认收货」，订单会永远停在待收货，
    运营看不出哪些其实已经结束了。10 天是个折中：比常见快递时长长一截，
    又不至于让订单挂太久。

    幂等：WHERE 带 status 判断，重复执行不会把别的状态改错。
    """
    async with pool.connection() as conn:
        result = await conn.execute(
            """
            UPDATE shop_orders
               SET status = 'completed', received_at = now()
             WHERE status = 'pending_receive'
               AND shipped_at < now() - interval '10 days'
            """
        )
    if result.rowcount:
        logger.info("自动确认收货 %d 笔", result.rowcount)
    return result.rowcount


def new_refund_no() -> str:
    """商户退款单号。与订单号不同，退款单号只对微信有意义，不给用户看，
    所以直接用随机串，不占用订单号的取号序列。"""
    return "RF" + secrets.token_hex(12)

"""支付异常台账 —— 收了钱却对不上单的那几种情况。

## 为什么需要一张表

支付回调有三条路径会走到「微信说付成功了，但我们没法把它落到某笔订单上」：

  * `amount_mismatch`         金额与订单不符
  * `order_not_found`         单号在库里根本不存在
  * `missing_transaction_id`  微信说付成功却没给支付单号

这三种都必须向微信返回 SUCCESS 让它别再重投——重投一百次结果一模一样，
只会把回调队列堵住。于是**钱确实收了，而唯一的线索是一行 error 日志**，
没有人会主动去翻日志，等到发现时往往已经是客户找上门。

所以它们要落库：可查询、可在后台看见、可标记已处理。行不删——这是资金异常的
台账，处理完也要留着备查。

## 为什么写在业务事务里

`record()` 收一个已经开好的连接，跟着调用方的事务一起提交。回调那条路径本来就
在事务里判重和迁移状态，异常记录必须与那次判断**原子**：分开写的话，
「判定金额不符」和「记下这件事」之间任何一次崩溃都会让这笔钱彻底无声无息。
"""

import logging
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from .admin_auth import current_admin
from .db import pool
from .models import PaymentAnomalyResolveIn
from .paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page
from .snowflake import next_id

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/shop", tags=["shop-admin"], dependencies=[Depends(current_admin)]
)

AnomalyKind = Literal["amount_mismatch", "order_not_found", "missing_transaction_id"]
# 哪条通道发现的。排查时要先知道是回调、主动查单还是超时扫描发现的
AnomalySource = Literal["notify", "sync_pay", "sweep"]


async def record(
    conn: psycopg.AsyncConnection,
    *,
    kind: AnomalyKind,
    source: AnomalySource,
    order_no: str,
    order_id: int | None = None,
    transaction_id: str = "",
    paid_cents: int | None = None,
    expected_cents: int | None = None,
) -> None:
    """记一笔支付异常。conn 必须是调用方已经开好事务的连接，见模块注释。

    **不抛异常**。这是一条观测记录，它自己失败不该把支付回调也带下去——
    那会让微信不停重投一笔本来就处理不了的通知。写不进去就退回成一行日志，
    也就是加这张表之前的状态。

    ⚠️ INSERT 套在**嵌套事务（savepoint）**里，这是「不抛异常」能成立的前提。
    在一个已经开着的事务里执行失败的语句会让**整个事务进入 aborted 状态**，
    之后连提交都会报错——那时光把异常吞掉毫无意义，调用方仍然会在提交时炸，
    只是报错位置挪到了一个更难懂的地方。savepoint 让失败只回滚这一条。
    """
    try:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO payment_anomalies
                    (id, kind, source, order_no, order_id, transaction_id,
                     paid_cents, expected_cents)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    next_id(),
                    kind,
                    source,
                    order_no,
                    order_id,
                    transaction_id,
                    paid_cents,
                    expected_cents,
                ),
            )
    except psycopg.Error:
        logger.exception(
            "支付异常台账写入失败，只能留在日志里 kind=%s order_no=%s transaction_id=%s "
            "实付=%s 分 应付=%s 分",
            kind,
            order_no,
            transaction_id,
            paid_cents,
            expected_cents,
        )
        return

    # error 级别：这是「钱收了但订单没走通」，需要有人去处理，不是一条流水日志
    logger.error(
        "支付异常已记台账 kind=%s 来源=%s order_no=%s transaction_id=%s 实付=%s 分 应付=%s 分",
        kind,
        source,
        order_no,
        transaction_id,
        paid_cents,
        expected_cents,
    )


@router.get("/payment-anomalies")
async def list_anomalies(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")] = DEFAULT_PAGE_SIZE,
    resolved: Annotated[str, Query(max_length=10)] = "open",
) -> dict:
    """异常列表。默认只看未处理的——已处理的越积越多，默认全看等于没有默认。

    resolved: open（默认）/ done / all
    """
    if resolved not in ("open", "done", "all"):
        raise HTTPException(status_code=400, detail="resolved 只能是 open / done / all")

    conditions: list[str] = []
    if resolved == "open":
        conditions.append("resolved_at IS NULL")
    elif resolved == "done":
        conditions.append("resolved_at IS NOT NULL")

    total, rows = await count_and_page(
        "payment_anomalies",
        """id, kind, source, order_no, order_id, transaction_id,
           paid_cents, expected_cents, resolved_at, resolved_by, resolve_note, created_at""",
        conditions,
        [],
        page,
        page_size,
        order_by="created_at DESC, id DESC",
    )

    items = [
        {
            "id": str(row["id"]),
            "kind": row["kind"],
            "source": row["source"],
            "orderNo": row["order_no"],
            # 订单 id 可能为空（order_not_found 就是「没有这笔订单」）
            "orderId": str(row["order_id"]) if row["order_id"] is not None else None,
            "transactionId": row["transaction_id"],
            "paidCents": row["paid_cents"],
            "expectedCents": row["expected_cents"],
            "resolvedAt": row["resolved_at"].isoformat() if row["resolved_at"] else None,
            "resolvedBy": row["resolved_by"],
            "resolveNote": row["resolve_note"],
            "createdAt": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return {"ok": True, "total": total, "page": page, "pageSize": page_size, "items": items}


@router.post("/payment-anomalies/{anomaly_id}/resolve")
async def resolve_anomaly(
    anomaly_id: int,
    body: PaymentAnomalyResolveIn,
    admin: Annotated[dict, Depends(current_admin)],
) -> dict:
    """标记为已处理。**不改任何订单状态**——这里只是把「人已经看过并处理了」记下来。

    真正要做的事（补一笔订单、去商户平台退款、改状态）各有各的入口，
    在这里顺手做等于开一个绕过所有状态机的后门。

    处理说明必填：几个月后回看时，「谁在什么时候标了已处理」远不如
    「当时是怎么处理的」有用。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE payment_anomalies
                       SET resolved_at = now(), resolved_by = %s, resolve_note = %s
                     WHERE id = %s AND resolved_at IS NULL
                 RETURNING order_no
                    """,
                    (admin["username"], body.note, anomaly_id),
                )
            ).fetchone()
            if row is not None:
                logger.info(
                    "支付异常标记已处理 id=%s order_no=%s 处理人=%s",
                    anomaly_id,
                    row["order_no"],
                    admin["username"],
                )
                return {"ok": True}

            # 没改到行：分清「没有这条」和「已经处理过了」，两者给运营的话不一样
            current = await (
                await conn.execute(
                    "SELECT resolved_at FROM payment_anomalies WHERE id = %s", (anomaly_id,)
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("标记支付异常失败 id=%s", anomaly_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    if current is None:
        raise HTTPException(status_code=404, detail="这条记录不存在")
    # 重复点不算错，当成已完成——省得运营看到一个红色报错
    return {"ok": True}

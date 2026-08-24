"""安玺·集 收货地址簿。

设计见 docs/superpowers/specs/2026-08-10-anxi-ji-design.md。

这张表是「用户当前的地址」，不是「订单用过的地址」——订单里存的是下单那一刻的
六列快照（见 schema.sql 的 shop_orders）。所以这里可以随用户任意改删，
历史订单一律不受影响，不需要软删除，也不需要在删除时检查有没有订单引用它。

整个模块都要求登录，且每条 SQL 的 WHERE 都带 user_id：只按 id 查改删的话，
拿到别人的地址 id 就能读到别人的收件人和手机号。这不是多余的防御——
地址 id 会出现在下单请求里，是客户端可见的值。
"""

import logging
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException

from . import snowflake
from .db import pool
from .models import MAX_ADDRESSES, ShopAddressIn
from .users import current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shop"], dependencies=[Depends(current_user)])

# 列表与详情共用的列，顺序即出接口的顺序
_COLUMNS = "id, receiver, phone, province, city, district, detail, is_default"


def _out(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "receiver": row["receiver"],
        "phone": row["phone"],
        "province": row["province"],
        "city": row["city"],
        "district": row["district"],
        "detail": row["detail"],
        "isDefault": row["is_default"],
    }


async def _clear_default(conn: psycopg.AsyncConnection, user_id: int, keep_id: int | None) -> None:
    """把该用户其余地址的 is_default 清掉。

    库上有 (user_id) WHERE is_default 的部分唯一索引，所以「设新默认」必须在
    同一个事务里先清旧的，否则第二条就撞唯一索引。清的时候排除掉自己，
    免得刚设上又被清掉。
    """
    await conn.execute(
        "UPDATE shop_addresses SET is_default = false "
        " WHERE user_id = %s AND is_default AND id IS DISTINCT FROM %s",
        (user_id, keep_id),
    )


@router.get("/api/shop/addresses")
async def list_addresses(user: Annotated[dict, Depends(current_user)]) -> dict:
    """我的地址簿。默认地址排在最前，其余按新增时间倒序。"""
    try:
        async with pool.connection() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {_COLUMNS} FROM shop_addresses
                     WHERE user_id = %s
                     ORDER BY is_default DESC, created_at DESC, id DESC
                    """,
                    (user["id"],),
                )
            ).fetchall()
    except psycopg.Error:
        logger.exception("查询地址簿失败 user_id=%s", user["id"])
        raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

    return {"ok": True, "items": [_out(row) for row in rows]}


@router.post("/api/shop/addresses")
async def create_address(body: ShopAddressIn, user: Annotated[dict, Depends(current_user)]) -> dict:
    """新增一条地址。

    地址簿为空时，无论前端传没传 isDefault 都强制设成默认——否则用户新增了唯一
    一条地址，去结算页还是「请选择收货地址」，没人能理解。
    """
    try:
        async with pool.connection() as conn:
            # 事务：数量上限、清旧默认、插入三步要么一起成要么一起不成
            async with conn.transaction():
                existing = await (
                    await conn.execute(
                        "SELECT count(*) AS n FROM shop_addresses WHERE user_id = %s",
                        (user["id"],),
                    )
                ).fetchone()
                if existing["n"] >= MAX_ADDRESSES:
                    raise HTTPException(
                        status_code=400, detail=f"最多保存 {MAX_ADDRESSES} 条地址，先删一些吧"
                    )

                is_default = body.is_default or existing["n"] == 0
                if is_default:
                    await _clear_default(conn, user["id"], None)

                row = await (
                    await conn.execute(
                        f"""
                        INSERT INTO shop_addresses
                            (id, user_id, receiver, phone, province, city, district, detail, is_default)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                     RETURNING {_COLUMNS}
                        """,
                        (
                            snowflake.next_id(),
                            user["id"],
                            body.receiver,
                            body.phone,
                            body.province,
                            body.city,
                            body.district,
                            body.detail,
                            is_default,
                        ),
                    )
                ).fetchone()
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("新增地址失败 user_id=%s", user["id"])
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info("新增收货地址 user_id=%s address_id=%s", user["id"], row["id"])
    return {"ok": True, "item": _out(row)}


@router.put("/api/shop/addresses/{address_id}")
async def update_address(
    address_id: int, body: ShopAddressIn, user: Annotated[dict, Depends(current_user)]
) -> dict:
    """整份覆盖一条地址。

    改地址**不影响已下的订单**——那里存的是快照。所以这里不需要任何「有订单在用
    就不让改」的检查。
    """
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                if body.is_default:
                    await _clear_default(conn, user["id"], address_id)

                row = await (
                    await conn.execute(
                        f"""
                        UPDATE shop_addresses
                           SET receiver = %s, phone = %s, province = %s, city = %s,
                               district = %s, detail = %s, is_default = %s
                         WHERE id = %s AND user_id = %s
                     RETURNING {_COLUMNS}
                        """,
                        (
                            body.receiver,
                            body.phone,
                            body.province,
                            body.city,
                            body.district,
                            body.detail,
                            body.is_default,
                            address_id,
                            user["id"],
                        ),
                    )
                ).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="这条地址不存在")
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("修改地址失败 user_id=%s address_id=%s", user["id"], address_id)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    return {"ok": True, "item": _out(row)}


@router.put("/api/shop/addresses/{address_id}/default")
async def set_default_address(
    address_id: int, user: Annotated[dict, Depends(current_user)]
) -> dict:
    """把某条设为默认。单独一条接口而不是让前端调整份 PUT：
    列表页点「设为默认」时前端手上只有 id，没有完整的地址内容。"""
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                await _clear_default(conn, user["id"], address_id)
                row = await (
                    await conn.execute(
                        "UPDATE shop_addresses SET is_default = true "
                        " WHERE id = %s AND user_id = %s RETURNING id",
                        (address_id, user["id"]),
                    )
                ).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="这条地址不存在")
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("设置默认地址失败 user_id=%s address_id=%s", user["id"], address_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    return {"ok": True}


@router.delete("/api/shop/addresses/{address_id}")
async def delete_address(address_id: int, user: Annotated[dict, Depends(current_user)]) -> dict:
    """删掉一条地址。硬删，没有回收站。

    删掉的如果是默认地址，**不自动把另一条顶上来**：默认地址是用户的选择，
    系统替他选一个，下次下单就可能寄到一个他没想寄的地方。结算页会提示去选。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "DELETE FROM shop_addresses WHERE id = %s AND user_id = %s RETURNING id",
                    (address_id, user["id"]),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("删除地址失败 user_id=%s address_id=%s", user["id"], address_id)
        raise HTTPException(status_code=500, detail="删除失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="这条地址不存在")
    logger.info("删除收货地址 user_id=%s address_id=%s", user["id"], address_id)
    return {"ok": True}

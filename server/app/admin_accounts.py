"""管理员账号管理。只有超级管理员能进。

没有注册接口：账号只能由超管在这里创建。这是需求定死的——后台出的是全量客户
资料，不能让任何人自助开号。

超管自己不在 admin_users 里（见 app/admin_auth.py），所以这里的每个接口都不必
判断「会不会误删自己」——超管压根不在这张表的查询结果里。
"""

import logging

import psycopg
from fastapi import APIRouter, Depends, HTTPException, status

from .admin_auth import SUPER_USERNAME, current_super
from .db import pool
from .models import AdminAccountIn, AdminPasswordResetIn, AdminStatusIn
from .security import hash_password
from .snowflake import next_id

logger = logging.getLogger(__name__)

# 依赖挂在 router 上，这个文件里将来加接口也自动只有超管能进
router = APIRouter(
    prefix="/api/admin/accounts",
    tags=["admin-accounts"],
    dependencies=[Depends(current_super)],
)

# password_hash 一个字节都不出接口
ACCOUNT_COLUMNS = "id, username, display_name, status, last_login_at, login_count, created_at"


def to_json(row: dict) -> dict:
    return {
        # 雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，出接口一律转字符串
        "id": str(row["id"]),
        "username": row["username"],
        "displayName": row["display_name"],
        "status": row["status"],
        "lastLoginAt": row["last_login_at"].isoformat() if row["last_login_at"] else None,
        "loginCount": row["login_count"],
        "createdAt": row["created_at"].isoformat(),
    }


@router.get("")
async def list_accounts() -> dict:
    """全部管理员账号。

    不分页：管理员是个位数到几十的量级，一页出得完；加了分页反而让前端多一套
    翻页状态要维护。真到几百个账号那天再说。
    """
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                f"SELECT {ACCOUNT_COLUMNS} FROM admin_users ORDER BY created_at DESC, id DESC"
            )
        ).fetchall()
    return {"ok": True, "items": [to_json(row) for row in rows]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_account(body: AdminAccountIn) -> dict:
    if body.username == SUPER_USERNAME:
        # 同名的普通账号登录时永远被超管判定抢先命中，建出来就是个点不动的鬼影
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "这个用户名被保留，换一个")

    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    INSERT INTO admin_users (id, username, display_name, password_hash)
                    VALUES (%s, %s, %s, %s)
                    RETURNING {ACCOUNT_COLUMNS}
                    """,
                    (next_id(), body.username, body.display_name, hash_password(body.password)),
                )
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "这个用户名已被占用") from None
    except psycopg.Error:
        logger.exception("创建管理员账号失败 username=%s", body.username)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "创建失败，请稍后再试"
        ) from None

    logger.info("新建管理员账号 username=%s", body.username)
    return {"ok": True, "account": to_json(row)}


@router.put("/{account_id}/password")
async def reset_password(account_id: int, body: AdminPasswordResetIn) -> dict:
    """超管重置某人的密码。改完把这个人所有会话清掉，让他必须用新密码重登。"""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "UPDATE admin_users SET password_hash = %s WHERE id = %s RETURNING username",
                (hash_password(body.password), account_id),
            )
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
        await conn.execute("DELETE FROM admin_sessions WHERE admin_id = %s", (account_id,))

    logger.info("重置管理员密码 username=%s", row["username"])
    return {"ok": True}


@router.put("/{account_id}/status")
async def set_status(account_id: int, body: AdminStatusIn) -> dict:
    """启用 / 停用。

    停用时顺手清掉会话，那个人手上没过期的 token 立刻作废——只改状态的话，
    要等鉴权层下一次查到 status 才拦得住，中间那一瞬他还在后台里翻客户手机号。
    """
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                f"UPDATE admin_users SET status = %s WHERE id = %s RETURNING {ACCOUNT_COLUMNS}",
                (body.status, account_id),
            )
        ).fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
        if body.status == "disabled":
            await conn.execute("DELETE FROM admin_sessions WHERE admin_id = %s", (account_id,))

    logger.info("管理员账号状态变更 username=%s status=%s", row["username"], body.status)
    return {"ok": True, "account": to_json(row)}


@router.delete("/{account_id}")
async def delete_account(account_id: int) -> dict:
    """删号。会话随 admin_sessions 的外键级联删除，这里不用管。"""
    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "DELETE FROM admin_users WHERE id = %s RETURNING username", (account_id,)
            )
        ).fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")

    logger.info("删除管理员账号 username=%s", row["username"])
    return {"ok": True}

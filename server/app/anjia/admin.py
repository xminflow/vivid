"""安家立业的管理后台接口。

一个请求同时用到两个连接池：会话在安东尼之家的库（`current_admin` 查那边的
admin_sessions），业务数据在安家立业的库。这是 docs/adr/0001「单进程多库承载
小程序矩阵」的既定形态，不是这次新欠的债——运营是同一批人，不为安家立业另建
一套后台账号。

权限挂的是 `current_admin` 而不是 `current_super`：企业认证是日常、高频、可撤销
的运营动作，与「退款」那种动钱且不可逆的动作不同，卡在超管身上只会让审核积压。
"""

import logging
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..admin_auth import current_admin
from ..paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page, like_pattern
from . import certifications
from .db import get_pool
from .models import CertificationReviewIn

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/anjia",
    tags=["anjia-admin"],
    dependencies=[Depends(current_admin)],
)

CertStatus = Literal["pending", "approved", "rejected", "revoked"]

# 队列里每一行都带上申请人的情况：审核时要判断「这是不是一个刚注册来蹭认证的
# 空号」，让前端再发一次请求去补，等于把这个判断变成两屏之间的来回
QUEUE_COLUMNS = """
    c.id, c.user_id, c.company_name, c.contact_name, c.contact_phone,
    c.status, c.reject_reason, c.reviewed_by, c.reviewed_at, c.created_at,
    u.nickname, u.is_member, u.created_at AS user_created_at
"""
QUEUE_TABLE = "company_certifications c JOIN users u ON u.id = c.user_id"
# JOIN 之后 created_at 两边都有，排序必须写全限定名，否则 Postgres 报歧义
QUEUE_ORDER = "c.created_at DESC, c.id DESC"


def queue_json(row: dict) -> dict:
    return {
        # 雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，出接口一律转字符串
        "id": str(row["id"]),
        "userId": str(row["user_id"]),
        "companyName": row["company_name"],
        "contactName": row["contact_name"],
        # 明文出后台。驳回后打电话是唯一的补救手段，脱敏只会让运营多点一次；
        # 后台本身是超管建号、无自助注册的封闭系统（与预约、服务申请一致）
        "contactPhone": row["contact_phone"],
        "status": row["status"],
        "rejectReason": row["reject_reason"],
        "reviewedBy": row["reviewed_by"],
        "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "createdAt": row["created_at"].isoformat(),
        "nickname": row["nickname"],
        "isMember": row["is_member"],
        "userCreatedAt": row["user_created_at"].isoformat(),
    }


@router.get("/certifications")
async def list_certifications(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")
    ] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=60)] = "",
    status_filter: Annotated[CertStatus | None, Query(alias="status")] = None,
    user_id: Annotated[int | None, Query(alias="userId")] = None,
) -> dict:
    """企业认证申请列表。默认最新的在前——待审的那些本来就是最新提交的。

    `userId` 是给「看看这个人以前交过什么」用的：同一个人改过三次公司名本身就是
    风控信号，而合并成一行就看不见了。
    """
    conditions: list[str] = []
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        # 只搜公司名。公司全称上没有唯一约束（同名公司、分公司真实存在，且我们
        # 不做资质核验），重名要靠运营在这里搜出来人工把关
        conditions.append("c.company_name ILIKE %s ESCAPE '\\'")
        params.append(like_pattern(keyword))
    if status_filter:
        conditions.append("c.status = %s")
        params.append(status_filter)
    if user_id is not None:
        conditions.append("c.user_id = %s")
        params.append(user_id)

    total, rows = await count_and_page(
        QUEUE_TABLE,
        QUEUE_COLUMNS,
        conditions,
        params,
        page,
        page_size,
        order_by=QUEUE_ORDER,
        db=get_pool(),
    )
    return {
        "ok": True,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "items": [queue_json(row) for row in rows],
    }


STATUS_LABELS = {
    certifications.PENDING: "待审核",
    certifications.APPROVED: "已通过",
    certifications.REJECTED: "已驳回",
    certifications.REVOKED: "已撤销",
}


async def _review(
    cert_id: int, expected: str, new_status: str, admin: dict, reason: str = ""
) -> dict:
    """三个审核动作共用的一段：改状态，改不动就说清楚为什么。"""
    try:
        async with get_pool().connection() as conn:
            row = await certifications.set_status(
                conn, cert_id, expected, new_status, admin["username"], reason
            )
            if row is None:
                # 改不动只有两种可能，查一次分清楚，好给出一句准确的话
                existing = await certifications.get(conn, cert_id)
                if existing is None:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到这份认证申请")
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"这份申请当前是「{STATUS_LABELS[existing['status']]}」，不能这样处理",
                )
    except psycopg.Error as exc:
        logger.exception("[anjia] 审核企业认证失败：id=%s -> %s", cert_id, new_status)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "处理失败，请稍后再试"
        ) from exc

    logger.info(
        "[anjia] 企业认证 %s：id=%s 用户=%s 操作人=%s",
        new_status,
        cert_id,
        row["user_id"],
        admin["username"],
    )
    return {"ok": True, "certification": certifications.to_json(row)}


@router.post("/certifications/{cert_id}/approve")
async def approve(cert_id: int, admin: dict = Depends(current_admin)) -> dict:
    """通过。只有待审的能通过。"""
    return await _review(
        cert_id, certifications.PENDING, certifications.APPROVED, admin
    )


@router.post("/certifications/{cert_id}/reject")
async def reject(
    cert_id: int, body: CertificationReviewIn, admin: dict = Depends(current_admin)
) -> dict:
    """驳回。理由必填——申请人得知道该改什么，而不是收到一个沉默的拒绝。"""
    return await _review(
        cert_id,
        certifications.PENDING,
        certifications.REJECTED,
        admin,
        body.reason,
    )


@router.post("/certifications/{cert_id}/revoke")
async def revoke(cert_id: int, admin: dict = Depends(current_admin)) -> dict:
    """撤销已通过的认证。账号即刻退回个人账号，会员状态不受影响（两者正交）。

    撤销不是惩罚：这条记录改成 revoked 之后，那条「一个账号最多一条生效认证」的
    部分唯一索引自动放行，用户可以立刻重新提交。
    """
    return await _review(
        cert_id, certifications.APPROVED, certifications.REVOKED, admin
    )


@router.get("/users")
async def list_users(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")
    ] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=60)] = "",
    member: Annotated[bool | None, Query()] = None,
) -> dict:
    """安家立业的注册用户列表。**只读**。

    没有封禁：这张表连 status 列都没有，而这一期没有任何内容可封，加了等于把整条
    内容治理链提前拽进来。也没有后台开通/取消会员：会员是用户自助行为，多一个后台
    写入口就多一处「这个人的会员是哪来的」说不清的地方。
    """
    conditions: list[str] = []
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        conditions.append("nickname ILIKE %s ESCAPE '\\'")
        params.append(like_pattern(keyword))
    if member is not None:
        conditions.append("is_member = %s")
        params.append(member)

    total, rows = await count_and_page(
        "users",
        """id, nickname, is_member, member_since, member_expires_at,
           created_at, last_login_at, login_count""",
        conditions,
        params,
        page,
        page_size,
        db=get_pool(),
    )

    items = [
        {
            # openid、session_key、token 一个都不出接口，它们是身份与密钥
            "id": str(row["id"]),
            "nickname": row["nickname"],
            "isMember": row["is_member"],
            "memberSince": (
                row["member_since"].isoformat() if row["member_since"] else None
            ),
            "memberExpiresAt": (
                row["member_expires_at"].isoformat()
                if row["member_expires_at"]
                else None
            ),
            "createdAt": row["created_at"].isoformat(),
            "lastLoginAt": (
                row["last_login_at"].isoformat() if row["last_login_at"] else None
            ),
            "loginCount": row["login_count"],
        }
        for row in rows
    ]
    return {
        "ok": True,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "items": items,
    }

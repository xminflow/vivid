"""安家立业的案例审核接口（管理后台侧）。

小程序侧在 app/anjia/cases.py，状态常量与图片出参都从那里取，不在这里重写一份。

与企业认证审核同一个形态：会话在安东尼之家的库（`current_admin` 查那边的
admin_sessions），业务数据在安家立业的库，一个请求同时用两个连接池
（docs/adr/0001）。权限挂 `current_admin` 而不是 `current_super`——内容审核是
日常、高频、可回退的动作，卡在超管身上只会让队列积压。

第一刀只做「通过 / 驳回」。事后下架（`delisted`）的接口在第二刀，但状态和列都
已经在库里了，所以下面各处的筛选一律按四个状态写，不按「就这两个」写死。
"""

import logging
from typing import Annotated, Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..admin_auth import current_admin
from ..paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page, like_pattern
from . import cases
from .db import get_pool
from .models import CaseReviewIn

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/admin/anjia",
    tags=["anjia-admin"],
    dependencies=[Depends(current_admin)],
)

CaseStatus = Literal["pending", "published", "rejected", "delisted"]

# 队列里每一行都带上作者的情况：审核时要判断「这是不是一个刚认证完就来刷屏的
# 空号」，让前端再发一次请求去补，等于把这个判断变成两屏之间的来回
QUEUE_COLUMNS = """
    c.id, c.user_id, c.title, c.body, c.images, c.status, c.views,
    c.reject_reason, c.reject_note, c.reviewed_by, c.reviewed_at,
    c.published_at, c.created_at, c.updated_at,
    u.nickname, u.avatar_url, u.is_member, u.created_at AS user_created_at,
    COALESCE(cc.company_name, '') AS company_name
"""
QUEUE_TABLE = """
    cases c
    JOIN users u ON u.id = c.user_id
    LEFT JOIN company_certifications cc
           ON cc.user_id = c.user_id AND cc.status = 'approved'
"""
# JOIN 之后 created_at 两边都有，排序必须写全限定名，否则 Postgres 报歧义
QUEUE_ORDER = "c.created_at DESC, c.id DESC"


def queue_json(row: dict) -> dict:
    """一条案例出后台接口的样子。

    图片给全九张而不是只给封面：审核就是要把内容看全再判，只给封面的话运营点
    「通过」时其实没看过后面八张。
    """
    return {
        # 雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，出接口一律转字符串
        "id": str(row["id"]),
        "userId": str(row["user_id"]),
        "title": row["title"],
        "body": row["body"],
        "images": [cases.image_json(item) for item in row["images"]],
        "status": row["status"],
        "statusLabel": cases.STATUS_LABELS[row["status"]],
        "views": row["views"],
        "rejectReason": row["reject_reason"],
        "rejectNote": row["reject_note"],
        "reviewedBy": row["reviewed_by"],
        "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        "publishedAt": row["published_at"].isoformat() if row["published_at"] else None,
        "createdAt": row["created_at"].isoformat(),
        # 与 reviewedAt 比对就知道「过审之后又被作者改过没有」
        "updatedAt": row["updated_at"].isoformat(),
        "author": {
            "id": str(row["user_id"]),
            "companyName": row["company_name"],
            "nickname": row["nickname"],
            "avatarUrl": row["avatar_url"],
            "isMember": row["is_member"],
            "createdAt": row["user_created_at"].isoformat(),
        },
    }


async def _counts() -> dict:
    """四个分页各有多少条。

    并进列表响应里，不单开一个接口：运营进这一页必然要拉列表，多一次往返只为
    一个角标数字不值。已软删的不计——它们在后台任何一个分页里都不出现。
    """
    async with get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT status, count(*) AS n FROM cases"
                " WHERE deleted_at IS NULL GROUP BY status"
            )
        ).fetchall()
    # 四个键一个都不能少：前端拿它渲染分页上的数字，缺一个就是一个空白角标
    counts = {name: 0 for name in cases.STATUS_LABELS}
    for row in rows:
        counts[row["status"]] = row["n"]
    return counts


@router.get("/cases")
async def list_cases(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")
    ] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=60)] = "",
    status_filter: Annotated[CaseStatus | None, Query(alias="status")] = None,
    user_id: Annotated[int | None, Query(alias="userId")] = None,
) -> dict:
    """案例列表。默认最新的在前——待审的那些本来就是最新提交的。

    `userId` 是给「这个账号都发过什么」用的：判断一条案例是不是广告，最有力的
    依据往往不是这一条本身，而是同一个账号前面那五条长什么样。
    """
    conditions = ["c.deleted_at IS NULL"]
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        # 标题和公司名一起搜。运营找一条案例时手里有的要么是标题里的词，
        # 要么是「某某设计最近发的那条」，两条路都得通
        conditions.append(
            "(c.title ILIKE %s ESCAPE '\\' OR cc.company_name ILIKE %s ESCAPE '\\')"
        )
        params += [like_pattern(keyword), like_pattern(keyword)]
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
        "counts": await _counts(),
        "items": [queue_json(row) for row in rows],
    }


@router.get("/cases/{case_id}")
async def read_case(case_id: int) -> dict:
    """审核详情。看的是全文与全部图片——列表里塞不下，而没看全就点通过等于没审。

    与小程序侧的详情接口不同，这里**不限状态**：已驳回、已下架的也要能打开，
    否则运营处理完就再也看不到自己处理过的是什么。软删的除外。
    """
    try:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    SELECT {QUEUE_COLUMNS}
                      FROM {QUEUE_TABLE}
                     WHERE c.id = %s AND c.deleted_at IS NULL
                    """,
                    (case_id,),
                )
            ).fetchone()
    except psycopg.Error as exc:
        logger.exception("[anjia] 案例详情查询失败：id=%s", case_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "查询失败，请稍后再试"
        ) from exc

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到这条案例")
    return {"ok": True, "case": queue_json(row)}


async def _review(
    case_id: int,
    expected: str,
    new_status: str,
    admin: dict,
    reason: str = "",
    note: str = "",
) -> dict:
    """审核动作共用的一段：改状态，改不动就说清楚为什么。

    状态写在 WHERE 里而不是先查再改：两个管理员各开一个标签页同时点，先查再改会
    让两条请求都通过检查、后一条把前一条的结果覆盖掉。放进 WHERE，第二条自然改
    不到行，调用方据此回 409 并让前端刷新——这就是那道乐观锁。

    `published_at` 的取值由 new_status 决定，是**代码里的字面量**不是请求参数，
    拼进 SQL 是安全的。用 COALESCE 而不是无条件 now()：作者改完错别字重新过审时
    不该重新抢一次首页头部的位置。
    """
    published_at = (
        "COALESCE(published_at, now())" if new_status == cases.PUBLISHED else "published_at"
    )
    try:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE cases
                       SET status        = %s,
                           reject_reason = %s,
                           reject_note   = %s,
                           reviewed_by   = %s,
                           reviewed_at   = now(),
                           published_at  = {published_at}
                     WHERE id = %s AND status = %s AND deleted_at IS NULL
                    RETURNING id, user_id, status
                    """,
                    (
                        new_status,
                        reason,
                        note,
                        admin["username"],
                        case_id,
                        expected,
                    ),
                )
            ).fetchone()

            if row is None:
                # 改不动有三种可能，查一次分清楚，好给出一句准确的话
                existing = await (
                    await conn.execute(
                        "SELECT status, deleted_at FROM cases WHERE id = %s", (case_id,)
                    )
                ).fetchone()
                if existing is None:
                    raise HTTPException(status.HTTP_404_NOT_FOUND, "找不到这条案例")
                if existing["deleted_at"] is not None:
                    raise HTTPException(status.HTTP_409_CONFLICT, "作者已经删掉了这条案例")
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"这条案例当前是「{cases.STATUS_LABELS[existing['status']]}」，"
                    "可能已被其他人处理，请刷新后再看",
                )
    except psycopg.Error as exc:
        logger.exception("[anjia] 审核案例失败：id=%s -> %s", case_id, new_status)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "处理失败，请稍后再试"
        ) from exc

    logger.info(
        "[anjia] 案例 %s：id=%s 作者=%s 操作人=%s",
        new_status,
        case_id,
        row["user_id"],
        admin["username"],
    )
    return {"ok": True, "id": str(row["id"]), "status": row["status"]}


@router.post("/cases/{case_id}/approve")
async def approve(case_id: int, admin: dict = Depends(current_admin)) -> dict:
    """通过。只有待审的能通过，过审即刻出现在首页。

    驳回理由一并清空：这条案例现在是通过的，留着上一次的驳回理由，作者在「我的
    发布」里就会看到一条「已发布」却挂着一句「图片模糊」。
    """
    return await _review(case_id, cases.PENDING, cases.PUBLISHED, admin)


@router.post("/cases/{case_id}/reject")
async def reject(
    case_id: int, body: CaseReviewIn, admin: dict = Depends(current_admin)
) -> dict:
    """驳回。理由必填——作者看到的就是这句话，没有它他只能靠反复提交去猜。"""
    return await _review(
        case_id, cases.PENDING, cases.REJECTED, admin, body.reason, body.note
    )

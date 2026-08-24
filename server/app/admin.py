"""后台管理接口。

路径统一挂在 `/api/admin` 下，与小程序接口分开，原因有两个：

1. 风险等级不同。小程序那些接口要么只写、要么只出「自己的」数据（`/api/users/me/*`），
   这里出的是**全量**客户姓名和手机号，一条 URL 泄漏就是全部客户资料泄漏。
2. 鉴权好加。这个 router 挂了 `dependencies=[Depends(current_admin)]`，一行覆盖
   全部后台接口，不用逐个接口去改，新加的接口也不会漏掉。

登录与账号管理见 app/admin_auth.py 和 app/admin_accounts.py。
"""

import logging
from datetime import date, timedelta
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from . import cos, snowflake
from .admin_auth import current_admin
from .db import pool
from .home import load_slots
from .models import (
    HOME_SLOT_MAX,
    HomeMediaIn,
    HomeSlot,
    Purpose,
    ServiceId,
    VisitorType,
)
from .paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page, like_pattern

logger = logging.getLogger(__name__)

# 鉴权挂在 router 上而不是逐个接口挂：这样将来在这个文件里加接口，
# 不需要记得加依赖也一样是受保护的。登录接口在 app/admin_auth.py，
# 那是独立的 router，不受这一行影响
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_admin)])


@router.get("/appointments")
async def list_appointments(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=40)] = "",
    visitor_type: Annotated[VisitorType | None, Query(alias="visitorType")] = None,
    purpose: Purpose | None = None,
    visit_date_from: Annotated[date | None, Query(alias="visitDateFrom")] = None,
    visit_date_to: Annotated[date | None, Query(alias="visitDateTo")] = None,
) -> dict:
    """展厅预约申请列表。

    排序按提交时间倒序（最新的要先被跟进），日期区间筛的是**到访日期**——
    运营关心的是「明天有谁要来」，不是「这条记录什么时候填的」。
    """
    conditions: list[str] = []
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        # 姓名和手机号一个框搜完：运营手上要么有名字要么有号码，不该逼他们选字段
        conditions.append("(name ILIKE %s ESCAPE '\\' OR phone LIKE %s ESCAPE '\\')")
        pattern = like_pattern(keyword)
        params += [pattern, pattern]
    if visitor_type:
        conditions.append("visitor_type = %s")
        params.append(visitor_type)
    if purpose:
        conditions.append("purpose = %s")
        params.append(purpose)
    if visit_date_from:
        conditions.append("visit_date >= %s")
        params.append(visit_date_from)
    if visit_date_to:
        conditions.append("visit_date <= %s")
        params.append(visit_date_to)

    total, rows = await count_and_page(
        "appointments",
        """id, name, phone, visitor_type, visit_date, party_size, purpose,
           note, space_id, user_id, created_at""",
        conditions,
        params,
        page,
        page_size,
    )

    items = [
        {
            "id": row["id"],
            "name": row["name"],
            "phone": row["phone"],
            "visitorType": row["visitor_type"],
            "visitDate": row["visit_date"].isoformat(),
            "partySize": row["party_size"],
            "purpose": row["purpose"],
            "note": row["note"],
            "spaceId": row["space_id"],
            # 雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，出接口必须转字符串
            "userId": str(row["user_id"]) if row["user_id"] is not None else None,
            "createdAt": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return {"ok": True, "total": total, "page": page, "pageSize": page_size, "items": items}


def _presign_images(images: dict[str, list[str]] | None) -> dict[str, list[dict]]:
    """把库里存的 COS 对象键换成一小时有效的浏览地址。

    COS 没配时 `url` 给 null 而不是把整组图丢掉：后台看到的是「有 3 张图但服务未配置」，
    而不是「这个客户没传图」——后者会让运营漏掉客户已经提供的信息。
    """
    groups = images or {}
    if groups and not cos.configured():
        logger.warning("COS 未配置，服务申请图片只能出对象键，后台看不到图")

    configured = cos.configured()
    return {
        group: [
            {"key": key, "url": cos.presign_get(key) if configured else None} for key in keys
        ]
        for group, keys in groups.items()
    }


@router.get("/service-applications")
async def list_service_applications(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=40)] = "",
    service_id: Annotated[ServiceId | None, Query(alias="serviceId")] = None,
    created_from: Annotated[date | None, Query(alias="createdFrom")] = None,
    created_to: Annotated[date | None, Query(alias="createdTo")] = None,
) -> dict:
    """服务申请列表。

    五个服务共用一张表，各自的表单字段整体存在 `fields` 里（键是字段 id），
    这里原样出，字段中文名由后台按 `antony-casa/mock/service.js` 的定义翻译。
    """
    conditions: list[str] = []
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        conditions.append("(name ILIKE %s ESCAPE '\\' OR phone LIKE %s ESCAPE '\\')")
        pattern = like_pattern(keyword)
        params += [pattern, pattern]
    if service_id:
        conditions.append("service_id = %s")
        params.append(service_id)
    if created_from:
        conditions.append("created_at >= %s")
        params.append(created_from)
    if created_to:
        # created_at 是时间戳，传进来的是日期。要把当天整天算进去，
        # 所以取「小于次日零点」而不是「小于等于当天零点」——后者会把当天的记录全筛掉
        conditions.append("created_at < %s")
        params.append(created_to + timedelta(days=1))

    total, rows = await count_and_page(
        "service_applications",
        "id, service_id, name, phone, fields, images, created_at",
        conditions,
        params,
        page,
        page_size,
    )

    items = [
        {
            "id": row["id"],
            "serviceId": row["service_id"],
            "name": row["name"],
            "phone": row["phone"],
            "fields": row["fields"] or {},
            "images": _presign_images(row["images"]),
            "createdAt": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return {"ok": True, "total": total, "page": page, "pageSize": page_size, "items": items}


# ---------------------------------------------------------------------------
# 删除线索
#
# 这两条是后台唯一会改动客户提交内容的接口，且**不可撤销**——没有软删除，
# 删了就是从库里没了。这么定是因为运营要的就是「处理完的线索从列表里消失」，
# 软删除会带来一个「已删除」的筛选项和一堆没人看的存量，等于把状态流转
# 换个名字又做了一遍。二次确认放在前端（website/ 的两个列表页）。
#
# 权限与本文件其余接口一致：router 上挂的是 current_admin，普通管理员也能删。
# 跟进线索本来就是运营的日常操作，收紧到超管会让这个功能没人用得上。


@router.delete("/appointments/{appointment_id}")
async def delete_appointment(appointment_id: int) -> dict:
    """删掉一条展厅预约。

    RETURNING 出姓名手机号是为了写日志：删除不可撤销，出了纠纷要能从日志里
    查到「哪条记录、什么时候、被删掉了」。手机号只打后四位，日志文件不该留全量号码。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "DELETE FROM appointments WHERE id = %s RETURNING name, phone",
                    (appointment_id,),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("删除展厅预约失败 id=%s", appointment_id)
        raise HTTPException(status_code=500, detail="删除失败，请稍后再试") from None

    if row is None:
        # 两个人同时开着列表，另一个先删掉了。前端据此提示并刷新，
        # 不能装作删成功——那样列表刷出来还在，看着像没生效
        raise HTTPException(status_code=404, detail="这条预约不存在，可能已被删除")

    logger.info(
        "删除展厅预约 id=%s name=%s phone=****%s", appointment_id, row["name"], row["phone"][-4:]
    )
    return {"ok": True}


@router.delete("/service-applications/{application_id}")
async def delete_service_application(application_id: int) -> dict:
    """删掉一条服务申请。

    客户传的图**不删**：对象键是随机的，留在桶里不会被谁猜到，而删对象是没法
    回退的第二次不可逆操作——万一是误删记录，图还在桶里至少能捞回来。
    清理由运维按 uploads/ 前缀对账，与首页配图的取舍一致（见 README）。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "DELETE FROM service_applications WHERE id = %s RETURNING name, phone, images",
                    (application_id,),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("删除服务申请失败 id=%s", application_id)
        raise HTTPException(status_code=500, detail="删除失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="这条申请不存在，可能已被删除")

    # 图片对象键一并记下来：将来要对账「哪些 uploads/ 对象已经没有记录引用了」，
    # 日志是唯一的线索，记录本身已经没了
    orphan_keys = [key for keys in (row["images"] or {}).values() for key in keys]
    logger.info(
        "删除服务申请 id=%s name=%s phone=****%s 遗留图片=%s",
        application_id,
        row["name"],
        row["phone"][-4:],
        orphan_keys,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# 首页配图
#
# 读的那一侧（小程序的 GET /api/home）在 app/home.py，两边共用 load_slots()。
# 这里只管后台：上传一张图、整组保存、整组读回来带缩略图地址。

# 首页图在桶里的场景目录，落成 static/home-media/YYYYMMDD/<uuid>.<ext>
UPLOAD_SCENE = "home-media"


@router.get("/home-media")
async def list_home_media() -> dict:
    """三个位置的当前配置。带上 url 是为了后台能直接把图显示出来。"""
    try:
        slots = await load_slots()
    except psycopg.Error:
        logger.exception("后台查询首页配图失败")
        raise HTTPException(status_code=500, detail="查询失败，请稍后再试") from None

    configured = cos.configured()
    if not configured:
        # 同 _presign_images 的取舍：宁可让后台看到「有图但显示不出来」，
        # 也不能让它看起来像「没配过图」——后者会让运营以为要重新传一遍
        logger.warning("COS 未配置，后台首页图只能出对象键，看不到图")

    return {
        "ok": True,
        "slots": {
            slot: [
                {
                    # 雪花 ID 超出 JS 的安全整数范围，出接口一律转字符串
                    "id": str(row["id"]),
                    "key": row["image_key"],
                    "url": cos.object_url(row["image_key"]) if configured else None,
                }
                for row in rows
            ]
            for slot, rows in slots.items()
        },
        "limits": HOME_SLOT_MAX,
    }


@router.put("/home-media/{slot}")
async def replace_home_media(slot: HomeSlot, body: HomeMediaIn) -> dict:
    """整组替换某个位置的图，数组顺序就是首页上的展示顺序。

    删旧插新放在一个事务里：中途失败不会留下「删了一半」的首页。
    旧图对应的 COS 对象**不删**——删了就没法回退到上一版配置，且对象键是随机的，
    留在桶里也不会被谁猜到；清理由运维按 static/home-media/ 前缀对账（见 README）。
    """
    limit = HOME_SLOT_MAX[slot]
    if len(body.keys) > limit:
        raise HTTPException(status_code=400, detail=f"这个位置最多放 {limit} 张图")

    rows = [(snowflake.next_id(), slot, key, index) for index, key in enumerate(body.keys)]

    try:
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM home_media WHERE slot = %s", (slot,))
            if rows:
                await conn.cursor().executemany(
                    """
                    INSERT INTO home_media (id, slot, image_key, sort_order)
                    VALUES (%s, %s, %s, %s)
                    """,
                    rows,
                )
    except psycopg.Error:
        logger.exception("保存首页配图失败 slot=%s 张数=%d", slot, len(body.keys))
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info("首页配图已更新 slot=%s 张数=%d keys=%s", slot, len(body.keys), body.keys)
    return {"ok": True, "slot": slot, "count": len(body.keys)}


async def _read_body_within_limit(request: Request) -> bytes:
    """边读边数，超限立刻停。

    不用 `await request.body()` 一次读完再判断：那样上限是「先把整个请求收进内存，
    再告诉对方太大了」，等于把内存交给调用方决定。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cos.MAX_UPLOAD_BYTES:
            # 413 Payload Too Large。这个文件里的状态码一律写字面量，
            # 不引 fastapi.status——半数字半常量比统一用哪一种都更难读
            raise HTTPException(
                status_code=413,
                detail=f"图片不能超过 {cos.MAX_UPLOAD_BYTES // 1024 // 1024}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/home-media/upload")
async def upload_home_media(request: Request) -> dict:
    """收一张图，落到 COS，返回对象键。

    请求体就是**裸的图片字节**，不是 multipart：解析 multipart 要装 python-multipart，
    而后台一次只传一张图，裸 body 已经够用，不值得为此多一个依赖。

    只返回键和地址，不写库——写库是 PUT /home-media/{slot} 的事。这样运营在后台
    先传几张、调完顺序再点保存，中途关掉页面不会把首页改成一半的样子。
    """
    data = await _read_body_within_limit(request)
    if not data:
        raise HTTPException(status_code=400, detail="没有收到图片内容")

    # 按文件头判类型，不信前端给的文件名和 Content-Type
    ext = cos.sniff_image_ext(data)
    if ext is None:
        raise HTTPException(status_code=400, detail="只支持 JPG / PNG / WebP 格式的图片")

    key = cos.build_static_key(UPLOAD_SCENE, ext)
    try:
        await cos.put_object(key, data, cos.CONTENT_TYPES[ext])
    except cos.CosNotConfigured:
        logger.exception("COS 未配置，首页图传不上去")
        raise HTTPException(status_code=503, detail="图片服务未配置，请联系技术") from None
    except cos.CosUploadFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    logger.info("首页图已上传 key=%s 字节数=%d", key, len(data))
    return {"ok": True, "key": key, "url": cos.object_url(key)}

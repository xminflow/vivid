"""安玺·集 后台接口 —— 分类与商品的维护。

与 app/admin.py 分开一个文件而不是塞进去，是因为那边出的是**客户资料**（姓名手机号），
这边出的是**商品目录**，两者的风险等级和读者都不同；将来要给「只管商品的运营」
单独收权限，也只需要换这一个 router 上的依赖。

小程序侧的只读接口在 app/shop.py。设计见
docs/superpowers/specs/2026-08-10-anxi-ji-design.md。

本文件只覆盖阶段一（商品目录）。购物车、地址、订单属于阶段二，届时另起文件。
"""

import logging
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg.types.json import Jsonb

from . import cos, snowflake
from .admin_auth import current_admin
from .db import pool
from .models import (
    ProductStatus,
    ShopCategoryIn,
    ShopCategoryStatusIn,
    ShopProductIn,
    ShopProductStatusIn,
)
from .paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page, like_pattern

logger = logging.getLogger(__name__)

# 鉴权挂在 router 上，理由同 app/admin.py：新加接口不会漏
router = APIRouter(
    prefix="/api/admin/shop", tags=["shop-admin"], dependencies=[Depends(current_admin)]
)

# COS 对象键的分目录名。商品图是**运营素材**，和首页配图一样走 static/ 前缀 + 公开直链，
# 不走 uploads/ 那套预签名——商品图本来就要给所有人看，签了名反而多一层没意义的过期
UPLOAD_SCENE = "shop"


def _image_list(keys: list[str] | None) -> list[dict]:
    """把库里存的对象键换成后台能直接显示的地址。

    COS 没配时 url 给 null 而不是把图丢掉：后台看到「有 3 张图但显示不出来」，
    而不是「这个商品没传图」——后者会让运营重新传一遍。同 app/admin.py 的 _presign_images。
    """
    items = keys or []
    if items and not cos.configured():
        logger.warning("COS 未配置，后台商品图只能出对象键，看不到图")

    configured = cos.configured()
    return [{"key": key, "url": cos.object_url(key) if configured else None} for key in items]


# ---------------------------------------------------------------------------
# 分类
#
# 只有停用、没有删除。分类是低频、少量、长期存在的东西，而「删除」在这里必然要回答
# 「它下面的商品归谁」——商品的分类是必填的。停用则语义干净：分类不再出现在小程序和
# 新建商品的下拉里，其下商品一并下架。库上的外键（NO ACTION）是第二道防线。


@router.get("/categories")
async def list_categories() -> dict:
    """全部分类，含每个分类下的商品数。

    不分页：分类是给运营手工维护的，几十个顶天了。带上商品数是因为停用分类会连带
    下架其下商品，运营点之前得知道要影响多少件。
    """
    try:
        async with pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT c.id, c.name, c.sort_order, c.status, c.created_at,
                           count(p.id)                                  AS product_count,
                           count(p.id) FILTER (WHERE p.status = 'active') AS active_count
                      FROM shop_categories c
                      LEFT JOIN shop_products p ON p.category_id = c.id
                     GROUP BY c.id
                     ORDER BY c.sort_order DESC, c.id
                    """
                )
            ).fetchall()
    except psycopg.Error:
        logger.exception("查询商品分类失败")
        raise HTTPException(status_code=500, detail="查询失败，请稍后再试") from None

    return {
        "ok": True,
        "items": [
            {
                # 雪花 ID 超出 JS 安全整数范围，出接口一律转字符串
                "id": str(row["id"]),
                "name": row["name"],
                "sortOrder": row["sort_order"],
                "status": row["status"],
                "productCount": row["product_count"],
                "activeCount": row["active_count"],
                "createdAt": row["created_at"].isoformat(),
            }
            for row in rows
        ],
    }


@router.post("/categories")
async def create_category(body: ShopCategoryIn) -> dict:
    """建一个分类。名称唯一，重名回 409 而不是静默建出两个「灯具」。"""
    category_id = snowflake.next_id()
    try:
        async with pool.connection() as conn:
            await conn.execute(
                "INSERT INTO shop_categories (id, name, sort_order) VALUES (%s, %s, %s)",
                (category_id, body.name, body.sort_order),
            )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="已经有同名的分类了") from None
    except psycopg.Error:
        logger.exception("新建商品分类失败 name=%s", body.name)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info("新建商品分类 id=%s name=%s", category_id, body.name)
    return {"ok": True, "id": str(category_id)}


@router.put("/categories/{category_id}")
async def update_category(category_id: int, body: ShopCategoryIn) -> dict:
    """改分类名和排序值。改状态走 PUT /categories/{id}/status，两件事不混在一个接口里。"""
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE shop_categories SET name = %s, sort_order = %s
                     WHERE id = %s
                 RETURNING id
                    """,
                    (body.name, body.sort_order, category_id),
                )
            ).fetchone()
    except psycopg.errors.UniqueViolation:
        raise HTTPException(status_code=409, detail="已经有同名的分类了") from None
    except psycopg.Error:
        logger.exception("修改商品分类失败 id=%s", category_id)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="这个分类不存在")

    logger.info("修改商品分类 id=%s name=%s", category_id, body.name)
    return {"ok": True}


@router.put("/categories/{category_id}/status")
async def set_category_status(category_id: int, body: ShopCategoryStatusIn) -> dict:
    """停用 / 启用分类。

    **停用会把该分类下所有在售商品一并改写成下架**，而且启用回来**不会**自动恢复。
    这么做是为了让 shop_products.status 保持「商品能否被购买」的唯一判据：若分类状态
    能隐式决定可购性，后台会显示「在售」而用户点不进去，那种鬼状态没人查得出来。
    恢复走 POST /categories/{id}/activate-products，是一个显式动作。

    改分类和改商品在同一个事务里：中途失败不会留下「分类停了但商品还在卖」的状态。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "UPDATE shop_categories SET status = %s WHERE id = %s RETURNING name",
                    (body.status, category_id),
                )
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="这个分类不存在")

            taken_down = 0
            if body.status == "disabled":
                affected = await conn.execute(
                    """
                    UPDATE shop_products SET status = 'off'
                     WHERE category_id = %s AND status = 'active'
                    """,
                    (category_id,),
                )
                taken_down = affected.rowcount
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("切换分类状态失败 id=%s status=%s", category_id, body.status)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info(
        "切换分类状态 id=%s name=%s status=%s 连带下架=%d",
        category_id,
        row["name"],
        body.status,
        taken_down,
    )
    return {"ok": True, "takenDown": taken_down}


@router.post("/categories/{category_id}/activate-products")
async def activate_category_products(category_id: int) -> dict:
    """把该分类下已下架的商品批量上架。

    分类停用是不可逆的批量下架，这个接口是它的**显式**反向操作。

    没有封面图的商品**跳过不上架**：上架规则要求至少一张图（见 set_product_status），
    这里若一并放行，就等于开了一个绕过校验的后门。跳过几件会在返回值里说明，
    让运营知道还有活儿没干完，而不是以为全上架了。
    """
    try:
        async with pool.connection() as conn:
            category = await (
                await conn.execute(
                    "SELECT name, status FROM shop_categories WHERE id = %s", (category_id,)
                )
            ).fetchone()
            if category is None:
                raise HTTPException(status_code=404, detail="这个分类不存在")
            if category["status"] != "active":
                raise HTTPException(status_code=400, detail="分类还停用着，请先启用分类")

            activated = await conn.execute(
                """
                UPDATE shop_products SET status = 'active'
                 WHERE category_id = %s AND status = 'off' AND jsonb_array_length(images) > 0
                """,
                (category_id,),
            )
            skipped = await (
                await conn.execute(
                    """
                    SELECT count(*) AS n FROM shop_products
                     WHERE category_id = %s AND status = 'off'
                    """,
                    (category_id,),
                )
            ).fetchone()
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("批量上架失败 category_id=%s", category_id)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info(
        "批量上架 category_id=%s name=%s 上架=%d 跳过=%d",
        category_id,
        category["name"],
        activated.rowcount,
        skipped["n"],
    )
    return {"ok": True, "activated": activated.rowcount, "skippedWithoutImage": skipped["n"]}


# ---------------------------------------------------------------------------
# 商品


async def _load_category(conn: Any, category_id: int) -> dict:
    """取分类并确认它可用。新建和编辑商品都要走一遍，所以抽出来。"""
    row = await (
        await conn.execute(
            "SELECT id, name, status FROM shop_categories WHERE id = %s", (category_id,)
        )
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="选的分类不存在")
    if row["status"] != "active":
        # 允许往停用分类里放商品的话，这些商品会永远处在「上架不了」的状态，
        # 而运营在商品页上看不出原因
        raise HTTPException(status_code=400, detail="这个分类已停用，请先启用分类")
    return row


@router.get("/products")
async def list_products(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=60)] = "",
    category_id: Annotated[str, Query(max_length=19, alias="categoryId")] = "",
    status: Annotated[ProductStatus | None, Query()] = None,
) -> dict:
    """商品列表。

    排序与小程序端一致：sort_order 大的在前，同序号按新旧。后台看到的顺序就是
    用户看到的顺序，运营调完排序值能直接在这一页确认效果。
    """
    conditions: list[str] = []
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        # 只搜标题。简介和参数不进搜索：那两处是长文本，搜出来运营也看不出命中在哪
        conditions.append("p.title ILIKE %s ESCAPE '\\'")
        params.append(like_pattern(keyword))
    if category_id:
        if not category_id.isdigit():
            raise HTTPException(status_code=400, detail="分类标识不合法")
        conditions.append("p.category_id = %s")
        params.append(int(category_id))
    if status:
        conditions.append("p.status = %s")
        params.append(status)

    total, rows = await count_and_page(
        "shop_products p JOIN shop_categories c ON c.id = p.category_id",
        """p.id, p.category_id, c.name AS category_name, p.title, p.price_cents,
           p.images, p.status, p.sort_order, p.created_at""",
        conditions,
        params,
        page,
        page_size,
        order_by="p.sort_order DESC, p.created_at DESC, p.id DESC",
    )

    items = [
        {
            "id": str(row["id"]),
            "categoryId": str(row["category_id"]),
            "categoryName": row["category_name"],
            "title": row["title"],
            "priceCents": row["price_cents"],
            # 列表只要封面，整组图在详情接口里出
            "cover": (_image_list(row["images"]) or [None])[0],
            "status": row["status"],
            "sortOrder": row["sort_order"],
            "createdAt": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return {"ok": True, "total": total, "page": page, "pageSize": page_size, "items": items}


@router.get("/products/{product_id}")
async def get_product(product_id: int) -> dict:
    """商品详情，编辑页用。这里出的是全量字段，含简介、详情图和参数。"""
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT p.id, p.category_id, c.name AS category_name, p.title, p.summary,
                           p.price_cents, p.images, p.detail_images, p.params, p.status,
                           p.sort_order, p.created_at, p.updated_at
                      FROM shop_products p
                      JOIN shop_categories c ON c.id = p.category_id
                     WHERE p.id = %s
                    """,
                    (product_id,),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("查询商品详情失败 id=%s", product_id)
        raise HTTPException(status_code=500, detail="查询失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="这个商品不存在")

    return {
        "ok": True,
        "item": {
            "id": str(row["id"]),
            "categoryId": str(row["category_id"]),
            "categoryName": row["category_name"],
            "title": row["title"],
            "summary": row["summary"],
            "priceCents": row["price_cents"],
            "images": _image_list(row["images"]),
            "detailImages": _image_list(row["detail_images"]),
            "params": row["params"] or [],
            "status": row["status"],
            "sortOrder": row["sort_order"],
            "createdAt": row["created_at"].isoformat(),
            "updatedAt": row["updated_at"].isoformat(),
        },
    }


@router.post("/products")
async def create_product(body: ShopProductIn) -> dict:
    """建一个商品。

    新建出来一律是**下架**态，没有草稿态——「下架」本来就是「不可购买」，
    再加一个草稿只是多一个语义重叠的状态。图集可以为空，运营常常先把文字存下来
    再回头传图；等到点上架时才要求至少有一张封面。
    """
    product_id = snowflake.next_id()

    try:
        async with pool.connection() as conn:
            await _load_category(conn, int(body.category_id))
            await conn.execute(
                """
                INSERT INTO shop_products
                       (id, category_id, title, summary, price_cents,
                        images, detail_images, params, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    product_id,
                    int(body.category_id),
                    body.title,
                    body.summary,
                    body.price_cents,
                    Jsonb(body.images),
                    Jsonb(body.detail_images),
                    Jsonb([p.model_dump() for p in body.params]),
                    body.sort_order,
                ),
            )
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("新建商品失败 title=%s", body.title)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info("新建商品 id=%s title=%s 分为=%d", product_id, body.title, body.price_cents)
    return {"ok": True, "id": str(product_id)}


@router.put("/products/{product_id}")
async def update_product(product_id: int, body: ShopProductIn) -> dict:
    """整份覆盖一个商品。不做字段级 PATCH——编辑页本来就是整份提交。

    正在售的商品不允许把图删光：那样它会在小程序列表里变成一块空白，
    而后台还标着「在售」。要清空图就先下架。
    """

    try:
        async with pool.connection() as conn:
            current = await (
                await conn.execute("SELECT status FROM shop_products WHERE id = %s", (product_id,))
            ).fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="这个商品不存在")
            if current["status"] == "active" and not body.images:
                raise HTTPException(status_code=400, detail="在售商品至少要有一张图，请先下架")

            await _load_category(conn, int(body.category_id))
            await conn.execute(
                """
                UPDATE shop_products
                   SET category_id = %s, title = %s, summary = %s, price_cents = %s,
                       images = %s, detail_images = %s, params = %s, sort_order = %s
                 WHERE id = %s
                """,
                (
                    int(body.category_id),
                    body.title,
                    body.summary,
                    body.price_cents,
                    Jsonb(body.images),
                    Jsonb(body.detail_images),
                    Jsonb([p.model_dump() for p in body.params]),
                    body.sort_order,
                    product_id,
                ),
            )
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("修改商品失败 id=%s", product_id)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info("修改商品 id=%s title=%s 分为=%d", product_id, body.title, body.price_cents)
    return {"ok": True}


@router.put("/products/{product_id}/status")
async def set_product_status(product_id: int, body: ShopProductStatusIn) -> dict:
    """上架 / 下架。这是「商品能否被购买」的唯一开关。

    上架有两个前置条件，都在这里卡死：
      1. 至少一张图 —— 第一张兼作列表页封面，没有封面的商品在列表里就是一块空白
      2. 所属分类是启用的 —— 否则商品会出现在一个用户根本点不进去的分类下
    下架无条件放行：出了问题要能立刻停售，任何校验都不该挡着这一步。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT p.title, p.images, c.status AS category_status
                      FROM shop_products p
                      JOIN shop_categories c ON c.id = p.category_id
                     WHERE p.id = %s
                    """,
                    (product_id,),
                )
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="这个商品不存在")

            if body.status == "active":
                if not row["images"]:
                    raise HTTPException(status_code=400, detail="上架前至少要传一张商品图")
                if row["category_status"] != "active":
                    raise HTTPException(status_code=400, detail="所属分类已停用，请先启用分类")

            await conn.execute(
                "UPDATE shop_products SET status = %s WHERE id = %s", (body.status, product_id)
            )
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("切换商品状态失败 id=%s status=%s", product_id, body.status)
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from None

    logger.info("切换商品状态 id=%s title=%s status=%s", product_id, row["title"], body.status)
    return {"ok": True}


@router.delete("/products/{product_id}")
async def delete_product(product_id: int) -> dict:
    """删掉一个商品。硬删，没有软删除。

    **被订单引用过的商品删不掉**：阶段二起 shop_order_items.product_id 会以
    ON DELETE RESTRICT 引用这张表，删除会被数据库挡下来，这里翻译成 409。
    交给外键而不是先查一遍再删，是因为「查完到删掉」之间可能刚好有人下单。

    阶段一还没有订单表，这条路径暂时走不到，但代码先写着——等 009 建完表，
    这里不需要再改一次。

    图片对象**不删**，只记一条日志，与首页配图一致（见 app/admin.py 的说明）：
    对象键是随机的，留在桶里不会被谁猜到，而删了就没法回退。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    DELETE FROM shop_products WHERE id = %s
                 RETURNING title, images, detail_images
                    """,
                    (product_id,),
                )
            ).fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(
            status_code=409, detail="这个商品已经有订单了，删不掉，只能下架"
        ) from None
    except psycopg.Error:
        logger.exception("删除商品失败 id=%s", product_id)
        raise HTTPException(status_code=500, detail="删除失败，请稍后再试") from None

    if row is None:
        # 两个人同时开着列表，另一个先删掉了。装作成功会让列表刷出来还在，看着像没生效
        raise HTTPException(status_code=404, detail="这个商品不存在，可能已被删除")

    orphans = [*(row["images"] or []), *(row["detail_images"] or [])]
    logger.info("删除商品 id=%s title=%s 遗留对象=%s", product_id, row["title"], orphans)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 图片上传


async def _read_body_within_limit(request: Request) -> bytes:
    """边读边数，超限立刻停。同 app/admin.py 的同名函数。

    不用 `await request.body()` 一次读完再判断：那样上限是「先把整个请求收进内存，
    再告诉对方太大了」，等于把内存交给调用方决定。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > cos.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"图片不能超过 {cos.MAX_UPLOAD_BYTES // 1024 // 1024}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/products/upload")
async def upload_product_image(request: Request) -> dict:
    """收一张商品图，落到 COS，返回对象键。不写库——写库是保存商品时的事。

    请求体是**裸的图片字节**，不是 multipart：解析 multipart 要装 python-multipart，
    后台一次只传一张图，不值得为此多一个依赖。同 POST /api/admin/home-media/upload。
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
        logger.exception("COS 未配置，商品图传不上去")
        raise HTTPException(status_code=503, detail="图片服务未配置，请联系技术") from None
    except cos.CosUploadFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from None

    logger.info("商品图已上传 key=%s 字节数=%d", key, len(data))
    return {"ok": True, "key": key, "url": cos.object_url(key)}

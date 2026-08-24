"""安玺·集 小程序接口 —— 商品浏览与购物车。

后台维护接口在 app/shop_admin.py。设计见
docs/superpowers/specs/2026-08-10-anxi-ji-design.md。

鉴权**不挂在 router 上**，逐个接口分：浏览类（分类、商品列表、商品详情）不要求登录，
购物车类逐条挂 current_user。这个分界是有意的——antony-casa 启动时就静默登录了，
正常情况下用户感觉不到差别；真出现登录失败，至少商品还看得见，而不是整个板块打不开。
下单与支付属于后续阶段，同样会要求登录。

只出 status = 'active' 的数据。「在售」是商品能否被购买的**唯一**判据，所以也用它
当「能否被看到」的判据——不存在「能看但买不了」的中间态，那种商品只会让用户白跑一趟。
分类同理，只出启用中的。

图片地址是 COS 公开直链，不现签：商品图本来就要给所有人看，签名没有保护作用，
还会让地址每次都变、微信的图片缓存全部落空（同 app/home.py 的取舍）。
"""

import logging
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from . import cos, snowflake, wxnotice
from .db import pool
from .models import MAX_CART_ITEMS, MAX_CART_QUANTITY, CartItemIn, CartQuantityIn
from .paging import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, count_and_page, like_pattern
from .users import current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shop"])


def _urls(keys: list[str] | None) -> list[str]:
    """对象键换成公开直链。

    COS 没配时返回空列表而不是一串拼半截的地址：小程序拿到空列表会渲染占位图，
    拿到坏地址则是一片裂图，后者看不出是配置问题。原因同 app/home.py。
    """
    if not keys:
        return []
    if not cos.configured():
        logger.error("COS 未配置，安玺·集商品图返回空列表，小程序将显示占位图")
        return []
    return [cos.object_url(key) for key in keys]


@router.get("/api/shop/categories")
async def list_categories() -> dict:
    """启用中的分类，按排序值倒序。小程序顶部的分类横滑用。"""
    try:
        async with pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT id, name FROM shop_categories
                     WHERE status = 'active'
                     ORDER BY sort_order DESC, id
                    """
                )
            ).fetchall()
    except psycopg.Error:
        logger.exception("查询安玺·集分类失败")
        raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

    # 雪花 ID 超出 JS 安全整数范围，出接口一律转字符串
    return {"ok": True, "items": [{"id": str(row["id"]), "name": row["name"]} for row in rows]}


@router.get("/api/shop/products")
async def list_products(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, alias="pageSize")] = DEFAULT_PAGE_SIZE,
    keyword: Annotated[str, Query(max_length=60)] = "",
    category_id: Annotated[str, Query(max_length=19, alias="categoryId")] = "",
) -> dict:
    """在售商品列表，支持按分类筛选和按标题搜索。

    只出封面（图集第一张）：列表页一屏十几件，把每件的整组图都发过去纯属浪费流量。
    """
    # 分类停用时其下商品已被一并改写成下架，所以这里只判商品状态就够，
    # 不需要再 JOIN 一次分类去过滤。这正是「不做级联可见性」换来的简单
    conditions: list[str] = ["status = 'active'"]
    params: list[Any] = []

    keyword = keyword.strip()
    if keyword:
        conditions.append("title ILIKE %s ESCAPE '\\'")
        params.append(like_pattern(keyword))
    if category_id:
        if not category_id.isdigit():
            raise HTTPException(status_code=400, detail="分类标识不合法")
        conditions.append("category_id = %s")
        params.append(int(category_id))

    total, rows = await count_and_page(
        "shop_products",
        "id, category_id, title, summary, price_cents, images",
        conditions,
        params,
        page,
        page_size,
        order_by="sort_order DESC, created_at DESC, id DESC",
    )

    items = [
        {
            "id": str(row["id"]),
            "categoryId": str(row["category_id"]),
            "title": row["title"],
            # 列表行在标题下露一行简介。详情图和参数不带，那些只有详情页才用得上
            "summary": row["summary"],
            # 金额一律用分传，前端自己换算成元。浮点算钱迟早出对账差
            "priceCents": row["price_cents"],
            "cover": (_urls(row["images"]) or [None])[0],
        }
        for row in rows
    ]
    return {"ok": True, "total": total, "page": page, "pageSize": page_size, "items": items}


@router.get("/api/shop/settings")
async def get_settings() -> dict:
    """板块设置。小程序启动或进结算页时读一次。

    出两样东西：

    * **客服二维码**。运营在后台配，用户长按加微信。没配时出 null，
      小程序据此隐藏「联系客服」入口——而不是显示一个点了没反应的按钮。
    * **发货通知的订阅消息模板 ID**。小程序要用它调 wx.requestSubscribeMessage
      去要授权。从服务端出而不是写死在小程序里：换模板时只改服务端配置，
      不用重新发版，也不会出现两边模板 ID 对不上（那种错的表现是「用户授权了
      但收不到通知」，极难查）。模板 ID 不是密钥，出接口没有风险。

    不要求登录：这两样在用户登录之前就要用到（首屏就可能显示客服入口）。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute("SELECT service_qr_key FROM shop_settings WHERE id = 1")
            ).fetchone()
    except psycopg.Error:
        logger.exception("查询安玺·集设置失败")
        raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

    key = (row or {}).get("service_qr_key") or ""
    return {
        "ok": True,
        "serviceQrUrl": cos.object_url(key) if key and cos.configured() else None,
        "shipNoticeTemplateId": wxnotice.SHIP_TEMPLATE_ID or "",
    }


@router.get("/api/shop/products/{product_id}")
async def get_product(product_id: int) -> dict:
    """商品详情。

    下架商品一律 404，不区分「从来没有过」和「刚下架」：用户可能是从收藏、分享链接
    或者停在后台的旧列表点进来的，对他来说这两种情况没有区别，都是「这件买不了了」。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT id, category_id, title, summary, price_cents,
                           images, detail_images, params
                      FROM shop_products
                     WHERE id = %s AND status = 'active'
                    """,
                    (product_id,),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("查询安玺·集商品详情失败 id=%s", product_id)
        raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="这件商品已经下架了")

    return {
        "ok": True,
        "item": {
            "id": str(row["id"]),
            "categoryId": str(row["category_id"]),
            "title": row["title"],
            "summary": row["summary"],
            "priceCents": row["price_cents"],
            "images": _urls(row["images"]),
            "detailImages": _urls(row["detail_images"]),
            # [{"name": "材质", "value": "实木"}]，顺序即展示顺序
            "params": row["params"] or [],
        },
    }


# ---------------------------------------------------------------------------
# 购物车
#
# 这几条**要登录**（挂 current_user），因为车是按人存的。浏览不要求登录、加购要求，
# 这个分界是有意的：小程序启动就静默登录了，正常情况下用户感觉不到；真出现登录失败，
# 至少商品还看得见，而不是整个板块打不开。
#
# 购物车只存「谁、要哪件、要几个」，**不存价格**：每次读都实时回查商品的当前价格
# 与在售状态，价格以结算那一刻为准。要固化价格的是订单，那是另一张表的事。


@router.get("/api/shop/cart")
async def get_cart(user: Annotated[dict, Depends(current_user)]) -> dict:
    """我的购物车。

    下架的商品**不删也不藏**，照样列出来但 available=false，由小程序置灰、不可勾选。
    悄悄移除会让用户以为自己没加过；藏起来则会让「车里明明有 3 件、结算只有 2 件」
    这种事没法解释。想清掉走 DELETE /api/shop/cart/invalid。
    """
    try:
        async with pool.connection() as conn:
            rows = await (
                await conn.execute(
                    """
                    SELECT c.id, c.quantity, c.product_id,
                           p.title, p.price_cents, p.images, p.status
                      FROM shop_cart_items c
                      JOIN shop_products p ON p.id = c.product_id
                     WHERE c.user_id = %s
                     ORDER BY c.created_at DESC, c.id DESC
                    """,
                    (user["id"],),
                )
            ).fetchall()
    except psycopg.Error:
        logger.exception("查询购物车失败 user_id=%s", user["id"])
        raise HTTPException(status_code=500, detail="加载失败，请稍后再试") from None

    items = [
        {
            "id": str(row["id"]),
            "productId": str(row["product_id"]),
            "title": row["title"],
            "priceCents": row["price_cents"],
            "cover": (_urls(row["images"]) or [None])[0],
            "quantity": row["quantity"],
            # 唯一的可购判据仍然是商品的 status，购物车不另立一套
            "available": row["status"] == "active",
        }
        for row in rows
    ]
    # 角标要显示的是**件数**不是行数：车里两件沙发，角标应该是 2
    quantity = sum(item["quantity"] for item in items if item["available"])
    return {"ok": True, "items": items, "count": len(items), "quantity": quantity}



@router.post("/api/shop/cart")
async def add_to_cart(body: CartItemIn, user: Annotated[dict, Depends(current_user)]) -> dict:
    """加购。

    同一件商品重复加是**累加数量**，靠 ON CONFLICT 一条语句完成——「先查再插」
    在两次加购几乎同时到达时会各自认为「还没有」，插出两行。

    累加到上限时**封顶而不是报错**：用户连点几下加购，得到「最多 99 件」的弹窗
    比得到一个失败更难理解。封顶后返回真实数量，小程序照着显示即可。
    """
    product_id = int(body.product_id)
    try:
        async with pool.connection() as conn:
            product = await (
                await conn.execute(
                    "SELECT title, status FROM shop_products WHERE id = %s", (product_id,)
                )
            ).fetchone()
            if product is None or product["status"] != "active":
                # 不区分「没有过」和「刚下架」：对用户来说都是这件买不了了
                raise HTTPException(status_code=400, detail="这件商品已经下架了")

            existing = await (
                await conn.execute(
                    "SELECT count(*) AS n FROM shop_cart_items WHERE user_id = %s",
                    (user["id"],),
                )
            ).fetchone()
            in_cart = await (
                await conn.execute(
                    "SELECT 1 FROM shop_cart_items WHERE user_id = %s AND product_id = %s",
                    (user["id"], product_id),
                )
            ).fetchone()
            # 只有「新增一行」才受行数上限约束；给车里已有的商品加数量不该被挡
            if in_cart is None and existing["n"] >= MAX_CART_ITEMS:
                raise HTTPException(
                    status_code=400, detail=f"购物车最多放 {MAX_CART_ITEMS} 种商品，先清一些吧"
                )

            row = await (
                await conn.execute(
                    """
                    INSERT INTO shop_cart_items (id, user_id, product_id, quantity)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (user_id, product_id) DO UPDATE
                       SET quantity = LEAST(shop_cart_items.quantity + EXCLUDED.quantity, %s)
                 RETURNING id, quantity
                    """,
                    (
                        snowflake.next_id(),
                        user["id"],
                        product_id,
                        body.quantity,
                        MAX_CART_QUANTITY,
                    ),
                )
            ).fetchone()
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("加购失败 user_id=%s product_id=%s", user["id"], product_id)
        raise HTTPException(status_code=500, detail="加入购物车失败，请稍后再试") from None

    logger.info(
        "加购 user_id=%s product_id=%s title=%s 车内数量=%d",
        user["id"],
        product_id,
        product["title"],
        row["quantity"],
    )
    return {"ok": True, "id": str(row["id"]), "quantity": row["quantity"]}


# ⚠️ 这一条必须声明在 /api/shop/cart/{item_id} **之前**：FastAPI 按声明顺序匹配，
# 反过来的话 'invalid' 会先落到 {item_id} 上，再因为转不成 int 而变成 422
@router.delete("/api/shop/cart/invalid")
async def clear_invalid_cart_items(user: Annotated[dict, Depends(current_user)]) -> dict:
    """一键清掉车里已下架的商品。用户主动点才执行，系统不自作主张。"""
    try:
        async with pool.connection() as conn:
            removed = await conn.execute(
                """
                DELETE FROM shop_cart_items c
                 USING shop_products p
                 WHERE p.id = c.product_id
                   AND c.user_id = %s
                   AND p.status <> 'active'
                """,
                (user["id"],),
            )
    except psycopg.Error:
        logger.exception("清理失效购物车行失败 user_id=%s", user["id"])
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    return {"ok": True, "removed": removed.rowcount}


@router.put("/api/shop/cart/{item_id}")
async def set_cart_quantity(
    item_id: int, body: CartQuantityIn, user: Annotated[dict, Depends(current_user)]
) -> dict:
    """改某一行的数量。

    WHERE 里带上 user_id：只按 id 改的话，拿到别人的行 id 就能改别人的车。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    """
                    UPDATE shop_cart_items SET quantity = %s
                     WHERE id = %s AND user_id = %s
                 RETURNING quantity
                    """,
                    (body.quantity, item_id, user["id"]),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("修改购物车数量失败 user_id=%s item_id=%s", user["id"], item_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="购物车里没有这一项")
    return {"ok": True, "quantity": row["quantity"]}


@router.delete("/api/shop/cart/{item_id}")
async def remove_from_cart(item_id: int, user: Annotated[dict, Depends(current_user)]) -> dict:
    """从车里删掉一行。同样带 user_id，理由见上。"""
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    "DELETE FROM shop_cart_items WHERE id = %s AND user_id = %s RETURNING id",
                    (item_id, user["id"]),
                )
            ).fetchone()
    except psycopg.Error:
        logger.exception("删除购物车行失败 user_id=%s item_id=%s", user["id"], item_id)
        raise HTTPException(status_code=500, detail="操作失败，请稍后再试") from None

    if row is None:
        raise HTTPException(status_code=404, detail="购物车里没有这一项")
    return {"ok": True}

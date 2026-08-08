"""首页配图：小程序读，后台写（写在 app/admin.py）。

原先这三组图写死在小程序的 `mock/home.js` 里，换图要改代码 + 跑上传脚本 + 发版。
挪进 `home_media` 表后，运营在后台自己换，小程序下次进首页就是新图。

约定「某个位置没配 = 用包内默认图」而不是「没配 = 不展示」：
库是空的（刚建完表、还没人配过）时，这个接口三个位都返回空数组，小程序按包内的
`mock/home.js` 渲染——也就是和上线前完全一样。这条约定让建表、发服务、配图三步
可以分开做，中间任何时刻首页都不会是空的。

地址是 COS 公开直链，不现签：首页图本来就是给所有访客看的，签名既没有保护作用，
还会让地址每次都变、微信的图片缓存全部落空（同样的取舍见 cos.presign_get 的注释）。
"""

import logging

import psycopg
from fastapi import APIRouter, HTTPException

from . import cos
from .db import pool
from .models import HOME_SLOTS

logger = logging.getLogger(__name__)

router = APIRouter(tags=["home"])


async def load_slots() -> dict[str, list[dict]]:
    """按位置取出全部配图，每个位置内按 sort_order 排好。

    没有行的位置返回空列表而不是缺键，调用方不用到处判存在。
    """
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id, slot, image_key, sort_order, updated_at
                  FROM home_media
                 ORDER BY slot, sort_order, id
                """
            )
        ).fetchall()

    slots: dict[str, list[dict]] = {slot: [] for slot in HOME_SLOTS}
    for row in rows:
        # slot 有 CHECK 约束兜着，理论上不会有别的值；真出现了也不该让整个首页 500
        if row["slot"] not in slots:
            logger.warning("home_media 里有未知的 slot=%s id=%s", row["slot"], row["id"])
            continue
        slots[row["slot"]].append(row)
    return slots


@router.get("/api/home")
async def get_home() -> dict:
    """小程序首页要的三组图。失败一律 500，由小程序回退到本地缓存。"""
    if not cos.configured():
        # 没配 COS 就拼不出可用地址。这里不能返回一堆拼半截的 URL——那会让首页
        # 变成一片裂图，且看不出是配置问题。返回空数组等价于「后台还没配」，
        # 小程序用包内默认图，同时把原因留在日志里
        logger.error("COS 未配置，/api/home 返回空配置，小程序将使用包内默认图")
        return {"ok": True, "hero": [], "showroom": [], "activity": None, "updatedAt": None}

    try:
        slots = await load_slots()
    except psycopg.Error:
        logger.exception("查询首页配图失败")
        raise HTTPException(status_code=500, detail="首页配置读取失败") from None

    def urls(slot: str) -> list[str]:
        return [cos.object_url(row["image_key"]) for row in slots[slot]]

    activity = urls("activity")
    # 全部行里最新的一次改动时间。小程序不靠它做判断，是给排查用的：
    # 「后台明明改了、小程序还是旧图」时，一眼能看出拿到的是哪一版配置
    stamps = [row["updated_at"] for rows in slots.values() for row in rows]

    return {
        "ok": True,
        "hero": urls("hero"),
        "showroom": urls("showroom"),
        # 活动位只有一张（库上有唯一索引），出参就给单值，省得小程序再取一次下标
        "activity": activity[0] if activity else None,
        "updatedAt": max(stamps).isoformat() if stamps else None,
    }

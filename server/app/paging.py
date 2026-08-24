"""列表分页与关键字搜索的共用件。

从 app/admin.py 抽出来的，因为 app/shop_admin.py（后台商品列表）和 app/shop.py
（小程序商品列表）也要用同一套。抽出来而不是各写一份，是因为这里两段逻辑都有
**反直觉的坑**（见各自的 docstring），坑的解释不该有三份——多份注释迟早会改一处漏一处。
"""

import logging
from typing import Any

import psycopg
from fastapi import HTTPException
from psycopg_pool import AsyncConnectionPool

from .db import pool

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 20
# 单页上限。后台表格一屏放不下更多，且每条记录都可能要现签图片地址，
# 页太大既没人看得完，也在做无用的签名计算
MAX_PAGE_SIZE = 100

# 关键字搜索里这几个字符对 LIKE 有特殊含义，用户搜「100%」不该匹配到所有人
LIKE_ESCAPE_CHARS = ("\\", "%", "_")

# 默认排序：最新的在前。列表接口要别的排法就自己传 order_by
DEFAULT_ORDER_BY = "created_at DESC, id DESC"


def like_pattern(keyword: str) -> str:
    """把用户输入转成 LIKE 的模式串，通配符按字面量处理。

    转义符声明在 SQL 里（`ESCAPE '\\'`），Postgres 默认也是反斜杠，写出来是为了
    不依赖 `standard_conforming_strings` 的当前取值。
    """
    escaped = keyword
    for ch in LIKE_ESCAPE_CHARS:
        escaped = escaped.replace(ch, f"\\{ch}")
    return f"%{escaped}%"


async def count_and_page(
    table: str,
    columns: str,
    conditions: list[str],
    params: list[Any],
    page: int,
    page_size: int,
    order_by: str = DEFAULT_ORDER_BY,
    db: AsyncConnectionPool | None = None,
) -> tuple[int, list[dict]]:
    """先数总条数再取当页。

    不用 `COUNT(*) OVER ()` 跟数据一起带出来：翻到超出范围的页时窗口函数没有行可返回，
    总数会变成 0，前端的分页器会跳回第一页，看起来像数据丢了。

    `table` / `columns` / `order_by` 直接拼进 SQL，**只能传代码里写死的字面量**，
    不能把请求参数透传进来。真正来自用户的值一律走 `params` 占位符。

    `db` 指定查哪个库，不传就是安东尼之家。一库一个小程序（docs/adr/0001），
    安家立业的后台列表要把它自己的池传进来。做成参数而不是让安家立业那边照抄一份
    分页逻辑：上面两段注释讲的是两个反直觉的坑，解释有第二份就迟早改一处漏一处。
    """
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    try:
        async with (db or pool).connection() as conn:
            total_row = await (
                await conn.execute(f"SELECT count(*) AS total FROM {table} {where}", params)
            ).fetchone()
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {columns}
                      FROM {table}
                      {where}
                     ORDER BY {order_by}
                     LIMIT %s OFFSET %s
                    """,
                    [*params, page_size, (page - 1) * page_size],
                )
            ).fetchall()
    except psycopg.Error:
        # 不能只回一句「查询失败」了事：后台查不出来时，运维要能从日志里看到是哪条 SQL、
        # 哪个筛选条件的问题
        logger.exception("后台列表查询失败 table=%s where=%s params=%s", table, where, params)
        raise HTTPException(status_code=500, detail="查询失败，请稍后再试") from None

    return total_row["total"], rows

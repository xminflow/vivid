"""连接池。连接串放在 .env 里，不进版本库。

一个进程装整个小程序矩阵，**每个小程序一个库、一个池**（见
docs/adr/0001-单进程多库承载小程序矩阵.md）。本模块出工厂 `make_pool()`，
并持有安东尼之家的那一个。

它叫 `pool` 而不是 `antony_pool`：二十多处 `from .db import pool` 都指着它，
改名的收益抵不上一次全量改动的风险，而那次改动没有自己的验收标准。
新接的小程序在自己的包里建池，见 app/anjia/db.py。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

# 从仓库根的 .env 读，无论从哪个目录启动都找得到
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("缺少 DATABASE_URL：把 .env.example 复制成 .env 并填上连接串")


def make_pool(url: str) -> AsyncConnectionPool:
    """按连接串建一个池。参数一致，差别只在连的是哪个库。

    check：借出连接前先探一下活。

    不加这一行的症状很具体：闲置一段时间后的**头几个请求必然 500**，池里有几条死连接
    就失败几次，之后又好了。因为对端（腾讯云 TencentDB，以及中间的 NAT / 负载均衡）
    会主动掐掉闲置连接，而池自己不知道，照样把已经关闭的连接借出去，
    于是第一条 SQL 抛 `OperationalError: consuming input failed: server closed the
    connection unexpectedly`，池这才把它丢掉。

    这个坑在开发时格外致命：小程序打开一个页面只发一个请求，正好每次都撞上那一条，
    表现就是「本地什么都看不到」，而日志里是数据库报错，看着像库有问题。

    check_connection 会在借出前发一次 `SELECT 1`，坏的直接丢掉换一条。代价是每次
    借连接多一个来回；相比"随机 500"这点开销可以忽略。
    """
    return AsyncConnectionPool(
        url,
        min_size=1,
        max_size=5,
        open=False,
        check=AsyncConnectionPool.check_connection,
        kwargs={"row_factory": dict_row},
    )


pool = make_pool(DATABASE_URL)

"""安家立业的连接池。

与安东尼之家是**两个库两个池**，同一个进程里并存（docs/adr/0001）。

没配 DATABASE_URL_ANJIA 时不建池、不注册路由，服务照常启动并在日志里写明——
和支付没配时的处理方式一致（见 app/main.py 的 lifespan）。这不是静默兜底：
选择显式降级是因为**部署顺序**。生产和 dev 那两份 .env 现在都没有这一项，
若缺配置就启动失败，那么这份代码一推上去，在有人去补 .env 之前，
安东尼之家的接口是整个挂掉的。
"""

import os

from psycopg_pool import AsyncConnectionPool

from ..db import make_pool

DATABASE_URL = os.getenv("DATABASE_URL_ANJIA", "").strip()

_pool: AsyncConnectionPool | None = make_pool(DATABASE_URL) if DATABASE_URL else None


def configured() -> bool:
    return _pool is not None


def get_pool() -> AsyncConnectionPool:
    """拿池。没配就抛——调用得到这里说明路由被错误地注册了，是代码问题不是配置问题。"""
    if _pool is None:
        raise RuntimeError("安家立业的库没配：在 server/.env 里填 DATABASE_URL_ANJIA")
    return _pool

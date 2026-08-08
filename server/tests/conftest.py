"""全部测试共用的连接池开关。

`app.db.pool` 是一个**模块级单例**，整个测试会话只有这一个。原先每个测试文件
各自写了一份 session 级的 `db` fixture 去 open/close 它，跑单个文件时看不出问题，
一次跑多个文件就会出事：pytest 在会话结束时按逆序 teardown，先轮到的那个文件把
池 close 掉，还没 teardown 完的 fixture（比如 test_home_media 要把首页配图还原回去）
再去 `pool.connection()` 就是 PoolClosed。

所以开关只放这一处，且必须是最先建立的 session fixture——依赖它的 fixture 一定
在它之后建立，也就一定在它之前 teardown，池还开着。
"""

import asyncio
import sys

import pytest

from app.db import pool

# Windows 上 asyncio 默认用 ProactorEventLoop，psycopg 的异步模式不认它
# （它没有 socket 的 add_reader），表现是每条测试都 PoolTimeout，看着像连不上库。
# 线上跑在 Linux 容器里没这个问题，所以只在测试这一侧切回 SelectorEventLoop。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session", autouse=True)
async def db():
    await pool.open(wait=True, timeout=10)
    yield
    await pool.close()


@pytest.fixture(scope="session")
async def auth_headers(db) -> dict:
    """后台接口现在全部要鉴权，各测试文件的 client 统一带上这个头。

    用配置里的超管登录一次，整个会话共用一条 token——每个测试各登一次会白白
    多跑几十次 scrypt，而 scrypt 是故意做得慢的。

    超管这条会话没有归属的 admin_users 行（超管本来就不入库），test_admin_auth.py
    那个按 `test.` 前缀删 admin_users、靠外键级联清会话的 clean() 碰不到它。不主动
    删的话，每跑一次全量测试就会在共享的远程开发库里留一条 12 小时有效的超管
    token，所以这里改成 yield，结束时显式删掉这一条。
    """
    from httpx import ASGITransport, AsyncClient

    from app.admin_auth import SUPER_PASSWORD, SUPER_USERNAME
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/admin/auth/login",
            json={"username": SUPER_USERNAME, "password": SUPER_PASSWORD},
        )
        assert r.status_code == 200, r.text
        token = r.json()["token"]

    yield {"Authorization": f"Bearer {token}"}

    # 这个 fixture 显式依赖 db，一定在 db 之后建立、也就一定在 db 之前 teardown
    # （见本文件开头那段注释），执行到这里池必然还开着
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM admin_sessions WHERE token = %s", (token,))

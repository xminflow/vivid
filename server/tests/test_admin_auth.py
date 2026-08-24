"""管理端登录与鉴权。需要库可连，跑完清掉自己造的数据。

造的管理员账号统一用 test. 前缀，清理时按前缀删——库里可能有真实账号，
不能用 TRUNCATE。
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.admin_auth import SUPER_PASSWORD, SUPER_USERNAME
from app.db import pool
from app.main import app

TEST_PREFIX = "test."

# 本文件里每一次成功登录（含反复调用 super_headers()）拿到的 token，测试跑完要
# 精确删掉这些会话对应的行。不能按 `is_super` 一刀切删：conftest.py 的
# auth_headers 也是一条超管会话，test_admin.py / test_home_media.py 整个测试
# 会话期间都在用它，粗暴地按 is_super 删会把那条也删掉，那两个文件会莫名其妙
# 全红。精确记 token、精确删，才不会误伤别的文件正在用的会话
_issued_tokens: set[str] = set()


async def clean() -> None:
    async with pool.connection() as conn:
        # 会话随外键级联删除，不用单独清
        await conn.execute("DELETE FROM admin_users WHERE username LIKE %s", (f"{TEST_PREFIX}%",))
        await conn.execute(
            "DELETE FROM admin_login_attempts WHERE username LIKE %s", (f"{TEST_PREFIX}%",)
        )
        # 超管那条也清掉：有测试会故意用错密码登超管，留下的计数会累加，
        # 攒够 5 次之后**别的**测试就会莫名其妙吃 429
        await conn.execute(
            "DELETE FROM admin_login_attempts WHERE username = %s", (SUPER_USERNAME,)
        )
        # 本文件登录超管建的那些会话按 token 精确删掉——见 _issued_tokens 上面
        # 那段注释，为什么不能按 is_super 整批删
        if _issued_tokens:
            await conn.execute(
                "DELETE FROM admin_sessions WHERE token = ANY(%s)",
                (list(_issued_tokens),),
            )
            _issued_tokens.clear()


@pytest.fixture
async def client():
    await clean()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await clean()


async def login(client, username: str, password: str):
    r = await client.post(
        "/api/admin/auth/login", json={"username": username, "password": password}
    )
    if r.status_code == 200:
        # 记下这个 token，交给 clean() 在这条测试结束时精确删掉
        _issued_tokens.add(r.json()["token"])
    return r


async def super_headers(client) -> dict:
    r = await login(client, SUPER_USERNAME, SUPER_PASSWORD)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ---------------------------------------------------------------------------
# 登录


async def test_super_admin_logs_in_with_configured_credentials(client):
    r = await login(client, SUPER_USERNAME, SUPER_PASSWORD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["token"]
    assert body["user"]["isSuper"] is True
    assert body["user"]["username"] == SUPER_USERNAME
    assert body["expiresAt"]


async def test_non_ascii_password_against_super_username_is_401_not_500(client):
    # 早期实现用 secrets.compare_digest 直接比对超管密码明文，带非 ASCII 字符的
    # 密码会让它抛 TypeError、裸奔成 500（500 和 401 的区别本身就能让人一次请求
    # 判断出这就是超管用户名）。现在密码统一走 verify_password（scrypt，对任意
    # Unicode 都能正确算），这行留作回归测试，确认这条路径不会再裸奔出 500
    r = await login(client, SUPER_USERNAME, "密码不对但是中文")
    assert r.status_code == 401, r.text


async def test_wrong_password_gives_the_same_message_as_unknown_user(client):
    wrong = await login(client, SUPER_USERNAME, "definitely-not-it")
    unknown = await login(client, "test.nobody", "definitely-not-it")
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    # 两种情况文案必须一模一样，否则等于告诉爆破的人哪些用户名存在
    assert wrong.json()["message"] == unknown.json()["message"]


async def test_repeated_failures_lock_the_username(client):
    victim = "test.locktarget"
    for _ in range(5):
        r = await login(client, victim, "nope")
        assert r.status_code == 401, r.text
    r = await login(client, victim, "nope")
    assert r.status_code == 429, r.text
    assert "次数过多" in r.json()["message"]


async def test_successful_login_clears_the_failure_count(client):
    async with pool.connection() as conn:
        await conn.execute(
            """
            INSERT INTO admin_login_attempts (username, fail_count, locked_until)
            VALUES (%s, 3, NULL)
            ON CONFLICT (username) DO UPDATE SET fail_count = 3, locked_until = NULL
            """,
            (SUPER_USERNAME,),
        )
    r = await login(client, SUPER_USERNAME, SUPER_PASSWORD)
    assert r.status_code == 200, r.text

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT fail_count FROM admin_login_attempts WHERE username = %s",
                (SUPER_USERNAME,),
            )
        ).fetchone()
    assert row is None or row["fail_count"] == 0


# ---------------------------------------------------------------------------
# 会话


async def test_me_returns_the_logged_in_admin(client):
    headers = await super_headers(client)
    r = await client.get("/api/admin/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["isSuper"] is True


async def test_missing_or_bad_token_is_401(client):
    assert (await client.get("/api/admin/auth/me")).status_code == 401
    r = await client.get("/api/admin/auth/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    # 不带 Bearer 前缀的也不认
    headers = await super_headers(client)
    raw = headers["Authorization"].removeprefix("Bearer ")
    assert (await client.get("/api/admin/auth/me", headers={"Authorization": raw})).status_code == 401


async def test_logout_kills_the_token(client):
    headers = await super_headers(client)
    assert (await client.post("/api/admin/auth/logout", headers=headers)).status_code == 200
    assert (await client.get("/api/admin/auth/me", headers=headers)).status_code == 401


async def test_expired_session_is_401(client):
    headers = await super_headers(client)
    token = headers["Authorization"].removeprefix("Bearer ")
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE admin_sessions SET expires_at = now() - interval '1 minute' WHERE token = %s",
            (token,),
        )
    assert (await client.get("/api/admin/auth/me", headers=headers)).status_code == 401


async def test_a_session_past_the_max_lifetime_stops_being_renewed(client):
    """签发超过 MAX_LIFETIME 的会话不再滑动续期，到点就自己死。

    这条守的是超管：它不入库，改密码/停用/删号那三条吊销路径一条都用不上，
    滑动续期又是「每 6 小时用一次就永远续」——没有这个绝对上限，一个泄漏的
    超管 token 就是永久有效的，后台里没有任何操作能撤销它。
    """
    headers = await super_headers(client)
    token = headers["Authorization"].removeprefix("Bearer ")

    # 造一条「7 天前签发、还剩 1 小时到期」的会话：剩余远不足半个 TTL，
    # 要不是有绝对上限，这次请求一定会把它续满 12 小时
    async with pool.connection() as conn:
        await conn.execute(
            """
            UPDATE admin_sessions
               SET created_at = now() - interval '8 days',
                   expires_at = now() + interval '1 hour'
             WHERE token = %s
            """,
            (token,),
        )

    # 还没到 expires_at，这一次请求本身仍然放行——上限只是不再续，不是立刻踢人
    assert (await client.get("/api/admin/auth/me", headers=headers)).status_code == 200

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT expires_at FROM admin_sessions WHERE token = %s", (token,)
            )
        ).fetchone()
    remaining = row["expires_at"] - datetime.now(timezone.utc)
    assert remaining < timedelta(hours=2), f"到期时间被续了：还剩 {remaining}"

    # 过了 expires_at 就彻底进不来，没有第二次机会
    async with pool.connection() as conn:
        await conn.execute(
            "UPDATE admin_sessions SET expires_at = now() - interval '1 minute' WHERE token = %s",
            (token,),
        )
    assert (await client.get("/api/admin/auth/me", headers=headers)).status_code == 401


# ---------------------------------------------------------------------------
# 改自己的密码


async def test_super_admin_cannot_change_own_password_here(client):
    headers = await super_headers(client)
    r = await client.put(
        "/api/admin/auth/password",
        headers=headers,
        json={"oldPassword": SUPER_PASSWORD, "newPassword": "brand-new-pass"},
    )
    # 超管密码在配置文件里，接口改不了
    assert r.status_code == 400, r.text
    assert "配置" in r.json()["message"]


# ---------------------------------------------------------------------------
# 鉴权真的覆盖了业务接口


# 遍历真实路由表，而不是手抄一份路径清单：将来在 admin.py 里新加接口、或者有人
# 不小心把某条路由挪出受保护的 router，这条测试都会自动纳入、自动失败，不需要
# 有人记得回来往清单里补一行——「新接口自动受保护」正是鉴权挂在 router 层而不是
# 逐个接口挂的意义，这条测试就是在守这个性质本身。
#
# 走 app.openapi()["paths"] 而不是直接遍历 app.routes：这个 FastAPI 版本
# （0.141）里 include_router() 挂上去的子路由不会被立刻拍平进 app.routes，
# 会包一层内部的 `_IncludedRouter`，直接遍历 app.routes 只能看到在 main.py
# 里用 @app.xxx 直接定义的那几条，admin/home/users 那些子 router 全部隐身，
# 断言过不了会以为鉴权哪里漏了，其实是遍历方式不对。openapi() 是 FastAPI
# 生成 /docs 用的公开接口，本来就要求把全部路由（含嵌套 router）拍平，稳。
ADMIN_PATHS = [
    (path, method.upper())
    for path, methods in app.openapi()["paths"].items()
    for method in methods
    if path.startswith("/api/admin/") and not path.startswith("/api/admin/auth/")
]

# 上面这行推导要是被改挂成空列表，parametrize 会用 0 条用例安静地「全绿」过去，
# 而这条测试原本就是要盯住「后台路由一条都没漏保护」——放在模块级，写错时收集
# 阶段就直接报错，不会被 0 用例悄悄放过
assert ADMIN_PATHS, "遍历路由表得到了空列表，鉴权覆盖测试形同虚设"


@pytest.mark.parametrize("path,method", ADMIN_PATHS)
async def test_business_endpoints_require_login(client, path, method):
    """不带 token 时，/api/admin/ 下任何一条业务接口（登录本身除外）都要 401。

    路径里 `{slot}`、`{account_id}` 这类占位符随便填一个合法值——鉴权是路由器
    级别的依赖，在请求体解析、路径参数校验之前就生效，占位符填什么不影响这里
    断言的 401。

    新加带路径参数的接口时要在下面的 format 里补上对应的键，否则这条用例会以
    KeyError 报错——这是有意的，比悄悄跳过一条没被覆盖的路由好。
    """
    url = path.format(
        slot="hero",
        account_id="1",
        appointment_id="1",
        application_id="1",
        category_id="1",
        product_id="1",
        order_id="1",
        anomaly_id="1",
        cert_id="1",
        case_id="1",
    )
    r = await client.request(method, url)
    assert r.status_code == 401, f"{method} {url}: {r.text}"


async def test_business_endpoints_work_with_a_token(client):
    headers = await super_headers(client)
    r = await client.get("/api/admin/appointments", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# ---------------------------------------------------------------------------
# 账号管理


async def make_account(client, headers, username="test.alice", password="alice-pass-1"):
    r = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": username, "displayName": "爱丽丝", "password": password},
    )
    assert r.status_code == 201, r.text
    return r.json()["account"]


async def test_super_creates_an_account_that_can_log_in(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    assert account["username"] == "test.alice"
    assert account["status"] == "active"
    # 雪花 ID 超出 JS 安全整数范围，出接口必须是字符串
    assert isinstance(account["id"], str)
    # 哈希一个字节都不能出接口
    assert "password" not in account and "passwordHash" not in account

    r = await login(client, "test.alice", "alice-pass-1")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["isSuper"] is False


async def test_normal_admin_cannot_touch_account_management(client):
    headers = await super_headers(client)
    await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}

    assert (await client.get("/api/admin/accounts", headers=alice)).status_code == 403
    r = await client.post(
        "/api/admin/accounts",
        headers=alice,
        json={"username": "test.bob", "displayName": "", "password": "bob-pass-11"},
    )
    assert r.status_code == 403
    # 但业务接口她是能看的
    assert (await client.get("/api/admin/appointments", headers=alice)).status_code == 200


async def test_normal_admin_changes_own_password(client):
    headers = await super_headers(client)
    await make_account(client, headers)
    # 登两次，拿两条不同的会话：一条模拟「别处」还开着的登录态，用另一条去改密码，
    # 才能验出 admin_auth.py 里 `DELETE ... AND token <> %s` 那一半——只验当前
    # 这条会话留着，验不出别处的会话是不是真的被清掉了
    r1 = await login(client, "test.alice", "alice-pass-1")
    elsewhere = {"Authorization": f"Bearer {r1.json()['token']}"}
    r2 = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r2.json()['token']}"}

    bad = await client.put(
        "/api/admin/auth/password",
        headers=alice,
        json={"oldPassword": "wrong-one-11", "newPassword": "alice-pass-2"},
    )
    assert bad.status_code == 400, bad.text

    ok = await client.put(
        "/api/admin/auth/password",
        headers=alice,
        json={"oldPassword": "alice-pass-1", "newPassword": "alice-pass-2"},
    )
    assert ok.status_code == 200, ok.text

    assert (await login(client, "test.alice", "alice-pass-1")).status_code == 401
    assert (await login(client, "test.alice", "alice-pass-2")).status_code == 200
    # 改密码的那条会话自己留着，不用重登
    assert (await client.get("/api/admin/auth/me", headers=alice)).status_code == 200
    # 「别处」那条会话被清掉了——这是改密码防不住已登录的人这条安全行为的核心
    assert (await client.get("/api/admin/auth/me", headers=elsewhere)).status_code == 401


async def test_reserved_and_duplicate_usernames_are_refused(client):
    headers = await super_headers(client)
    reserved = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": SUPER_USERNAME, "displayName": "", "password": "whatever-11"},
    )
    # 建一个和超管同名的普通账号，登录时永远被超管判定抢先命中，是个点不动的鬼影
    assert reserved.status_code == 400, reserved.text
    # 光断言 400 不够：SUPER_USERNAME 现在配的是 "root"，格式上也合法，这个 400
    # 确实来自 admin_accounts.py 里的保留检查。但哪天超管用户名配成不合
    # ADMIN_USERNAME_PATTERN 的值（比如带 @ 或短于 3 位），Pydantic 的格式校验会
    # 抢在保留检查前面同样返 400，这条测试会安静地测不到保留检查那一分支了。
    # 断言文案钉死来源，不让这条覆盖跟着配置值漂移
    assert "被保留" in reserved.json()["message"], reserved.text

    await make_account(client, headers)
    dup = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": "test.alice", "displayName": "", "password": "another-11"},
    )
    assert dup.status_code == 409, dup.text


async def test_bad_username_or_short_password_is_rejected(client):
    headers = await super_headers(client)
    short = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": "test.bob", "displayName": "", "password": "short"},
    )
    assert short.status_code == 400
    bad_name = await client.post(
        "/api/admin/accounts",
        headers=headers,
        json={"username": "有中文", "displayName": "", "password": "good-pass-11"},
    )
    assert bad_name.status_code == 400


async def test_disabling_an_account_kills_its_session_immediately(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await client.get("/api/admin/appointments", headers=alice)).status_code == 200

    r = await client.put(
        f"/api/admin/accounts/{account['id']}/status",
        headers=headers,
        json={"status": "disabled"},
    )
    assert r.status_code == 200, r.text

    # 手上那条 token 立刻不作数
    assert (await client.get("/api/admin/appointments", headers=alice)).status_code == 401
    # 也登不回来
    assert (await login(client, "test.alice", "alice-pass-1")).status_code == 403

    # 闭环走完：重新启用，账号要能登回来。"active" 这条分支、以及
    # set_status 里 `if body.status == "disabled"` 的 else 分支，
    # 在这行加之前从没被执行过
    r = await client.put(
        f"/api/admin/accounts/{account['id']}/status",
        headers=headers,
        json={"status": "active"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["account"]["status"] == "active"
    assert (await login(client, "test.alice", "alice-pass-1")).status_code == 200


async def test_reset_password_logs_the_account_out_everywhere(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}

    r = await client.put(
        f"/api/admin/accounts/{account['id']}/password",
        headers=headers,
        json={"password": "reset-pass-11"},
    )
    assert r.status_code == 200, r.text

    assert (await client.get("/api/admin/auth/me", headers=alice)).status_code == 401
    assert (await login(client, "test.alice", "reset-pass-11")).status_code == 200


async def test_deleting_an_account_cascades_its_sessions(client):
    headers = await super_headers(client)
    account = await make_account(client, headers)
    r = await login(client, "test.alice", "alice-pass-1")
    alice = {"Authorization": f"Bearer {r.json()['token']}"}

    r = await client.delete(f"/api/admin/accounts/{account['id']}", headers=headers)
    assert r.status_code == 200, r.text

    assert (await client.get("/api/admin/auth/me", headers=alice)).status_code == 401
    assert (await login(client, "test.alice", "alice-pass-1")).status_code == 401


async def test_operations_on_a_missing_account_are_404(client):
    headers = await super_headers(client)
    missing = "1234567890123456789"
    assert (await client.delete(f"/api/admin/accounts/{missing}", headers=headers)).status_code == 404
    r = await client.put(
        f"/api/admin/accounts/{missing}/status", headers=headers, json={"status": "disabled"}
    )
    assert r.status_code == 404
    r = await client.put(
        f"/api/admin/accounts/{missing}/password", headers=headers, json={"password": "whatever-11"}
    )
    assert r.status_code == 404


async def test_account_list_shows_created_accounts(client):
    headers = await super_headers(client)
    await make_account(client, headers)
    r = await client.get("/api/admin/accounts", headers=headers)
    assert r.status_code == 200, r.text
    names = [a["username"] for a in r.json()["items"]]
    assert "test.alice" in names


# ---------------------------------------------------------------------------
# 登录路径的耗时等价（防用户名枚举的计时侧信道）


async def _scrypt_calls(monkeypatch, client, username, password):
    """数这一次登录请求里 hashlib.scrypt 真正被调用了几次。

    打的是 `hashlib.scrypt` 本体而不是 `verify_password`：前者是真正花掉那几十
    毫秒的地方，任何「被短路成 0 次」或「不小心跑了 2 次」都逃不掉；换成打
    verify_password 的话，将来若有人在里面加一层缓存或提前 return，计数照样是 1。
    """
    n = 0
    real = hashlib.scrypt

    def counting(*args, **kwargs):
        nonlocal n
        n += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(hashlib, "scrypt", counting)
    try:
        r = await login(client, username, password)
    finally:
        monkeypatch.setattr(hashlib, "scrypt", real)
    return n, r


async def test_every_login_path_runs_exactly_one_scrypt(client, monkeypatch):
    """每条登录路径都恰好跑一次 scrypt，一次不多一次不少。

    这是 admin_auth.py 里两处「看着像多余代码」的存在理由，守的是同一件事：
    三条路径（超管 / 真实管理员 / 不存在的用户名）耗时必须没有可测的差别，
    否则响应快慢本身就是个免费的用户名探测 oracle，能被用来反推出超管用户名——
    而超管用户名一旦泄漏，就能被恶意反复锁定（见 deploy/README.md）。

    那两处分别是：`_DUMMY_PASSWORD_HASH`（用户名不存在时垫一次 scrypt，不能让
    `and` 短路把它省掉）和 `_SUPER_PASSWORD_HASH`（超管的配置明文也预先哈希，
    不走 compare_digest，否则超管那条路径会比别的快一个数量级）。

    这条断言此前只在改动时人工核过一次，没有留在库里；它已经被改坏过两轮，
    值得有个自动回归盯着——尤其是 scrypt 现在被挪进了 anyio 线程池，
    调用点的写法变了，更容易在下次重构里被无意中改成两次或零次。
    """
    headers = await super_headers(client)
    await make_account(client, headers)

    # 顺序有意为之：先成功登一次超管把失败计数清零，后面那两次失败加起来
    # 也远够不到 MAX_FAILURES=5，不会有哪条用例被 429 顶掉
    cases = [
        ("超管 / 密码正确", SUPER_USERNAME, SUPER_PASSWORD, 200),
        ("超管 / 密码错误", SUPER_USERNAME, "definitely-not-it", 401),
        ("普通管理员 / 密码正确", "test.alice", "alice-pass-1", 200),
        ("用户名不存在", "test.nobody", "definitely-not-it", 401),
    ]
    for label, username, password, expected in cases:
        n, r = await _scrypt_calls(monkeypatch, client, username, password)
        assert r.status_code == expected, f"{label}: {r.status_code} {r.text}"
        assert n == 1, f"{label}: scrypt 跑了 {n} 次，应当恰好 1 次"

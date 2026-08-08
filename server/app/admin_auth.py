"""管理端登录与鉴权。

超级管理员**不入库**，由配置文件的 ADMIN_SUPER_USERNAME / ADMIN_SUPER_PASSWORD
定义。由此推出两件事：

1. admin_users 表里每一行都是普通管理员，所以那张表不需要 role 列。
   「是不是超管」这个信息只存在于会话行的 is_super 上。
2. 超管改密码 = 改配置 + 重启服务，接口改不了（见 change_password）。
   代价是运维要动配置，好处是超管既停用不了也删不掉，后台锁不死在门外。

登录链路：POST /api/admin/auth/login → 比对配置或库里的哈希 → 建一条
admin_sessions → 前端带 Authorization: Bearer <token> 调后续接口。
"""

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, status

# 这一行同时把仓库根的 .env 读进环境变量（db.py 在导入时 load_dotenv），
# 所以下面的 os.getenv 一定拿得到值。别把这个 import 挪到 getenv 后面
from .db import pool
from .models import AdminLoginIn, AdminPasswordChangeIn
from .security import hash_password, verify_password

logger = logging.getLogger(__name__)

SUPER_USERNAME = os.getenv("ADMIN_SUPER_USERNAME", "").strip()
SUPER_PASSWORD = os.getenv("ADMIN_SUPER_PASSWORD", "")
if not SUPER_USERNAME or not SUPER_PASSWORD:
    # 不给默认值：默认账号会让某次忘了配的部署把后台悄悄开成公开的，
    # 而后台出的是全量客户手机号。宁可起不来
    raise RuntimeError(
        "缺少 ADMIN_SUPER_USERNAME 或 ADMIN_SUPER_PASSWORD："
        "把 .env.example 里管理后台那两项复制到 .env 并填上"
    )
try:
    SUPER_PASSWORD.encode("ascii")
except UnicodeEncodeError:
    # 密码现在统一走 verify_password（scrypt），对任意 Unicode 都能正确算，
    # 这条校验不再是为了绕开某个函数的字符集限制。保留它是运维层面的考虑：
    # 超管密码要在部署环境里手敲/粘贴进配置文件，全角符号、不可见字符这类
    # 非 ASCII 字符很容易在不同终端/编辑器之间跑丢或跑样却查不出来，
    # 与其让超管某天莫名其妙登不进去，不如启动期直接报错逼着换成 ASCII
    raise RuntimeError(
        "ADMIN_SUPER_PASSWORD 只能用 ASCII 字符：避免配置文件在不同环境间"
        "传递时混进全角符号、不可见字符等不容易排查的问题"
    ) from None

# 登录态有效期。比小程序那边的 30 天短得多：后台一屏就是全量客户手机号，
# 一台没锁屏的电脑不该一个月都是登录状态
TOKEN_TTL = timedelta(hours=12)

# 连错几次锁多久。锁的是用户名不是 IP：超管账号只有一个，锁它才拦得住爆破，
# 锁 IP 会让同一个办公室的运营互相牵连
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)

# 用户名不存在时拿来垫时间的假哈希，见 login() 里的用法。
# 内容本身没有意义（甚至用不到明文），只是让「查无此人」也要付一次 scrypt
# 的 CPU 代价——不这么做，「不存在」和「存在但密码错」的响应耗时会差出
# scrypt 那几十毫秒（见 security.py），足够当用户名探测的计时侧信道
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))

# 超管密码本来是配置文件里的明文，比对时却不直接和明文做 compare_digest：
# 那样超管这条路径只有「一次常量时间比较」，比普通管理员那条「一次 SELECT +
# 一次 scrypt」快得多，快慢本身就成了「这是不是超管用户名」的计时侧信道——
# 跟 I2 挡的用户名枚举是同一类问题，只是从状态码通道换到了耗时通道。
# 把配置密码也预先算成哈希、登录时同样走一次 verify_password，
# 三条路径（超管 / 真实管理员 / 不存在的用户名）就都是「1 次 SELECT + 1 次
# scrypt」，没有谁能抢跑。别嫌多此一举把这个哈希删掉、改回明文比较
_SUPER_PASSWORD_HASH = hash_password(SUPER_PASSWORD)

router = APIRouter(prefix="/api/admin/auth", tags=["admin-auth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 鉴权依赖


async def current_admin(authorization: str = Header(default="")) -> dict:
    """从 Authorization 头解出当前管理员。

    挂在 admin_router 上（见 app/admin.py），一次覆盖全部后台接口，
    包括将来新加的——逐个接口去挂迟早会漏一个。
    """
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                """
                SELECT s.admin_id, s.is_super, s.expires_at,
                       a.username, a.display_name, a.status
                  FROM admin_sessions s
                  LEFT JOIN admin_users a ON a.id = s.admin_id
                 WHERE s.token = %s
                   AND s.expires_at > now()
                """,
                (token,),
            )
        ).fetchone()

        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")

        # 停用的账号即便手上还有没过期的 token 也进不来。
        # 停用接口会顺手删它的会话，这里再查一次是兜底：将来若有别处改了状态
        # 却忘了删会话，鉴权层仍然挡得住
        if not row["is_super"] and row["status"] != "active":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被停用，请联系超级管理员")

        # 滑动续期：剩余不足一半就续满。固定 12 小时会让运营正干到一半被踢出去，
        # 而一直在用的会话本来就说明人在电脑前
        if row["expires_at"] - _now() < TOKEN_TTL / 2:
            await conn.execute(
                "UPDATE admin_sessions SET expires_at = %s WHERE token = %s",
                (_now() + TOKEN_TTL, token),
            )

    return {
        "token": token,
        # 超管不入库，没有 id。所有拿这个字段去查 admin_users 的地方都要先判 isSuper
        "id": row["admin_id"],
        "username": SUPER_USERNAME if row["is_super"] else row["username"],
        "displayName": "超级管理员" if row["is_super"] else row["display_name"],
        "isSuper": row["is_super"],
    }


async def current_super(admin: dict = Depends(current_admin)) -> dict:
    """只有超管能过。挂在 admin_accounts 那个 router 上。"""
    if not admin["isSuper"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "没有权限")
    return admin


# ---------------------------------------------------------------------------
# 登录失败计数


async def _attempts(conn, username: str) -> dict | None:
    return await (
        await conn.execute(
            "SELECT fail_count, locked_until FROM admin_login_attempts WHERE username = %s",
            (username,),
        )
    ).fetchone()


async def _record_failure(conn, username: str) -> None:
    """失败计数 +1，到阈值就锁一段时间。

    递增和判断阈值整句下沉到这一条 SQL 里算，不在 Python 里「读出来 -> 算 ->
    写回去」：并发的两次失败请求会都读到同一个旧值，各自 +1 写回，计数总共
    只前进 1 次而不是 2 次——攻击者只要把请求并发发出去，就能把锁定阈值按
    并发数放大，限流形同虚设。`INSERT ... ON CONFLICT DO UPDATE` 会给冲突行
    加锁，并发请求在这条语句上自然排队，不会看到彼此的中间状态。

    锁过期后从 1 重新数，不接着上次往上加：一个人今天错 4 次、下周再错 1 次
    就被锁，那不是爆破的特征，只是记性不好——下面的 CASE 就是干这个的。
    """
    await conn.execute(
        """
        INSERT INTO admin_login_attempts (username, fail_count, locked_until)
        VALUES (%s, 1, NULL)
        ON CONFLICT (username) DO UPDATE SET
          fail_count = CASE
            WHEN admin_login_attempts.locked_until IS NOT NULL
             AND admin_login_attempts.locked_until <= now()
            THEN 1
            ELSE admin_login_attempts.fail_count + 1
          END,
          -- 这里没法直接引用上面刚算出来的 fail_count（同一条 UPDATE 的
          -- SET 列表里互相看不到彼此的新值），只能把同一个 CASE 再写一遍
          locked_until = CASE
            WHEN (
              CASE
                WHEN admin_login_attempts.locked_until IS NOT NULL
                 AND admin_login_attempts.locked_until <= now()
                THEN 1
                ELSE admin_login_attempts.fail_count + 1
              END
            ) >= %s
            THEN now() + %s
            ELSE NULL
          END
        """,
        (username, MAX_FAILURES, LOCKOUT),
    )


async def _clear_failures(conn, username: str) -> None:
    await conn.execute("DELETE FROM admin_login_attempts WHERE username = %s", (username,))


# ---------------------------------------------------------------------------
# 接口


@router.post("/login")
async def login(body: AdminLoginIn) -> dict:
    username = body.username

    try:
        async with pool.connection() as conn:
            prior = await _attempts(conn, username)
            if prior and prior["locked_until"] and prior["locked_until"] > _now():
                minutes = max(1, round((prior["locked_until"] - _now()).total_seconds() / 60))
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS,
                    f"密码错误次数过多，请 {minutes} 分钟后再试",
                )

            admin_id: int | None = None
            is_super = username == SUPER_USERNAME

            # 不管是不是超管都先查一次 admin_users——超管本来就不在这张表里，
            # 这里必然查不到，但每一条路径都要付这一次 SELECT 往返，否则
            # 「查不查库」本身就会在超管和别的用户名之间制造耗时差，
            # 被人拿去反推超管用户名（跟下面 verify_password 要防的是同一类问题）
            row = await (
                await conn.execute(
                    "SELECT id, password_hash, status FROM admin_users WHERE username = %s",
                    (username,),
                )
            ).fetchone()

            if is_super:
                ok = verify_password(body.password, _SUPER_PASSWORD_HASH)
            else:
                # row 为 None（用户名不存在）时也要跑一遍 verify_password，用假哈希
                # 垫上同样的 scrypt 耗时——`and` 短路会让「不存在」比「存在但密码错」
                # 快一个数量级，那个时间差本身就是一个免费的用户名探测 oracle。
                # 这行不是多余代码，看着像 dead code 但删了就是开了个计时侧信道
                ok = verify_password(
                    body.password, row["password_hash"] if row else _DUMMY_PASSWORD_HASH
                )
                if row is not None and ok and row["status"] != "active":
                    # 密码是对的，只是号被停了。这条不算失败计数——
                    # 本人反复试自己的密码不是爆破
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN, "账号已被停用，请联系超级管理员"
                    )
                if row is not None and ok:
                    admin_id = row["id"]

            if not ok:
                await _record_failure(conn, username)
                # 显式提交：接下来这行 raise 会让 `async with pool.connection()`
                # 带着异常退出，psycopg 遇到异常退出会回滚整个事务——不提前提交，
                # 刚记的这次失败次数会被回滚掉，锁定机制形同虚设
                await conn.commit()
                logger.warning("后台登录失败 username=%s", username)
                # 「用户不存在」和「密码错误」回同一句：区分开等于给对方做用户名枚举
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码不正确")

            await _clear_failures(conn, username)

            token = secrets.token_urlsafe(32)
            expires_at = _now() + TOKEN_TTL
            await conn.execute(
                """
                INSERT INTO admin_sessions (token, admin_id, is_super, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (token, admin_id, is_super, expires_at),
            )

            display_name = "超级管理员"
            if not is_super:
                updated = await (
                    await conn.execute(
                        """
                        UPDATE admin_users
                           SET last_login_at = now(), login_count = login_count + 1
                         WHERE id = %s
                        RETURNING display_name
                        """,
                        (admin_id,),
                    )
                ).fetchone()
                display_name = updated["display_name"]
    except HTTPException:
        raise
    except psycopg.Error:
        logger.exception("后台登录出错 username=%s", username)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "登录失败，请稍后再试"
        ) from None

    logger.info("后台登录成功 username=%s is_super=%s", username, is_super)
    return {
        "ok": True,
        "token": token,
        "expiresAt": expires_at.isoformat(),
        "user": {"username": username, "displayName": display_name, "isSuper": is_super},
    }


@router.post("/logout")
async def logout(admin: dict = Depends(current_admin)) -> dict:
    """只删当前这一条会话，不动这个人在别的电脑上的登录态。"""
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM admin_sessions WHERE token = %s", (admin["token"],))
    return {"ok": True}


@router.get("/me")
async def read_me(admin: dict = Depends(current_admin)) -> dict:
    """前端启动时用它验一次本地缓存的 token 还作不作数。"""
    return {
        "ok": True,
        "user": {
            "username": admin["username"],
            "displayName": admin["displayName"],
            "isSuper": admin["isSuper"],
        },
    }


@router.put("/password")
async def change_password(
    body: AdminPasswordChangeIn, admin: dict = Depends(current_admin)
) -> dict:
    """改自己的密码。改完当前这条会话留着，这个人在别处的会话全部作废。"""
    if admin["isSuper"]:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "超级管理员的密码在服务器配置文件里维护，改完需要重启服务",
        )

    async with pool.connection() as conn:
        row = await (
            await conn.execute(
                "SELECT password_hash FROM admin_users WHERE id = %s", (admin["id"],)
            )
        ).fetchone()
        if row is None or not verify_password(body.old_password, row["password_hash"]):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前密码不正确")

        await conn.execute(
            "UPDATE admin_users SET password_hash = %s WHERE id = %s",
            (hash_password(body.new_password), admin["id"]),
        )
        # 密码换了，别处的登录态就该失效——不然改密码防不住已经登进去的人
        await conn.execute(
            "DELETE FROM admin_sessions WHERE admin_id = %s AND token <> %s",
            (admin["id"], admin["token"]),
        )

    logger.info("后台管理员改密码 username=%s", admin["username"])
    return {"ok": True}

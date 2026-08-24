"""安家立业的用户与登录。

链路和安东尼之家同形：小程序 wx.login 拿 code → POST /api/anjia/auth/login →
服务端用**安家立业自己的** appid 换 openid，按 openid 落库（没有就建），发 token。
之后带 Authorization: Bearer <token> 调 /api/anjia/users/me。

路径全部挂在 /api/anjia/ 下：一个进程装两个小程序，前缀是它们唯一的分界。
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, status
from psycopg import AsyncConnection

from ..models import LoginIn
from ..snowflake import next_id
from ..wechat import WeChatError, code2session, credentials
from . import certifications
from .db import get_pool
from .models import CertificationIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/anjia")

# 登录态有效期。用户不常主动登录，给足一个月，过期了前端静默重登
TOKEN_TTL = timedelta(days=30)

# 出接口的字段。session_key、token 是密钥，openid 前端也用不上，都不在这里
USER_COLUMNS = (
    "id, nickname, avatar_url, created_at, last_login_at, login_count,"
    " is_member, member_expires_at"
)

# 安家立业自己的 appid/secret。传错就是认错人，所以后缀写死在这一处
WX_SUFFIX = "_ANJIA"


def to_json(row: dict) -> dict:
    """库里的行转成小程序要的驼峰。

    id 必须转字符串：雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，
    直接出数字前端解析时末几位会变。
    """
    return {
        "id": str(row["id"]),
        "nickname": row["nickname"],
        "avatarUrl": row["avatar_url"],
        "createdAt": row["created_at"].isoformat(),
        "lastLoginAt": row["last_login_at"].isoformat() if row["last_login_at"] else None,
        "loginCount": row["login_count"],
        "isMember": row["is_member"],
        # 出给前端只为展示。判断是不是会员一律看 isMember，别拿这个跟当前时间比——
        # 一期不校验过期是有意为之，见 docs/adr/0002-会员到期时间只写不读.md
        "memberExpiresAt": (
            row["member_expires_at"].isoformat() if row["member_expires_at"] else None
        ),
    }


async def user_json(conn: AsyncConnection, row: dict) -> dict:
    """完整的身份出参：登录态 + 会员 + 企业认证，一次给全。

    三层身份（个人 / 企业认证 / 会员）是正交的，但对前端来说是**一个**东西——
    它只有一处渲染身份的地方。所以每个会改动身份的接口都回这一份完整对象，
    前端整体替换本机快照即可，不必各写一份合并逻辑（见 anjia/utils/api.js）。
    """
    return {**to_json(row), **await certifications.identity(conn, row["id"])}


async def current_user(authorization: str = Header(default="")) -> dict:
    """从 Authorization 头解出当前用户。挂在需要登录的路由上。"""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")

    async with get_pool().connection() as conn:
        row = await (
            await conn.execute(
                f"""
                SELECT {USER_COLUMNS}
                  FROM users
                 WHERE token = %s
                   AND (token_expires_at IS NULL OR token_expires_at > now())
                """,
                (token,),
            )
        ).fetchone()

    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新进入小程序")
    return row


@router.post("/auth/login")
async def login(body: LoginIn) -> dict:
    try:
        session = await code2session(body.code, *credentials(WX_SUFFIX), suffix=WX_SUFFIX)
    except WeChatError as exc:
        if exc.detail:
            logger.warning("[anjia] 登录失败：%s", exc.detail)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from exc

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + TOKEN_TTL
    logger.debug("[anjia] code2session 通过：openid=%s unionid=%s",
                 session["openid"], session["unionid"])

    try:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    INSERT INTO users
                      (id, openid, unionid, session_key, nickname, avatar_url,
                       token, token_expires_at, last_login_at, login_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), 1)
                    ON CONFLICT (openid) DO UPDATE SET
                      -- 老用户：id 不动，换新的登录态
                      unionid          = COALESCE(EXCLUDED.unionid, users.unionid),
                      session_key      = EXCLUDED.session_key,
                      token            = EXCLUDED.token,
                      token_expires_at = EXCLUDED.token_expires_at,
                      last_login_at    = now(),
                      login_count      = users.login_count + 1,
                      -- 昵称头像只在这次真授权了才覆盖，否则会被空串洗掉
                      nickname   = CASE WHEN EXCLUDED.nickname   <> '' THEN EXCLUDED.nickname
                                        ELSE users.nickname END,
                      avatar_url = CASE WHEN EXCLUDED.avatar_url <> '' THEN EXCLUDED.avatar_url
                                        ELSE users.avatar_url END
                    RETURNING {USER_COLUMNS}
                    """,
                    (
                        next_id(),
                        session["openid"],
                        session["unionid"],
                        session["session_key"],
                        body.nickname,
                        body.avatar_url,
                        token,
                        expires_at,
                    ),
                )
            ).fetchone()
            # 在同一个连接里把身份补齐：老用户登录时要带上他的会员与认证状态，
            # 小程序首屏就是拿这一份渲染的
            user = await user_json(conn, row)
    except psycopg.Error as exc:
        logger.exception("[anjia] 落库失败")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "登录失败，请稍后再试") from exc

    logger.debug("[anjia] 登录完成：id=%s 第 %d 次登录", row["id"], row["login_count"])
    return {
        "ok": True,
        "token": token,
        "expiresAt": expires_at.isoformat(),
        "user": user,
    }


@router.get("/users/me")
async def read_me(user: dict = Depends(current_user)) -> dict:
    async with get_pool().connection() as conn:
        return {"ok": True, "user": await user_json(conn, user)}


@router.post("/users/me/membership")
async def join_membership(user: dict = Depends(current_user)) -> dict:
    """开通会员。一期免费、即时生效、不分等级（需求文档 2.2）。

    整条 SQL 是幂等的，靠的是两个 COALESCE 而不是「先查再写」：已经是会员的人
    再点一次，开通时间与到期时间都保持首次开通那两个值。**不能改成无条件覆盖**
    ——那等于谁都可以反复点击给自己刷有效期。

    到期时间写成一年后，但一期没有任何地方校验它，理由见
    docs/adr/0002-会员到期时间只写不读.md。用 `interval '1 year'` 而不是加 365 天，
    是为了让闰年落在正确的日子上。
    """
    try:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE users
                       SET is_member         = true,
                           member_since      = COALESCE(member_since, now()),
                           member_expires_at = COALESCE(
                               member_expires_at, now() + interval '1 year')
                     WHERE id = %s
                    RETURNING {USER_COLUMNS}
                    """,
                    (user["id"],),
                )
            ).fetchone()
            joined = await user_json(conn, row)
    except psycopg.Error as exc:
        logger.exception("[anjia] 开通会员失败：id=%s", user["id"])
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "开通失败，请稍后再试"
        ) from exc

    logger.info("[anjia] 开通会员：id=%s", row["id"])
    return {"ok": True, "user": joined}


@router.post("/certifications")
async def submit_certification(
    body: CertificationIn, user: dict = Depends(current_user)
) -> dict:
    """提交企业认证申请。

    提交进的是待审队列，不是直接生效——认证是首页发布权的唯一闸门，需求文档 4.3.2
    明说「正因为认证从简、核验宽松，审核机制必须保留」。

    两种情况挡在这里：已经有一条待审的（同时两条申请，管理员不知道该以哪条为准），
    以及已经是企业认证账号的（通过后不能自行改公司名，要改得联系运营重新提交，
    否则这个身份对读者就不可信了）。库上的两条部分唯一索引是同样约束的最后一道，
    这里先拦一次只是为了给出一句人话，而不是让用户看到一个 500。
    """
    try:
        async with get_pool().connection() as conn:
            if await certifications.approved(conn, user["id"]):
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "已经是企业认证账号，如需修改请联系我们"
                )
            if await certifications.has_pending(conn, user["id"]):
                raise HTTPException(status.HTTP_409_CONFLICT, "已有一份认证申请正在审核中")

            await certifications.insert(
                conn,
                user["id"],
                body.company_name,
                body.contact_name,
                body.contact_phone,
            )
            submitted = await user_json(conn, user)
    except psycopg.Error as exc:
        logger.exception("[anjia] 提交企业认证失败：id=%s", user["id"])
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "提交失败，请稍后再试"
        ) from exc

    logger.info("[anjia] 提交企业认证：id=%s 公司=%s", user["id"], body.company_name)
    return {"ok": True, "user": submitted}

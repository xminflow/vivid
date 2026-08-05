"""用户与登录。

登录链路：小程序 wx.login 拿 code → POST /api/auth/login →
服务端拿 code 换 openid，按 openid 落库（没有就建），发一个 token 回去。
之后小程序带 Authorization: Bearer <token> 调 /api/users/me 读写资料。

不用 openid 当登录凭证：openid 是长期不变的身份，一旦从前端泄出去就换不掉；
token 能过期、能重发、能吊销。
"""

import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from . import cos
from .db import pool
from .models import AvatarIn, LoginIn, ProfileIn
from .snowflake import next_id
from .wechat import WeChatError, code2session

router = APIRouter()

# 登录态有效期。小程序用户不常主动登录，给足一个月，过期了前端静默重登
TOKEN_TTL = timedelta(days=30)

# 出接口的字段。session_key、token 是密钥，openid 前端也用不上，都不在这里
USER_COLUMNS = """
    id, nickname, avatar_url, avatar_key, member_name, phone, email, birthday, gender,
    province, city, district, is_member, member_expires_at, status, created_at
"""


def avatar_url(row: dict) -> str:
    """头像出接口是一个带签名的临时地址。

    库里存的是 COS 对象键，键本身给客户端没用；桶和地域将来会变，也不能存死 URL。
    用户没设过头像时 avatar_key 是空串，回落到微信授权时给的外链（通常也是空），
    客户端拿到空串就显示会员名首字。
    """
    key = row["avatar_key"]
    if key and cos.configured():
        return cos.presign_get(key, cos.AVATAR_EXPIRE_SECONDS)
    return row["avatar_url"]


def to_json(row: dict) -> dict:
    """库里的行转成小程序要的驼峰。

    id 必须转字符串：雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，
    直接出数字前端解析时末几位会变。
    """
    region = [p for p in (row["province"], row["city"], row["district"]) if p]
    return {
        "id": str(row["id"]),
        "nickname": row["nickname"],
        "avatarUrl": avatar_url(row),
        "memberName": row["member_name"],
        "phone": row["phone"],
        "email": row["email"],
        "birthday": row["birthday"].isoformat() if row["birthday"] else "",
        "gender": row["gender"],
        "region": region,
        "isMember": row["is_member"],
        "memberExpiresAt": (
            row["member_expires_at"].isoformat() if row["member_expires_at"] else None
        ),
        "createdAt": row["created_at"].isoformat(),
    }


async def current_user(authorization: str = Header(default="")) -> dict:
    """从 Authorization 头解出当前用户。挂在需要登录的路由上。"""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")

    async with pool.connection() as conn:
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
    if row["status"] == "banned":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被停用，请联系顾问")
    return row


async def current_user_or_none(authorization: str = Header(default="")) -> dict | None:
    """没登录也放行。给「未登录也能提交」的表单用，登录了就顺手记上是谁提交的。"""
    try:
        return await current_user(authorization)
    except HTTPException:
        return None


@router.post("/api/auth/login")
async def login(body: LoginIn) -> dict:
    try:
        session = await code2session(body.code)
    except WeChatError as exc:
        if exc.detail:
            print(f"[login failed] {exc.detail}")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.message) from exc

    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + TOKEN_TTL

    try:
        async with pool.connection() as conn:
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
    except psycopg.Error as exc:
        print(f"[login failed] {exc}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "登录失败，请稍后再试") from exc

    if row["status"] == "banned":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已被停用，请联系顾问")

    return {
        "ok": True,
        "token": token,
        "expiresAt": expires_at.isoformat(),
        "user": to_json(row),
    }


@router.get("/api/users/me")
async def read_me(user: dict = Depends(current_user)) -> dict:
    return {"ok": True, "user": to_json(user)}


@router.get("/api/users/me/appointments")
async def my_appointments(user: dict = Depends(current_user)) -> dict:
    """「我的」页的预约记录。只出这个人自己的，别人的一条也带不出来。"""
    async with pool.connection() as conn:
        rows = await (
            await conn.execute(
                """
                SELECT id, name, phone, visitor_type, visit_date, party_size,
                       purpose, note, space_id, status, created_at
                  FROM appointments
                 WHERE user_id = %s
                 ORDER BY created_at DESC
                 LIMIT 50
                """,
                (user["id"],),
            )
        ).fetchall()

    items = [
        {
            "id": row["id"],
            "name": row["name"],
            "phone": row["phone"],
            "visitorType": row["visitor_type"],
            "visitDate": row["visit_date"].isoformat(),
            "partySize": row["party_size"],
            "purpose": row["purpose"],
            "note": row["note"],
            "spaceId": row["space_id"],
            "status": row["status"],
            "createdAt": row["created_at"].isoformat(),
        }
        for row in rows
    ]
    return {"ok": True, "total": len(items), "items": items}


@router.put("/api/users/me")
async def update_me(profile: ProfileIn, user: dict = Depends(current_user)) -> dict:
    """整份覆盖。小程序那边是改一个字段就提交一次，传的始终是完整资料。"""
    province, city, district = profile.region_parts()

    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE users
                       SET member_name = %s, phone = %s, email = %s, birthday = %s,
                           gender = %s, province = %s, city = %s, district = %s
                     WHERE id = %s
                    RETURNING {USER_COLUMNS}
                    """,
                    (
                        profile.member_name,
                        profile.phone,
                        profile.email,
                        profile.birthday,
                        profile.gender,
                        province,
                        city,
                        district,
                        user["id"],
                    ),
                )
            ).fetchone()
    except psycopg.Error as exc:
        print(f"[profile update failed] {exc}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "保存失败，请稍后再试") from exc

    return {"ok": True, "user": to_json(row)}


@router.put("/api/users/me/avatar")
async def update_avatar(body: AvatarIn, user: dict = Depends(current_user)) -> dict:
    """换头像。单独一个接口，不并进 PUT /api/users/me：

    「我的信息」是改一个字段就整份覆盖提交、还带防抖，头像混进去会被反复重传，
    表单里任一字段校验不过也会连头像一起保存失败。两者触发时机和失败语义都不同。
    """
    try:
        async with pool.connection() as conn:
            row = await (
                await conn.execute(
                    f"""
                    UPDATE users SET avatar_key = %s WHERE id = %s
                    RETURNING {USER_COLUMNS}
                    """,
                    (body.avatar_key, user["id"]),
                )
            ).fetchone()
    except psycopg.Error as exc:
        print(f"[avatar update failed] user_id={user['id']} {exc}")
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "头像保存失败，请稍后再试") from exc

    # 旧头像的对象没有删，留在桶里。删除要处理「删到一半失败」和「回滚后旧图已没了」，
    # 本期先不做，靠后续的桶生命周期规则清理孤儿对象
    print(f"[avatar updated] user_id={user['id']} key={body.avatar_key}")
    return {"ok": True, "user": to_json(row)}

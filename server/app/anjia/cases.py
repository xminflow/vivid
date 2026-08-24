"""安家立业首页内容流的小程序侧接口：发图、发布、看流、看详情。

后台的审核接口在 app/anjia/cases_admin.py，它从这里取状态常量与出参函数。
两边分文件而不是像企业认证那样把数据逻辑单独抽一层，是因为案例没有那种双向
依赖——用户信息接口要带认证状态、认证审核接口又要按用户查人，那才需要中间层。

模型上最要紧的一点：案例是**一行会流转状态的实体**，不是一次提交的记录。
它与隔壁 company_certifications 的记录式设计刻意相反，理由见
docs/adr/0004-案例是状态实体而非提交记录.md。
"""

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from psycopg.types.json import Json

from .. import cos
from ..snowflake import next_id
from . import certifications
from .db import get_pool
from .models import CaseIn
from .users import current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/anjia", tags=["anjia-cases"])

# 与 schema.anjia.sql 的 CHECK 约束逐字一致
PENDING = "pending"
PUBLISHED = "published"
REJECTED = "rejected"
DELISTED = "delisted"

STATUS_LABELS = {
    PENDING: "待审核",
    PUBLISHED: "已发布",
    REJECTED: "已驳回",
    DELISTED: "已下架",
}

# 案例图在桶里的场景名。落 static/ 前缀（公开可读、地址稳定、能命中微信的图片
# 缓存），不落 uploads/——后者一律现签，签名 URL 每次都变，缓存全部落空
# （见 app/cos.py 的 SIGN_WINDOW_SECONDS）
SCENE = "anjia-case"

MAX_IMAGES = 9
# 比 cos.MAX_UPLOAD_BYTES（10MB）小一半：小程序端会先 compressImage，而一条案例
# 最多九张，按 10MB 放行等于允许单条案例往服务端推 90MB
MAX_IMAGE_BYTES = 5 * 1024 * 1024

# 案例图的键长这样：static/anjia-case/20260820/<32位hex>_1200x1600.jpg
#
# 宽高焊在键里，是这条链路的关键一环。发布接口收到的是一串键，而**宽高必须可信**
# ——双列瀑布流要在图片加载之前就知道封面多高，客户端自报的话谁都能报个 1×9999
# 把自己的卡片撑成一整列。键由服务端在上传时生成（cos.build_static_key 的 suffix
# 参数），客户端改动其中任何一个字符，它就指向一个不存在的对象、图直接裂掉——
# 于是「这个键真的存在」本身就担保了宽高没被改过，不必再存一张待发布图片的中间表。
#
# 顺带这条正则也挡住了「拿别人的键」和「拿运营的首页图键」：前缀写死在这里。
IMAGE_KEY_RE = re.compile(
    r"^static/" + SCENE + r"/\d{8}/[0-9a-f]{32}_(\d{1,5})x(\d{1,5})\.(jpg|jpeg|png|webp)$"
)

# 游标分页的时间基准。不用 dt.timestamp() 去算微秒：那是浮点运算，边界上那一行
# 可能因为 1 微秒的漂移被跳过或重复
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

DEFAULT_FEED_SIZE = 20
MAX_FEED_SIZE = 50

# 列表与详情共用一套列。作者信息一起 JOIN 出来，不让前端再发一次请求去补——
# 一屏十二张卡就是十二次往返
FEED_COLUMNS = """
    c.id, c.user_id, c.title, c.body, c.images, c.status, c.views,
    c.published_at, c.created_at,
    u.nickname, u.avatar_url,
    COALESCE(cc.company_name, '') AS company_name
"""

# LEFT JOIN 而不是 JOIN：认证被撤销后 company_certifications 里那行变成 revoked，
# 而他**已发布的案例照旧挂在首页**（撤销针对的是账号资格，不是已过审的内容）。
# 用 JOIN 的话这些案例会在撤销的瞬间集体从首页消失，且没有任何一处报错。
# 「一个账号最多一条 approved」由那张表上的部分唯一索引保证，不会 JOIN 出多行
FEED_TABLE = """
    cases c
    JOIN users u ON u.id = c.user_id
    LEFT JOIN company_certifications cc
           ON cc.user_id = c.user_id AND cc.status = 'approved'
"""


def image_key_size(key: str) -> tuple[int, int] | None:
    """从对象键里读回宽高。对不上格式的键返回 None。"""
    m = IMAGE_KEY_RE.match(key)
    if m is None:
        return None
    return int(m.group(1)), int(m.group(2))


def image_json(item: dict) -> dict:
    """一张案例图出接口的样子。宽高照原样给，前端拿它算瀑布流的占位高度。"""
    return {
        "url": cos.object_url(item["key"]) if cos.configured() else None,
        "w": item["w"],
        "h": item["h"],
    }


def to_json(row: dict) -> dict:
    """一条案例出小程序接口的样子。

    正文与作者信息一起给：列表页只用得到封面和标题，但两者从同一条查询里出，
    省掉进详情时的一次往返，而一条案例的正文上限只有 1000 字。
    """
    images = [image_json(item) for item in row["images"]]
    return {
        # 雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，出接口一律转字符串
        "id": str(row["id"]),
        "title": row["title"],
        "body": row["body"],
        "images": images,
        # 第一张兼作封面。瀑布流在图片加载前就靠它的宽高占位
        "cover": images[0] if images else None,
        "views": row["views"],
        "publishedAt": row["published_at"].isoformat() if row["published_at"] else None,
        "author": {
            "id": str(row["user_id"]),
            # 展示名取生效认证的公司全称，没有（认证已被撤销）就落回微信昵称。
            # 与 app/anjia/certifications.py 的 identity() 同一条规则
            "name": row["company_name"] or row["nickname"] or "未命名账号",
            "avatarUrl": row["avatar_url"],
        },
    }


def encode_cursor(row: dict) -> str:
    """把一行的位置编成游标。

    不用 offset 分页：内容流一直有新条目插到最前，翻第二页时会重复或漏条。
    格式是「微秒_ID」，纯数字与下划线，不必再做 URL 编码；对前端是不透明串。
    """
    micros = (row["published_at"] - EPOCH) // timedelta(microseconds=1)
    return f"{micros}_{row['id']}"


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        micros, _, case_id = cursor.partition("_")
        return EPOCH + timedelta(microseconds=int(micros)), int(case_id)
    except ValueError:
        # 游标是我们自己发出去的，传错的只可能是被手改过或版本对不上。不静默当成
        # 第一页——那会让用户在翻到一半时莫名其妙回到顶部，且没有任何提示
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "翻页参数不正确") from None


async def company_user(user: dict = Depends(current_user)) -> dict:
    """要求当前用户是企业认证账号。首页内容流的发布权与上传权都挂这一道闸。

    上传接口同样挂它，不是多此一举：图先落桶、发布时才校验身份的话，任何一个
    登录用户都能往公开可读的 static/ 前缀里塞东西。
    """
    async with get_pool().connection() as conn:
        live = await certifications.approved(conn, user["id"])
    if live is None:
        # 403 不是 401：他登录了，只是还不够格。小程序据此弹「去认证」而不是重新登录
        raise HTTPException(status.HTTP_403_FORBIDDEN, "首页内容流仅限企业认证账号发布")
    return user


async def _read_image_within_limit(request: Request) -> bytes:
    """边读边数，超限立刻停。

    不用 `await request.body()` 一次读完再判断：那样上限是「先把整个请求收进内存，
    再告诉对方太大了」，等于把内存交给调用方决定。

    与 app/admin.py 的同名函数是两份而不是一份：这里的上限只有那边的一半（见
    MAX_IMAGE_BYTES），而且 app/anjia/ 一贯不 import 安东尼之家的业务模块
    （docs/adr/0001 定的「新代码按目标形态长在 app/anjia/ 下」）。
    """
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMAGE_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"单张图片不能超过 {MAX_IMAGE_BYTES // 1024 // 1024}MB",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/cases/images")
async def upload_case_image(request: Request, user: dict = Depends(company_user)) -> dict:
    """收一张案例图，落 COS，返回可直接展示的地址与宽高。

    走**服务端中转**而不是 /api/upload-url 那种预签名直传，有两个直传给不了的东西：
    宽高要由服务端亲自读出来（客户端自报不可信），以及落桶的字节必须先被确认是
    图片——static/ 前缀是公开可读的，签个 PUT 地址给客户端等于在公开目录上开一个
    可写位，谁都能往里塞任意文件。带宽代价可接受：小程序端压缩后单张约 300KB~1MB。

    请求体就是**裸的图片字节**，不是 multipart：解析 multipart 要装 python-multipart，
    一次一张图，裸 body 已经够用，不值得为此多一个依赖（同 app/admin.py 的上传口）。

    只落桶、不写库：作者选完九张图再点发布，中途退出去不该在库里留下半条案例。
    没被引用的对象按 static/anjia-case/ 前缀由运维对账，与首页配图的取舍一致。
    """
    data = await _read_image_within_limit(request)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有收到图片内容")

    # 按文件头判类型，不信前端给的文件名和 Content-Type
    ext = cos.sniff_image_ext(data)
    if ext is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只支持 JPG / PNG / WebP 格式的图片")

    size = cos.image_size(data)
    if size is None:
        # 认得出格式却读不出宽高，说明文件头是坏的。收下去只会在首页上塌出一个洞
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "这张图片已损坏，请换一张")
    width, height = size

    key = cos.build_static_key(SCENE, ext, suffix=f"{width}x{height}")
    try:
        await cos.put_object(key, data, cos.CONTENT_TYPES[ext])
    except cos.CosNotConfigured:
        logger.exception("[anjia] COS 未配置，案例图传不上去")
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "图片服务未配置，请联系我们"
        ) from None
    except cos.CosUploadFailed as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from None

    logger.info("[anjia] 案例图已上传 key=%s 字节数=%d 用户=%s", key, len(data), user["id"])
    return {"ok": True, "key": key, "url": cos.object_url(key), "w": width, "h": height}


@router.post("/cases")
async def create_case(body: CaseIn, user: dict = Depends(company_user)) -> dict:
    """发布一条案例。进的是待审队列，不是直接上首页。

    人工先审后发是需求文档 2.3.1 对首页 UGC 的硬性要求，也是「企业认证从简、
    核验宽松」的配套——两者是一对，去掉任一个另一个就不成立（需求文档 4.3.2）。
    """
    images: list[dict[str, Any]] = []
    for key in body.images:
        size = image_key_size(key)
        if size is None:
            # 键是我们自己发出去的。对不上只有两种可能：被手改过，或者前端把别处的
            # 地址塞了进来。两种都不该只丢弃那一张，而要整条拒收
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "图片无效，请重新上传")
        images.append({"key": key, "w": size[0], "h": size[1]})

    try:
        async with get_pool().connection() as conn:
            row = await (
                await conn.execute(
                    """
                    INSERT INTO cases (id, user_id, title, body, images, status)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id, status, created_at
                    """,
                    (next_id(), user["id"], body.title, body.body, Json(images), PENDING),
                )
            ).fetchone()
    except psycopg.Error as exc:
        logger.exception("[anjia] 发布案例失败：用户=%s", user["id"])
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "发布失败，请稍后再试"
        ) from exc

    logger.info("[anjia] 案例已提交：id=%s 用户=%s 图=%d张", row["id"], user["id"], len(images))
    return {
        "ok": True,
        "case": {
            "id": str(row["id"]),
            "status": row["status"],
            "statusLabel": STATUS_LABELS[row["status"]],
            "createdAt": row["created_at"].isoformat(),
        },
    }


@router.get("/cases")
async def list_cases(
    cursor: Annotated[str, Query(max_length=40)] = "",
    size: Annotated[int, Query(ge=1, le=MAX_FEED_SIZE)] = DEFAULT_FEED_SIZE,
    _: dict = Depends(current_user),
) -> dict:
    """首页内容流。只出已发布且未删除的，按发布时间倒序。

    ⚠️ **不按 views 排**。这与需求文档 AJ-02 写的「按浏览量等热度指标排序」不一致，
    是有意偏离：冷启动阶段总共十几条案例，热度排序的结果基本等于随机，还会让新
    发布的内容永远沉底。views 照常在记，它是二期真做热度排序时唯一的历史基准
    （见 CONTEXT.md「浏览量」）。要改回去只是换一行 ORDER BY。

    行值比较 `(published_at, id) < (%s, %s)` 正好走 cases_feed_idx 那个部分索引，
    扫到的行数等于要出的行数。
    """
    conditions = ["c.status = %s", "c.deleted_at IS NULL"]
    params: list[Any] = [PUBLISHED]
    if cursor:
        published_at, case_id = decode_cursor(cursor)
        conditions.append("(c.published_at, c.id) < (%s, %s)")
        params += [published_at, case_id]

    try:
        async with get_pool().connection() as conn:
            rows = await (
                await conn.execute(
                    f"""
                    SELECT {FEED_COLUMNS}
                      FROM {FEED_TABLE}
                     WHERE {" AND ".join(conditions)}
                     ORDER BY c.published_at DESC, c.id DESC
                     LIMIT %s
                    """,
                    [*params, size],
                )
            ).fetchall()
    except psycopg.Error as exc:
        logger.exception("[anjia] 首页内容流查询失败 cursor=%s", cursor)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "加载失败，请稍后再试"
        ) from exc

    # 取满了才给下一页游标。不足一页说明到底了——不靠前端再发一次空请求去确认
    next_cursor = encode_cursor(rows[-1]) if len(rows) == size else None
    return {"ok": True, "items": [to_json(row) for row in rows], "nextCursor": next_cursor}


@router.get("/cases/{case_id}")
async def read_case(case_id: int, user: dict = Depends(current_user)) -> dict:
    """案例详情，顺带记一次浏览。

    只出已发布的：待审、被驳回、被下架、已删除的案例，拿着 ID 直接访问同样看不到。
    作者要看自己那条未过审的，走「我的发布」（第二刀）。
    """
    try:
        async with get_pool().connection() as conn:
            await _count_view(conn, case_id, user["id"])
            row = await (
                await conn.execute(
                    f"""
                    SELECT {FEED_COLUMNS}
                      FROM {FEED_TABLE}
                     WHERE c.id = %s AND c.status = %s AND c.deleted_at IS NULL
                    """,
                    (case_id, PUBLISHED),
                )
            ).fetchone()
    except psycopg.Error as exc:
        logger.exception("[anjia] 案例详情查询失败：id=%s", case_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, "加载失败，请稍后再试"
        ) from exc

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条案例已经不在了")
    return {"ok": True, "case": to_json(row)}


async def _count_view(conn: psycopg.AsyncConnection, case_id: int, user_id: int) -> None:
    """记一次浏览。同一个人对同一条只算一次，作者看自己的不算。

    去重是有意为之：views 的语义是「多少**人**看过」，二期拿它排「哪条更值得推」，
    本质是触达了多少人。不去重的话，在只有十几个企业账号的冷启动阶段，作者自己
    每天点开看一眼就能刷榜（见 CONTEXT.md「浏览量」）。

    整件事是一条 SQL：明细插不进去（这个人看过了）就不加计数，两句放在同一个 CTE
    里，并发下不会重复加。INSERT 用 SELECT ... FROM cases 而不是 VALUES，是为了把
    「这条案例已发布、且不是自己发的」也塞进同一次往返——条件不成立时一行都不插。

    计数失败不影响正文，所以这里吞掉异常只记日志：没人会因为一个统计数字没加上
    而看不成内容。
    """
    try:
        await conn.execute(
            """
            WITH seen AS (
                INSERT INTO case_views (case_id, user_id)
                SELECT %s, %s
                  FROM cases
                 WHERE id = %s AND status = %s AND deleted_at IS NULL
                   AND user_id <> %s
                ON CONFLICT DO NOTHING
                RETURNING case_id
            )
            UPDATE cases
               SET views = views + 1
             WHERE id = %s AND EXISTS (SELECT 1 FROM seen)
            """,
            (case_id, user_id, case_id, PUBLISHED, user_id, case_id),
        )
    except psycopg.Error:
        logger.warning("[anjia] 浏览计数失败：case=%s user=%s", case_id, user_id)

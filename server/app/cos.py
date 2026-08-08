"""COS 预签名与对象读写。

小程序不持有密钥——包能被反编译，永久密钥一旦打进客户端，等于把桶的读写删
权限公开。这里由服务端用密钥签出一个短时效、只针对单个对象键、只允许单个
动作的 URL，小程序拿着它直传 COS。密钥只存在于服务端的 .env。

后台（浏览器）不走直传，字节经服务端中转（见 put_object），理由在那个函数上。

签名算法是腾讯云 COS 的 Signature v5，文档见
https://cloud.tencent.com/document/product/436/7778
没引 SDK，因为只用到一个签名函数，标准库的 hmac + hashlib 就够。
"""

import hashlib
import hmac
import logging
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SECRET_ID = os.getenv("COS_SECRET_ID", "")
SECRET_KEY = os.getenv("COS_SECRET_KEY", "")
BUCKET = os.getenv("COS_BUCKET", "")
REGION = os.getenv("COS_REGION", "ap-shanghai")

# 上传地址的有效期。给够用户选完图到传完的时间，又不至于泄漏后能长期滥用
PUT_EXPIRE_SECONDS = 15 * 60
GET_EXPIRE_SECONDS = 60 * 60

# 头像地址签得比后台看图长得多：小程序把资料快照存在本机，下次冷启动先用快照渲染，
# 签一小时的话隔天进来第一眼就是一张拉不出来的图
AVATAR_EXPIRE_SECONDS = 7 * 24 * 60 * 60

# 看图地址的签名时间窗对齐长度。
#
# 签名里带 q-sign-time，不对齐的话每次签出来的 URL 都不一样——同一张头像，
# 每进一次「我的」就是一个全新地址：微信的图片缓存必然落空，必然重新下载一次，
# 下载完成前头像位置是空的，用户看到的就是「头像变回了会员名首字」。小程序端
# 还拿「地址变没变」来决定要不要重置加载状态，地址每次都变会让它每次都重置。
#
# 所以把签名的起点对齐到固定长度的时间窗：同一个对象键在同一个窗口内，签出的
# URL 逐字节相同。代价是有效期从窗口起点算起，最坏情况少掉一个窗口的长度
# （头像 7 天签期、1 天窗口，最坏仍有 6 天），换来的是地址稳定、能命中缓存。
SIGN_WINDOW_SECONDS = 24 * 60 * 60

# 只收这几种图片。扩展名是白名单，不是从用户传的文件名里直接拼的
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "heic"}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 桶里的两个前缀，语义不同，见 build_key / build_static_key 的说明
UPLOADS_PREFIX = "uploads"
STATIC_PREFIX = "static"

# 落桶时写的 Content-Type。不写的话 COS 存成 application/octet-stream，
# 浏览器和小程序拿到的是「下载」而不是「显示」
CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "heic": "image/heic",
}


class CosNotConfigured(RuntimeError):
    pass


class CosUploadFailed(RuntimeError):
    """字节没能写进桶。区别于配置缺失：这个要看日志里 COS 回的 <Code>。"""


def configured() -> bool:
    return bool(SECRET_ID and SECRET_KEY and BUCKET and REGION)


def _require_config() -> None:
    if not configured():
        raise CosNotConfigured(
            "缺少 COS 配置：在 server/.env 里填 COS_SECRET_ID / COS_SECRET_KEY / COS_BUCKET / COS_REGION"
        )


def host() -> str:
    return f"{BUCKET}.cos.{REGION}.myqcloud.com"


def object_url(key: str) -> str:
    return f"https://{host()}/{quote(key.lstrip('/'))}"


def _build_key(prefix: str, scene: str, ext: str) -> str:
    """对象键由服务端生成，不用客户端传来的文件名。

    客户端文件名可能带路径分隔符、中文、超长串，直接当键会有目录穿越和编码问题。
    这里只保留场景前缀和白名单后缀，主体用 uuid，键一定是安全的 ASCII。
    """
    ext = ext.lower().lstrip(".")
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"不支持的图片格式：{ext}")

    scene = "".join(c for c in scene if c.isalnum() or c in "-_") or "misc"
    day = time.strftime("%Y%m%d", time.localtime())
    return f"{prefix}/{scene}/{day}/{uuid.uuid4().hex}.{ext}"


def build_key(scene: str, ext: str) -> str:
    """用户上传图的对象键。私有语义：地址一律现签，见 presign_get。"""
    return _build_key(UPLOADS_PREFIX, scene, ext)


def build_static_key(scene: str, ext: str) -> str:
    """运营素材的对象键（后台配的首页图走这条）。

    与 build_key 只差一个前缀，但语义完全不同：static/ 下的对象要能被任何人直接
    加载——首页图是给所有访客看的，每次都现签既没意义（内容本来就公开），
    还会让地址一直变、微信的图片缓存全部落空。

    ⚠️ 依赖桶对 static/ 前缀公开可读。当前开发桶整体是「公有读私有写」所以能用；
    README「上线前要做的」里把桶收紧成私有读时，static/ 必须单独设公有读 ACL，
    否则首页图全挂。
    """
    return _build_key(STATIC_PREFIX, scene, ext)


def sniff_image_ext(data: bytes) -> str | None:
    """按文件头判断图片类型，返回后缀；认不出来返回 None。

    不信客户端给的文件名或 Content-Type：后台上传是「一段字节 + 一个后缀」，
    照着后缀存的话，任何文件改个名就能被当成图片放上首页。
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    # WEBP 是 RIFF 容器：前 4 字节 RIFF，第 8-12 字节是 WEBP，中间 4 字节是长度
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


async def put_object(key: str, data: bytes, content_type: str) -> None:
    """服务端把字节推到 COS。

    小程序端是客户端直传（省服务端带宽，见 presign_put）；后台走浏览器，
    直传要给桶单独配 CORS 规则，多一处线上配置就多一处会漏配的地方，
    所以后台这条链路由服务端中转——顺便还能在落桶之前校验类型和大小。
    """
    _require_config()

    url = presign_put(key)
    async with httpx.AsyncClient(timeout=30) as client:
        res = await client.put(url, content=data, headers={"Content-Type": content_type})

    if res.status_code != 200:
        # COS 拒绝时正文里有 <Code>/<Message>，说明是签名问题还是权限问题。
        # 不打出来的话只剩一个状态码，线上没法定位
        logger.error("COS 拒绝了 PUT key=%s status=%s body=%s", key, res.status_code, res.text)
        raise CosUploadFailed(f"图片写入存储失败（{res.status_code}）")


def _sign(method: str, key: str, expire_seconds: int, align_seconds: int = 0) -> str:
    """按 Signature v5 算出查询串。

    不把任何 header 和 query 纳入签名（q-header-list / q-url-param-list 都为空），
    这样客户端发什么 Content-Type 都不影响校验，小程序端少一个出错点。

    align_seconds 大于 0 时，签名起点对齐到该长度的时间窗，同一个键在同一窗口内
    签出的结果完全一致（见 SIGN_WINDOW_SECONDS）。写类操作不对齐：它们签期很短，
    对齐会让贴着窗口末尾拿到的地址几乎立刻过期。
    """
    _require_config()

    now = int(time.time())
    if align_seconds > 0:
        start = now // align_seconds * align_seconds
    else:
        start = now
    start -= 60  # 往前留一分钟，容忍客户端与服务端的时钟差

    # 对齐时必须把整个窗口也算进有效期：起点是窗口开头，而请求可能发生在窗口的
    # 任何位置，只加 expire_seconds 的话，签期短于窗口时地址一出生就是过期的
    # ——后台看图签 1 小时、窗口 24 小时，一天里 23 小时签出来的全是 403。
    # 加上窗口长度后，无论在窗口何处请求，剩余有效期都不少于 expire_seconds。
    # 代价是地址泄漏后最长可用 align_seconds + expire_seconds，这是换缓存命中的固有成本
    end = start + align_seconds + expire_seconds
    key_time = f"{start};{end}"

    sign_key = hmac.new(SECRET_KEY.encode(), key_time.encode(), hashlib.sha1).hexdigest()

    uri = "/" + quote(key.lstrip("/"))
    http_string = f"{method.lower()}\n{uri}\n\n\n"
    string_to_sign = "sha1\n{}\n{}\n".format(
        key_time, hashlib.sha1(http_string.encode()).hexdigest()
    )
    signature = hmac.new(sign_key.encode(), string_to_sign.encode(), hashlib.sha1).hexdigest()

    return "&".join(
        [
            "q-sign-algorithm=sha1",
            f"q-ak={SECRET_ID}",
            f"q-sign-time={key_time}",
            f"q-key-time={key_time}",
            "q-header-list=",
            "q-url-param-list=",
            f"q-signature={signature}",
        ]
    )


def presign_put(key: str) -> str:
    return f"{object_url(key)}?{_sign('put', key, PUT_EXPIRE_SECONDS)}"


def presign_get(key: str, expire_seconds: int = GET_EXPIRE_SECONDS) -> str:
    """看图用带签名的临时地址。同一个键在一个签名窗口内返回的地址是稳定的。

    注意：当前这个开发桶的权限实际是「公有读私有写」（对象拿 URL 就能直接下，
    只是不能列举），签名在读这一侧其实没有拦截作用。这里仍然一律签名，
    是为了桶权限收紧成私有读之后不用再改代码——见 README「上线前要做的」。
    """
    return f"{object_url(key)}?{_sign('get', key, expire_seconds, SIGN_WINDOW_SECONDS)}"


def presign_delete(key: str) -> str:
    return f"{object_url(key)}?{_sign('delete', key, PUT_EXPIRE_SECONDS)}"

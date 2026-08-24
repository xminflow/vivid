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


def _build_key(prefix: str, scene: str, ext: str, suffix: str = "") -> str:
    """对象键由服务端生成，不用客户端传来的文件名。

    客户端文件名可能带路径分隔符、中文、超长串，直接当键会有目录穿越和编码问题。
    这里只保留场景前缀和白名单后缀，主体用 uuid，键一定是安全的 ASCII。

    suffix 会以 `_` 接在 uuid 之后，同样只保留字母数字与 `-_`。它存在的理由是让
    调用方能把一小段**不可篡改的元信息**焊进键里：键由服务端生成，客户端改一个
    字符就指向一个不存在的对象，于是「键存在」本身就担保了这段元信息没被改过。
    安家立业的案例图用它带宽高（见 app/anjia/cases.py 的 image_key_size）。
    """
    ext = ext.lower().lstrip(".")
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"不支持的图片格式：{ext}")

    scene = "".join(c for c in scene if c.isalnum() or c in "-_") or "misc"
    suffix = "".join(c for c in suffix if c.isalnum() or c in "-_")
    day = time.strftime("%Y%m%d", time.localtime())
    stem = f"{uuid.uuid4().hex}_{suffix}" if suffix else uuid.uuid4().hex
    return f"{prefix}/{scene}/{day}/{stem}.{ext}"


def build_key(scene: str, ext: str) -> str:
    """用户上传图的对象键。私有语义：地址一律现签，见 presign_get。"""
    return _build_key(UPLOADS_PREFIX, scene, ext)


def build_static_key(scene: str, ext: str, suffix: str = "") -> str:
    """运营素材的对象键（后台配的首页图走这条）。

    与 build_key 只差一个前缀，但语义完全不同：static/ 下的对象要能被任何人直接
    加载——首页图是给所有访客看的，每次都现签既没意义（内容本来就公开），
    还会让地址一直变、微信的图片缓存全部落空。

    ⚠️ 依赖桶对 static/ 前缀公开可读。当前开发桶整体是「公有读私有写」所以能用；
    README「上线前要做的」里把桶收紧成私有读时，static/ 必须单独设公有读 ACL，
    否则首页图全挂。
    """
    return _build_key(STATIC_PREFIX, scene, ext, suffix)


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


def image_size(data: bytes) -> tuple[int, int] | None:
    """从图片的文件头读出 (宽, 高)；读不出来返回 None。

    为什么手写而不是引 Pillow：只用得到这一个函数，而 Pillow 带 C 扩展、带一串
    解码器的历史 CVE，为了两个整数把它拉进依赖不划算。仓库里 Signature v5 也是
    同样的取舍手写的（见本文件开头）。

    为什么服务端非算不可：双列瀑布流必须在图片**加载之前**就知道封面多高，否则
    页面先塌成空白再被内容顶开。这个值若由小程序自报，任何人都能报一个 1×9999
    把自己的卡片撑成一整列。

    只认 sniff_image_ext 能认出的那三种。三种格式的头都在前几十个字节里，
    调用方传整段字节进来即可，不需要流式解析。
    """
    ext = sniff_image_ext(data)
    if ext == "png":
        # IHDR 紧跟在 8 字节签名之后：4 字节长度 + 4 字节 "IHDR" + 宽高各 4 字节大端
        if len(data) < 24:
            return None
        return (
            int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"),
        )

    if ext == "jpg":
        return _jpeg_size(data)

    if ext == "webp":
        return _webp_size(data)

    return None


# JPEG 里带宽高的段。SOF0/1/2… 都算，但 C4（哈夫曼表）、C8（保留）、CC（算术编码
# 表）这三个是同区间里的冒牌货，长得像 SOF 却不是
_JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """顺着段链走到第一个 SOF 段。

    宽高**不在**文件头的固定偏移上：JPEG 是一串变长的段，EXIF、色彩配置、缩略图
    都可能排在前面，手机拍的图尤其如此。所以只能一段一段跳过去。
    """
    i = 2  # 跳过 SOI（FFD8）
    n = len(data)
    while i + 3 < n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        # 段之间允许有任意多个 FF 作填充，逐个跳过
        if marker == 0xFF:
            i += 1
            continue
        # 这几个是独立标记，没有长度字段，跳 2 个字节就是下一段
        if 0xD0 <= marker <= 0xD9 or marker == 0x01:
            i += 2
            continue
        length = int.from_bytes(data[i + 2:i + 4], "big")
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            # 段内布局：长度(2) + 精度(1) + 高(2) + 宽(2)。注意**高在前**
            if i + 9 > n:
                return None
            return (
                int.from_bytes(data[i + 7:i + 9], "big"),
                int.from_bytes(data[i + 5:i + 7], "big"),
            )
        i += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """WebP 有三种子格式，宽高的存法各不相同。

    小程序 wx.compressImage 出的是 jpg，这条路实际很少走到；但后台和第三方工具
    传 webp 是合法的，认不出来就只能拒收，不如把三种都读了。
    """
    if len(data) < 30:
        return None
    fmt = data[12:16]

    if fmt == b"VP8 ":  # 有损：同步码 9D 01 2A 之后是宽高，各 2 字节小端，低 14 位有效
        if data[23:26] != bytes((0x9D, 0x01, 0x2A)):
            return None
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )

    if fmt == b"VP8L":  # 无损：签名 2F 之后 4 字节里，宽高各占 14 位，且都是「实际值 - 1」
        if data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)

    if fmt == b"VP8X":  # 扩展（带动画/透明通道）：画布宽高各 3 字节小端，同样是「实际值 - 1」
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )

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

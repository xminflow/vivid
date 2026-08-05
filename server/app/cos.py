"""COS 预签名。

小程序不持有密钥——包能被反编译，永久密钥一旦打进客户端，等于把桶的读写删
权限公开。这里由服务端用密钥签出一个短时效、只针对单个对象键、只允许单个
动作的 URL，小程序拿着它直传 COS。密钥只存在于服务端的 .env。

签名算法是腾讯云 COS 的 Signature v5，文档见
https://cloud.tencent.com/document/product/436/7778
没引 SDK，因为只用到一个签名函数，标准库的 hmac + hashlib 就够。
"""

import hashlib
import hmac
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv

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

# 只收这几种图片。扩展名是白名单，不是从用户传的文件名里直接拼的
ALLOWED_EXTS = {"jpg", "jpeg", "png", "webp", "heic"}

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class CosNotConfigured(RuntimeError):
    pass


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


def build_key(scene: str, ext: str) -> str:
    """对象键由服务端生成，不用客户端传来的文件名。

    客户端文件名可能带路径分隔符、中文、超长串，直接当键会有目录穿越和编码问题。
    这里只保留场景前缀和白名单后缀，主体用 uuid，键一定是安全的 ASCII。
    """
    ext = ext.lower().lstrip(".")
    if ext not in ALLOWED_EXTS:
        raise ValueError(f"不支持的图片格式：{ext}")

    scene = "".join(c for c in scene if c.isalnum() or c in "-_") or "misc"
    day = time.strftime("%Y%m%d", time.localtime())
    return f"uploads/{scene}/{day}/{uuid.uuid4().hex}.{ext}"


def _sign(method: str, key: str, expire_seconds: int) -> str:
    """按 Signature v5 算出查询串。

    不把任何 header 和 query 纳入签名（q-header-list / q-url-param-list 都为空），
    这样客户端发什么 Content-Type 都不影响校验，小程序端少一个出错点。
    """
    _require_config()

    start = int(time.time()) - 60  # 往前留一分钟，容忍客户端与服务端的时钟差
    end = int(time.time()) + expire_seconds
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
    """看图用带签名的临时地址。

    注意：当前这个开发桶的权限实际是「公有读私有写」（对象拿 URL 就能直接下，
    只是不能列举），签名在读这一侧其实没有拦截作用。这里仍然一律签名，
    是为了桶权限收紧成私有读之后不用再改代码——见 README「上线前要做的」。
    """
    return f"{object_url(key)}?{_sign('get', key, expire_seconds)}"


def presign_delete(key: str) -> str:
    return f"{object_url(key)}?{_sign('delete', key, PUT_EXPIRE_SECONDS)}"

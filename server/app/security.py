"""密码哈希。

用标准库的 scrypt，不引 bcrypt / passlib：这个项目为了少一个依赖，连后台传图
都没装 python-multipart（见 admin.py 的上传接口）。scrypt 是标准库里现成的、
抗 GPU 爆破的密码哈希函数，够用。

存储格式自带参数：

    scrypt$<n>$<r>$<p>$<salt-base64>$<hash-base64>

参数写进串里而不是写死在代码里，是为了将来调大 n 时存量密码仍按各自记录里的
参数校验——否则调参那天所有人都得重设密码。
"""

import base64
import hashlib
import hmac
import secrets

# CPU/内存开销。n=16384, r=8 要 128*n*r = 16MB 内存，算一次几十毫秒：
# 登录接口等得起，爆破的人则要为每个候选密码付同样的代价。
# 注意别把 n 调到需要超过 32MB，那是 OpenSSL 的默认上限，会直接抛错
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 64

# 密码长度。下限 8 位挡住生日和 123456；不强制大小写数字符号组合——
# 那类规则实际上会把人逼去把密码写在便签上
PASSWORD_MIN_LEN = 8
PASSWORD_MAX_LEN = 64


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=KEY_BYTES,
    )
    return "$".join(
        [
            "scrypt",
            str(SCRYPT_N),
            str(SCRYPT_R),
            str(SCRYPT_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(key).decode(),
        ]
    )


def verify_password(password: str, stored: str) -> bool:
    """校验密码。

    解析不了的 stored 一律当「密码不对」返回 False，不往外抛：库里万一混进一条
    格式不对的哈希，登录接口该回「用户名或密码不正确」，不该 500 给对方看。
    """
    try:
        scheme, n, r, p, salt_b64, key_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(key_b64, validate=True)
        actual = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False

    # 常量时间比对：普通的 == 会在第一个不同的字节上返回，
    # 逐字节的耗时差能被用来一位一位地猜出哈希
    return hmac.compare_digest(actual, expected)

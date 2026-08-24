"""小程序 access_token 的获取与缓存。

## 这不是支付那一套

发货信息录入走的是**小程序开放接口**（api.weixin.qq.com），认证方式是 access_token；
微信支付走的是商户 API（api.mch.weixin.qq.com），认证方式是商户私钥签名。
两套鉴权体系互不相干，所以分成两个模块（app/wxpay.py 与本文件 + app/wxship.py）。

## 为什么必须缓存

access_token 是**账号级**的：同一个 appid 全局共用一个，有效期 2 小时，
且获取接口有调用频率限制。每次发货都去换一个的话，量一上来就会被限流，
而限流的表现是发货接口整片失败——那正是最不能失败的时候（有 48 小时的时限）。

## 为什么用 stable_token 而不是 token

`/cgi-bin/token` 每次调用都会**让上一个 token 失效**。多进程/多副本时，
两个进程各自去换，就会互相把对方的 token 顶掉，表现为随机的 40001「invalid
credential」。`/cgi-bin/stable_token` 在有效期内返回同一个 token，
天然适合多副本——这台机器现在是单实例，但这条约束不该等到扩容才发现。

## 并发

同一个进程里多个请求同时发现 token 过期时，只让第一个去换，其余等它。
不加锁的话它们会同时打微信的接口，白白消耗频率配额。
"""

import logging
import os
import time
from pathlib import Path

import httpx
from anyio import Lock
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

APPID = os.getenv("WX_APPID", "").strip()
SECRET = os.getenv("WX_SECRET", "").strip()

STABLE_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/stable_token"

# 提前多久换新的。微信给的有效期是 7200 秒，留 5 分钟余量：
# 正好卡在过期那一刻发出的请求会拿到一个已经失效的 token，而那次调用就白费了
_REFRESH_MARGIN_SECONDS = 300

_token: str = ""
_expires_at: float = 0.0
_lock = Lock()


class TokenError(RuntimeError):
    """换 access_token 失败。调用方翻译成明确的错误，不要吞。"""


def configured() -> bool:
    """appid 和 secret 都有才可能换到 token。与支付的配置相互独立。"""
    return bool(APPID and SECRET)


async def _fetch() -> tuple[str, float]:
    """真的去微信换一个。返回 (token, 过期时刻的单调时间)。"""
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            STABLE_TOKEN_URL,
            json={"grant_type": "client_credential", "appid": APPID, "secret": SECRET},
        )
    body = response.json()

    token = body.get("access_token")
    if not token:
        # 不记 secret，只记微信给的错误码与描述。40013 是 appid 不对，
        # 40125 是 secret 不对，45009 是接口调用超限
        logger.error(
            "换 access_token 失败 errcode=%s errmsg=%s",
            body.get("errcode"),
            body.get("errmsg"),
        )
        raise TokenError("获取微信接口凭证失败")

    expires_in = int(body.get("expires_in", 7200))
    logger.info("已换到新的 access_token，有效期 %d 秒", expires_in)
    return token, time.monotonic() + max(expires_in - _REFRESH_MARGIN_SECONDS, 60)


async def get_token(*, force: bool = False) -> str:
    """拿一个可用的 access_token。

    force=True 用于「微信说 token 无效」时强制换一个——那种情况下缓存里的值
    虽然没到期但已经不能用了（比如别处调了 /cgi-bin/token 把它顶掉）。
    """
    global _token, _expires_at

    if not configured():
        raise TokenError("微信接口凭证未配置：WX_APPID / WX_SECRET")

    now = time.monotonic()
    if not force and _token and now < _expires_at:
        return _token

    async with _lock:
        # 拿到锁之后再判一次：等锁期间别人可能已经换好了
        now = time.monotonic()
        if not force and _token and now < _expires_at:
            return _token
        _token, _expires_at = await _fetch()
        return _token


def invalidate() -> None:
    """把缓存作废。微信返回 40001/42001（token 无效或过期）时调用。"""
    global _token, _expires_at
    _token = ""
    _expires_at = 0.0

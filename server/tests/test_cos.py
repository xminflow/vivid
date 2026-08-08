"""COS 预签名。

重点是看图地址的稳定性：小程序端拿「地址变没变」判断要不要重新加载头像，
地址每次都变会导致每进一次页面就重下一次图、期间头像位置是空的。
"""

from urllib.parse import parse_qs, urlparse

import pytest

from app import cos


@pytest.fixture(autouse=True)
def fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """签名不需要真密钥，用固定假值，测试不依赖本机 .env 是否配了 COS。"""
    monkeypatch.setattr(cos, "SECRET_ID", "AKIDtest")
    monkeypatch.setattr(cos, "SECRET_KEY", "secrettest")
    monkeypatch.setattr(cos, "BUCKET", "bucket-1250000000")
    monkeypatch.setattr(cos, "REGION", "ap-shanghai")


def freeze(monkeypatch: pytest.MonkeyPatch, timestamp: int) -> None:
    monkeypatch.setattr(cos.time, "time", lambda: float(timestamp))


KEY = "uploads/avatar/20260805/abc123.jpg"

# 一个对齐到窗口起点的时刻，方便围绕窗口边界取点
WINDOW_START = 1_780_000_000 // cos.SIGN_WINDOW_SECONDS * cos.SIGN_WINDOW_SECONDS


def test_get_url_stable_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """同一窗口内的两次调用必须给出逐字节相同的地址，否则客户端缓存永远落空。"""
    freeze(monkeypatch, WINDOW_START + 10)
    first = cos.presign_get(KEY, cos.AVATAR_EXPIRE_SECONDS)

    freeze(monkeypatch, WINDOW_START + cos.SIGN_WINDOW_SECONDS - 1)
    last = cos.presign_get(KEY, cos.AVATAR_EXPIRE_SECONDS)

    assert first == last


def test_get_url_changes_across_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """跨窗口要换一份新签名，否则签期会随时间耗尽而不续。"""
    freeze(monkeypatch, WINDOW_START + 10)
    before = cos.presign_get(KEY, cos.AVATAR_EXPIRE_SECONDS)

    freeze(monkeypatch, WINDOW_START + cos.SIGN_WINDOW_SECONDS + 10)
    after = cos.presign_get(KEY, cos.AVATAR_EXPIRE_SECONDS)

    assert before != after


def sign_end(url: str) -> int:
    return int(parse_qs(urlparse(url).query)["q-sign-time"][0].split(";")[1])


@pytest.mark.parametrize(
    "expire",
    [cos.AVATAR_EXPIRE_SECONDS, cos.GET_EXPIRE_SECONDS],
    ids=["头像7天", "后台看图1小时"],
)
def test_get_url_keeps_full_validity_anywhere_in_window(
    monkeypatch: pytest.MonkeyPatch, expire: int
) -> None:
    """窗口内任意时刻拿到的地址，剩余有效期都不得少于签期（扣掉 60 秒时钟容差）。

    起点对齐到窗口开头，而请求可能落在窗口的任何位置。签期短于窗口时，
    如果有效期只从起点算 expire_seconds，地址一出生就是过期的——
    后台看图签 1 小时、窗口 24 小时，曾导致一天里 23 小时签出来的全是 403。
    所以这里对两种签期都要验，尤其是短的那个。

    起点还往前挪了 60 秒容忍时钟差，这 60 秒同样从签期里扣。
    """
    for offset in (0, cos.SIGN_WINDOW_SECONDS // 2, cos.SIGN_WINDOW_SECONDS - 1):
        at = WINDOW_START + offset
        freeze(monkeypatch, at)
        assert sign_end(cos.presign_get(KEY, expire)) - at >= expire - 60


def test_put_url_not_aligned(monkeypatch: pytest.MonkeyPatch) -> None:
    """上传地址签期只有 15 分钟，不能对齐——否则窗口末尾签出来的已经过期了。"""
    freeze(monkeypatch, WINDOW_START + 10)
    first = cos.presign_put(KEY)

    freeze(monkeypatch, WINDOW_START + 20)
    second = cos.presign_put(KEY)

    assert first != second


def test_put_url_validity_measured_from_now(monkeypatch: pytest.MonkeyPatch) -> None:
    at = WINDOW_START + cos.SIGN_WINDOW_SECONDS - 1
    freeze(monkeypatch, at)

    assert sign_end(cos.presign_put(KEY)) - at >= cos.PUT_EXPIRE_SECONDS - 60


def test_signature_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """签名串该有的字段一个不少，且 key_time 与 sign_time 一致（v5 要求）。"""
    freeze(monkeypatch, WINDOW_START + 10)
    params = parse_qs(urlparse(cos.presign_get(KEY)).query, keep_blank_values=True)

    assert params["q-sign-algorithm"] == ["sha1"]
    assert params["q-ak"] == ["AKIDtest"]
    assert params["q-sign-time"] == params["q-key-time"]
    assert params["q-header-list"] == [""]
    assert params["q-url-param-list"] == [""]
    assert len(params["q-signature"][0]) == 40

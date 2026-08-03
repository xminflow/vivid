"""微信登录：拿小程序的 code 换 openid。

文档：https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-login/code2Session.html
appid / secret 在小程序后台「开发管理 - 开发设置」里，放 .env，不进版本库。
"""

import os

import httpx

CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


class WeChatError(Exception):
    """微信侧返回的错误。message 是可以直接弹给用户的中文。"""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(detail or message)
        self.message = message
        self.detail = detail


async def code2session(code: str) -> dict:
    """返回 {'openid': ..., 'unionid': ... | None, 'session_key': ...}。"""
    # 在函数里读，不在导入时读：.env 是 db.py 导入时才加载的
    appid = os.getenv("WX_APPID", "")
    secret = os.getenv("WX_SECRET", "")
    if not appid or not secret:
        raise WeChatError("登录服务未配置", "缺少 WX_APPID / WX_SECRET，检查 .env")

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                CODE2SESSION_URL,
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as exc:
        raise WeChatError("网络繁忙，请稍后再试", f"code2session 请求失败：{exc}") from exc

    # 微信出错时 HTTP 仍是 200，靠 errcode 判断
    if data.get("errcode"):
        # 40029 是 code 无效或已用过，前端重新 wx.login 即可
        raise WeChatError(
            "登录已过期，请重试" if data["errcode"] == 40029 else "登录失败，请稍后再试",
            f"errcode={data['errcode']} errmsg={data.get('errmsg', '')}",
        )

    openid = data.get("openid")
    if not openid:
        raise WeChatError("登录失败，请稍后再试", f"返回里没有 openid：{data}")

    return {
        "openid": openid,
        "unionid": data.get("unionid"),
        "session_key": data.get("session_key", ""),
    }

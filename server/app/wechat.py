"""微信登录：拿小程序的 code 换 openid。

文档：https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-login/code2Session.html
appid / secret 在小程序后台「开发管理 - 开发设置」里，放 .env，不进版本库。

一个进程装多个小程序，每个小程序一套 appid/secret，所以 code2session 收参数而不是
读全局：拿错 appid 换来的是**另一个小程序的 openid 体系**，落库之后是认错人，
而微信那边只会回一句 40029，看不出是配串了。
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


def credentials(suffix: str = "") -> tuple[str, str]:
    """按小程序取 appid / secret。

    suffix 为空是安东尼之家——它的变量没有后缀，是历史原因，见 docs/adr/0001。
    安家立业传 "_ANJIA"。

    在函数里读、不在导入时读：.env 是 db.py 导入时才加载的。

    这里**不判空**：判空要抛异常，而测试是把 code2session 整个替掉的，凭据取不取
    得到本来与它们无关；在这里拦一道会让整套测试凭空依赖一份真实的 .env。
    缺配置由 code2session 报，见那边的 suffix 参数。
    """
    return os.getenv(f"WX_APPID{suffix}", "").strip(), os.getenv(f"WX_SECRET{suffix}", "").strip()


async def code2session(code: str, appid: str, secret: str, suffix: str = "") -> dict:
    """返回 {'openid': ..., 'unionid': ... | None, 'session_key': ...}。

    suffix 只用来在缺配置时**点名到具体变量**。矩阵里每个小程序一套 appid/secret，
    一句笼统的「缺少 appid / secret」会让人挨个变量去猜——真实发生过一次：安家立业
    能连库、能起服务，只有 WX_SECRET_ANJIA 是空的，而前端只看得到一个 400。
    """
    missing = [
        name
        for name, value in ((f"WX_APPID{suffix}", appid), (f"WX_SECRET{suffix}", secret))
        if not value
    ]
    if missing:
        raise WeChatError(
            "登录服务未配置",
            f"server/.env 里缺少 {' 和 '.join(missing)}，"
            "在微信公众平台「开发管理 → 开发设置」取值后填上",
        )

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

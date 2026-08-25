"""微信手机号快速验证：拿 getPhoneNumber 的 code 换手机号明文。

文档：https://developers.weixin.qq.com/miniprogram/dev/api-backend/open-api/phonenumber/phonenumber.getPhoneNumber.html

## 为什么是 access_token 这一套，不是 session_key 解密

微信给了两条路：新版是前端拿 `e.detail.code`、后端调开放接口换明文；旧版是前端拿
`encryptedData` + `iv`、后端用 users.session_key 做 AES-128-CBC 解密。这里走新版：

  * access_token 已经有现成的带缓存单飞实现（app/wechat_token.py），旧版则要从零写
    一套 AES 解密，还要处理 session_key 被下一次 wx.login 顶掉之后的失效重试；
  * 新版要求基础库 2.21.2+（2022 年初），覆盖率上不构成问题。低版本回调里没有 code，
    由前端提示手动输入，见 antony-casa/components/phone-get。

## ⚠️ 这个接口按次计费

微信对手机号快速验证是**按调用次数收费**的（有一批免费额度，超出后按次计价，
以公众平台「手机号快速验证」页面的当期公示为准）。也就是说这个接口的调用量
直接等于钱，所以调用方必须限频——见 app/users.py 的 _consume_phone_quota。
"""

import logging

import httpx

from . import wechat_token

logger = logging.getLogger(__name__)

GET_PHONE_URL = "https://api.weixin.qq.com/wxa/business/getuserphonenumber"


class PhoneError(Exception):
    """换手机号失败。message 是可以直接弹给用户的中文，detail 只进日志。

    不吞异常：调用方必须把它翻译成明确的 HTTP 错误，不能当作「没拿到就算了」。
    """

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(detail or message)
        self.message = message
        self.detail = detail


def mask(phone: str) -> str:
    """日志里的手机号一律脱敏到后四位。与 app/admin.py 的删除日志同一规则。"""
    return f"{phone[:3]}****{phone[7:]}" if len(phone) == 11 else "***"


async def _post(code: str) -> dict:
    """带一次 token 重试的 POST。

    token 可能在有效期内就被顶掉（别处调了 /cgi-bin/token），微信用 40001/42001
    表示这种情况。作废缓存重换一个再试一次——只重试一次，连着两次都是 token 无效
    就是配置问题，重试再多也没用。与 app/wxship.py 的 _post 同一套处理。
    """
    for attempt in (1, 2):
        token = await wechat_token.get_token(force=attempt == 2)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                GET_PHONE_URL, params={"access_token": token}, json={"code": code}
            )
        body: dict = response.json()
        if body.get("errcode") in (40001, 42001) and attempt == 1:
            logger.warning("access_token 失效，作废缓存后重试一次")
            wechat_token.invalidate()
            continue
        return body
    return {}


async def get_phone_number(code: str) -> str:
    """用一次性 code 换 11 位手机号。失败一律抛 PhoneError。"""
    if not wechat_token.configured():
        raise PhoneError(
            "手机号服务未配置",
            "server/.env 里缺少 WX_APPID / WX_SECRET，在微信公众平台"
            "「开发管理 → 开发设置」取值后填上",
        )

    try:
        body = await _post(code)
    except wechat_token.TokenError as exc:
        raise PhoneError("获取失败，请手动输入", f"换 access_token 失败：{exc}") from exc
    except httpx.HTTPError as exc:
        raise PhoneError("获取失败，请手动输入", f"getuserphonenumber 请求失败：{exc}") from exc

    # 微信出错时 HTTP 仍是 200，靠 errcode 判断。errcode=0 才是成功
    errcode = body.get("errcode")
    if errcode:
        raise PhoneError(
            "获取失败，请手动输入",
            f"errcode={errcode} errmsg={body.get('errmsg', '')}",
        )

    # purePhoneNumber 是不带区号的 11 位；phoneNumber 带 +86 前缀，落库过不了
    # users.phone 上 '^1[3-9][0-9]{9}$' 那条 CHECK，别取错
    phone = str(body.get("phone_info", {}).get("purePhoneNumber", "")).strip()
    if not phone:
        raise PhoneError("获取失败，请手动输入", f"返回里没有 purePhoneNumber：{body}")

    logger.info("手机号快速验证成功 phone=%s", mask(phone))
    return phone

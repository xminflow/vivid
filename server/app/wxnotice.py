"""订阅消息 —— 发货通知。

## 一次订阅换一次发送

小程序的订阅消息是**一次性**的：用户在提交订单时点一次「允许」，我们就获得
一次发送额度，发完即止。所以授权要在下单那一刻要（用户此刻最有动机同意），
而消息在发货时才发——中间可能隔几天，额度不会过期。

用户拒绝授权是常态，不是错误：微信会在发送时返回 43101，我们记一条 info 就过去。
**发不出通知绝不能让发货失败**——货已经交给快递了。

## 与发货信息录入的关系

两者都用小程序的 access_token（app/wechat_token.py），但目的完全不同：
  发货信息录入  给**微信平台**看的，不做会被判违规、影响交易权限（硬要求）
  订阅消息      给**用户**看的，发不出去只是体验差一点（软需求）
所以发货流程里，前者失败要在界面上警告运营，后者失败只记日志。

## 模板字段名要和小程序后台里选的模板一致

模板 ID 和字段键（thing1 / character_string2 这种）是在小程序后台「订阅消息」里
添加模板时**由微信分配**的，不同人添加同一个模板拿到的字段编号可能不同。
所以下面的 _FIELDS 必须按你实际那个模板改。填错微信会返回 47003（参数不符合规则），
错误原样记进日志——第一次发货就看得到，不会静默丢失。
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

from . import wechat_token

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/subscribe/send"

# 发货通知的模板 ID。在小程序后台「功能 - 订阅消息」里添加模板后拿到。
# 没配就不发通知（也不报错）——这个功能是可选的，不该挡住发货
SHIP_TEMPLATE_ID = os.getenv("WX_SHIP_TEMPLATE_ID", "").strip()

# 东八区。不用机器本地时区：容器的 TZ 由 Dockerfile 和 compose 各设一次，
# 漏一处就会给用户发出一个差 8 小时的发货时间。app/wxpay.py 有一份同样的常量，
# 那边是给支付协议用的——两个模块不互相依赖，各留一份比交叉引用好
_CHINA_TZ = timezone(timedelta(hours=8))

# 模板字段名 → 我们的值。
#
# ⚠️ 键名由微信在**添加模板时**分配，不同人添加同一个模板拿到的编号可能不同，
# 对不上返回 47003。下面这一份是用 wxaapi/newtmpl/gettemplate 查出来的实际模板
# 「订单发货通知」，内容是：
#     物品名称:{{thing5.DATA}}            发货时间:{{time1.DATA}}
#     订单编号:{{character_string2.DATA}}  物流公司:{{thing4.DATA}}
#     物流编号:{{character_string3.DATA}}
# 换模板时**先查一次这个接口拿真实键名**，别照抄别处的编号——
# 连同一个「订单发货通知」，换一次模板字段编号就可能全变。
#
# 字段类型决定了能填什么，填错同样是 47003：
#     time             24 小时制时间
#     character_string **只能数字/字母/符号**——不能填中文
#     thing            20 字符以内，可含中文
_FIELDS = {
    "thing5": "title",  # 物品名称
    "time1": "shipped_at",  # 发货时间
    "character_string2": "order_no",  # 订单编号
    "thing4": "company",  # 物流公司
    "character_string3": "tracking_no",  # 物流编号
}

# 用户拒绝接收 / 没有授权额度。这是最常见的「失败」，且完全正常
_ERRCODE_NO_PERMISSION = 43101


def configured() -> bool:
    """模板没配就整个功能关掉。与支付、发货信息录入各自独立。"""
    return bool(SHIP_TEMPLATE_ID) and wechat_token.configured()


def _clip(value: str, limit: int = 20) -> str:
    """模板的 thing 类字段有长度上限（一般 20 个字符），超了整条发不出去。
    截断而不是报错——用户收到一条标题被截短的通知，比收不到强。"""
    text = (value or "").strip()
    return text[:limit] if len(text) > limit else text


def _ascii_only(value: str, limit: int = 32) -> str:
    """character_string 类字段的清洗。

    这类字段**只接受数字、字母和符号**，混进一个中文字符整条就被 47003 拒掉。
    所以「没有运单号」的占位不能写「无」——同城配送和无需物流这两档正好没有
    运单号，那是常态而不是例外，写中文等于这两档的通知永远发不出去。
    """
    text = "".join(ch for ch in (value or "").strip() if ch.isascii() and not ch.isspace())
    return (text or "-")[:limit]


async def send_ship_notice(
    *,
    openid: str,
    order_id: str,
    order_no: str,
    title: str,
    company: str,
    tracking_no: str,
    shipped_at: datetime | None = None,
) -> None:
    """发一条发货通知。**任何失败都不抛**——通知发不出去不该影响发货。"""
    if not configured():
        return

    when = (shipped_at or datetime.now(timezone.utc)).astimezone(_CHINA_TZ)
    values = {
        # time 类字段：24 小时制。到分钟就够，秒对用户没有意义
        "shipped_at": when.strftime("%Y-%m-%d %H:%M"),
        "order_no": _ascii_only(order_no),
        "tracking_no": _ascii_only(tracking_no),
        "title": _clip(title),
        # thing 类允许中文。三档发货方式里有两档没有快递公司，
        # 说清楚比留一个「-」有用——用户看到「无需快递」就不会去等快递单号
        "company": _clip(company or "无需快递"),
    }
    data = {key: {"value": values[name]} for key, name in _FIELDS.items()}

    payload = {
        "touser": openid,
        "template_id": SHIP_TEMPLATE_ID,
        # 点通知进小程序时落在这一单的详情页
        "page": f"pages/order/order?id={order_id}",
        "data": data,
    }

    try:
        body = await _post(payload)
    except Exception:
        logger.exception("发货通知发送异常 order_no=%s", order_no)
        return

    errcode = body.get("errcode", 0)
    if errcode == 0:
        logger.info("发货通知已发送 order_no=%s", order_no)
        return
    if errcode == _ERRCODE_NO_PERMISSION:
        # 用户没授权或额度用完了。常态，不是错误
        logger.info("发货通知未发送：用户未授权 order_no=%s", order_no)
        return
    # 47003 是字段与模板不符——最可能的原因是 _FIELDS 的键名和后台那个模板对不上
    logger.warning(
        "发货通知发送失败 order_no=%s errcode=%s errmsg=%s",
        order_no,
        errcode,
        body.get("errmsg"),
    )


async def _post(payload: dict) -> dict:
    """带一次 token 重试的 POST，理由同 app/wxship.py。"""
    for attempt in (1, 2):
        token = await wechat_token.get_token(force=attempt == 2)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(SEND_URL, params={"access_token": token}, json=payload)
        body = response.json()
        if body.get("errcode") in (40001, 42001) and attempt == 1:
            wechat_token.invalidate()
            continue
        return body
    return {}

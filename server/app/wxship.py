"""发货信息录入 —— 把物流信息回传给微信。

## 为什么必须做这件事

商户号与小程序 appid 绑定后，用这个商户号收的每一笔钱都会被微信**自动同步**进
「发货管理」，生成一条待发货任务并开始计时。所以「有一笔单要发货」不需要我们
告诉微信，它自己就知道；我们要做的是回填物流信息。

超时不回填 = 判定发货延迟，平台先警告，连续三天不整改会触发支付风险提示，
直到暂停小程序交易。也就是说这不是可选的运营动作，是保住交易能力的硬要求。

## 与 app/wxpay.py 的区别

两套完全不同的鉴权体系，别混：

                 微信支付 APIv3            发货信息录入
    域名          api.mch.weixin.qq.com     api.weixin.qq.com
    认证          商户私钥签名 + 证书序列号   access_token（见 app/wechat_token.py）
    配置          WXPAY_*                   WX_APPID / WX_SECRET

## ⚠️ logistics_type 的取值没能从官方文档逐字核实

官方文档页是 SPA，抓不到正文；社区帖正文为空。下面这份映射来自检索到的资料，
**可能不准**。所以这里做了三件事让「填错」不会变成隐蔽故障：

  1. 映射抽成具名常量（_LOGISTICS_TYPE），改一处即可
  2. 微信的原始应答完整记进 error 日志（含 errcode/errmsg）
  3. 回传成功与否落库（shop_orders.shipping_uploaded_at），失败的单查得出来、重试得了

已知微信会用 errcode 10060005 表示「物流类型有误」——真填错了，第一次发货就会
在日志里看到这个码，而不是等到被判违规才发现。
"""

import logging
from datetime import datetime, timezone

import httpx

from . import wechat_token

logger = logging.getLogger(__name__)

UPLOAD_URL = "https://api.weixin.qq.com/wxa/sec/order/upload_shipping_info"

# 我们的三档发货方式 → 微信的 logistics_type。
#
# 我们只做三档（快递 / 同城配送 / 无需物流），因为卖家具走专线或自送时没有运单号。
# 微信侧的枚举据检索为：1 快递、2 同城配送、3 虚拟商品、4 用户自提。
#
# 'none'（无需物流）映射到 3 而不是 4：家具的「无需物流」通常是厂家直送或客户
# 自行安排运输，不是「到店自提」。这一条尤其需要按实际类目核对——
# 填错的表现是 errcode 10060005。
_LOGISTICS_TYPE = {
    "express": 1,
    "local": 2,
    "none": 3,
}

# 常用快递公司编码。微信有专门的接口可以拉全量列表，这里硬编码一份常用的：
# 拉全量要额外一次 access_token 调用和一份缓存，而运营实际会用到的就这几家。
# 不在列表里的走「其他」，运营在后台手填编码——比让他在几百项里翻要快。
EXPRESS_COMPANIES = [
    ("SF", "顺丰速运"),
    ("JD", "京东物流"),
    ("ZTO", "中通快递"),
    ("YTO", "圆通速递"),
    ("STO", "申通快递"),
    ("YUNDA", "韵达速递"),
    ("EMS", "中国邮政EMS"),
    ("YOUZHENGGUONEI", "邮政快递包裹"),
    ("HTKY", "百世快递"),
    ("JTSD", "极兔速递"),
    ("DBL", "德邦快递"),
    ("ZJS", "宅急送"),
]

_VALID_COMPANY_CODES = {code for code, _ in EXPRESS_COMPANIES}


class ShipError(RuntimeError):
    """回传失败。调用方**不要**因此回滚发货——货可能真的已经交给快递了。"""


def configured() -> bool:
    return wechat_token.configured()


def known_company(code: str) -> bool:
    """编码在不在内置列表里。不在也允许提交（列表不全），只是记一条日志。"""
    return code in _VALID_COMPANY_CODES


async def upload_shipping_info(
    *,
    transaction_id: str,
    openid: str,
    shipping_type: str,
    item_desc: str,
    tracking_no: str | None = None,
    express_company: str | None = None,
) -> None:
    """把一笔订单的物流信息回传给微信。失败抛 ShipError。

    用 transaction_id（微信支付订单号）而不是商户订单号来定位：两种都支持，
    但商户订单号那种要同时带 mchid，而 transaction_id 是全局唯一的，少一个出错点。
    没有 transaction_id 就说明这笔单压根没支付成功，也就不该发货。
    """
    if shipping_type not in _LOGISTICS_TYPE:
        raise ShipError(f"未知的发货方式：{shipping_type}")

    shipping: dict = {
        # 微信对描述有长度限制，截断而不是报错——运营不该因为商品名太长而发不了货
        "item_desc": item_desc[:120],
    }
    if shipping_type == "express":
        # 走快递才有这两项。库上的 CHECK 已经保证了 express 必有运单号
        shipping["tracking_no"] = tracking_no
        shipping["express_company"] = express_company

    payload = {
        "order_key": {
            # 1 = 用微信支付订单号定位
            "order_number_type": 1,
            "transaction_id": transaction_id,
        },
        "logistics_type": _LOGISTICS_TYPE[shipping_type],
        # 1 = 统一发货（整单一次发完）。我们的订单是不可分的，不存在分拆发货
        "delivery_mode": 1,
        "shipping_list": [shipping],
        "upload_time": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "payer": {"openid": openid},
    }

    body = await _post(payload)

    errcode = body.get("errcode", 0)
    if errcode == 0:
        logger.info(
            "发货信息已回传微信 transaction_id=%s 方式=%s 运单号=%s",
            transaction_id,
            shipping_type,
            tracking_no or "-",
        )
        return

    # 完整记下来。10060005 是物流类型有误，48001 是接口未授权（小程序没开通这个能力）
    logger.error(
        "发货信息回传失败 transaction_id=%s errcode=%s errmsg=%s payload_logistics_type=%s",
        transaction_id,
        errcode,
        body.get("errmsg"),
        payload["logistics_type"],
    )
    raise ShipError(f"回传微信失败（{errcode}: {body.get('errmsg')}）")


async def _post(payload: dict) -> dict:
    """带一次 token 重试的 POST。

    token 可能在有效期内就被顶掉（别处调了 /cgi-bin/token）。微信用 40001/42001
    表示这种情况，此时作废缓存重换一个再试一次——只重试一次，
    连着两次都是 token 无效就是配置问题，重试再多也没用。
    """
    for attempt in (1, 2):
        token = await wechat_token.get_token(force=attempt == 2)
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                UPLOAD_URL, params={"access_token": token}, json=payload
            )
        body = response.json()
        if body.get("errcode") in (40001, 42001) and attempt == 1:
            logger.warning("access_token 失效，作废缓存后重试一次")
            wechat_token.invalidate()
            continue
        return body
    return {}

"""微信支付 APIv3 —— 配置、客户端、四个用得上的操作。

设计见 docs/superpowers/specs/2026-08-10-anxi-ji-design.md 第四节。
业务编排（建单、状态迁移、清车）在 app/shop_orders.py 与 app/shop_pay.py，
这一层只负责「把微信那套协议包起来」，不碰库、不认识订单。

## 只支持「微信支付公钥」模式

官方对新商户的指引是直接用公钥、不要碰平台证书（平台证书有效期 5 年、要主动
轮换，还得自己处理下载与缓存）。安玺·集是全新接入，落在「无需切换」那一档，
所以这里**不实现平台证书模式**——留两条路会让人以为可以随便配，而错配的表现
是验签在运行时才炸。

## 缺配置不让服务起不来

模块导入期不做任何校验，`configured()` 供调用方判断。这与 app/cos.py 一致：
不涉及交易的功能（预约、服务申请、商品浏览）必须能在没有支付配置的机器上
照常开发和跑测试。真有人下单时才会拿到一句明确的错误。

## SDK 是阻塞的

`wechatpayv3` 内部用 requests，全部调用都要包进 anyio.to_thread，
否则会把事件循环卡住——与 app/admin_auth.py 跑 scrypt 的处理是同一个理由。
"""

import gzip
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anyio import to_thread
from dotenv import load_dotenv
from wechatpayv3 import WeChatPay, WeChatPayType

logger = logging.getLogger(__name__)

# 给 SDK 用的 logger，级别**钉死在 INFO**。
#
# SDK 在 debug 级别会打印回调的完整 headers、原始 body，以及**解密后的明文
# resource**（含 openid、金额、微信支付订单号），请求那一侧还会打印下单参数。
# 而 CLAUDE.md 要求开发环境必须开 debug —— 两条规则直接冲突，冲突的结果是
# 支付明文进日志文件。
#
# 在这个 logger 上设级别，SDK 的 logger.debug() 在发出前就被丢掉（isEnabledFor
# 为假），不依赖根 logger 或 handler 的配置。代价是排查支付协议问题时看不到
# SDK 的调试输出——真需要时临时把这一行改成 DEBUG，别改全局。
sdk_logger = logging.getLogger(__name__ + ".sdk")
sdk_logger.setLevel(logging.INFO)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 微信支付的时间字段一律是东八区带偏移量的 RFC3339。
#
# 不能用 time.localtime() 再拼一个字面量的 '+08:00'：那个组合只有在机器时区
# 恰好是东八区时才正确。容器里 TZ 由 Dockerfile 和 compose 各设一次，任何一处
# 漏了（本地开发、CI、临时起的容器）算出来的就是 8 小时前的时刻，
# 而 time_expire 落在过去会被微信直接拒单。用固定偏移量与机器时区彻底无关。
CHINA_TZ = timezone(timedelta(hours=8))


def _normalize_pem(value: str, label: str) -> str:
    """把 .env 里的单行 PEM 还原成真正的 PEM。

    .env 一行一个值，而 PEM 本身是多行的。所以约定：可以把 \\n 写成字面量的
    反斜杠 n，两端可以带引号。这里统一还原，免得每台机器的 .env 写法都不一样。

    已经是多行的原样返回——直接从文件粘过来的情况必须也能用。
    """
    text = value.strip().strip('"').strip("'")
    if not text:
        return ""
    if "\\n" in text and "\n" not in text:
        text = text.replace("\\n", "\n")
    if "-----BEGIN" not in text:
        # 不猜也不补头尾：补错了会在验签时才炸，而那时的报错完全指不到这里
        raise ValueError(f"{label} 不像 PEM，应当包含 -----BEGIN 开头的那一行")
    return text


def _read_key(inline_env: str, path_env: str, label: str) -> str:
    """密钥可以内联在 .env，也可以只给一个文件路径。

    两种都支持是因为部署方式不同：本地开发直接粘进 .env 最省事；
    服务器上更适合把密钥文件单独放、只在 .env 里写路径（文件权限管得住，
    而 .env 会被 compose 整个读进环境变量）。内联优先。
    """
    inline = os.getenv(inline_env, "")
    if inline.strip():
        return _normalize_pem(inline, label)

    path = os.getenv(path_env, "").strip()
    if path:
        file = Path(path)
        if not file.exists():
            raise ValueError(f"{path_env} 指向的文件不存在：{path}")
        return _normalize_pem(file.read_text(encoding="utf-8"), label)

    return ""


MCHID = os.getenv("WXPAY_MCHID", "").strip()
APIV3_KEY = os.getenv("WXPAY_APIV3_KEY", "").strip()
CERT_SERIAL = os.getenv("WXPAY_CERT_SERIAL", "").strip()
PUBLIC_KEY_ID = os.getenv("WXPAY_PUBLIC_KEY_ID", "").strip()
NOTIFY_URL = os.getenv("WXPAY_NOTIFY_URL", "").strip()
# 与登录用的是同一个 appid：小程序只有一个，code2session 和支付都用它
APPID = os.getenv("WX_APPID", "").strip()

# 订单多久没付就自动关掉。与定时任务里的扫描条件、以及下单时传给微信的
# time_expire 用同一个值——两处不一致会出现「微信那边还能付，我们这边已经关了」
PAY_TIMEOUT_MINUTES = 30

# 支付成功后回调可能迟到，小程序会主动查单。这是两次查单之间的最小间隔，
# 防止用户反复下拉刷新把微信的查单接口打爆
QUERY_THROTTLE_SECONDS = 30

# 两次向微信**下单**之间的最小间隔。查单和下单是两个接口、两套频率限制，
# 所以与上面那个分开：循环调用「去支付」等于无限消耗下单配额，
# 还会各占一条数据库连接（池只有 5 条）和一个阻塞线程
ORDER_THROTTLE_SECONDS = 5

# 回调请求体上的时间戳与本机相差超过这个秒数就拒绝。微信官方给的建议是 5 分钟。
#
# SDK **不做这件事**（utils.rsa_verify 只验签名，core._verify_signature 里没有
# 任何时间比较），所以必须在这一层补。就本系统而言重放同一份回调是无害的
# （_settle_paid 幂等），但「验过签的请求可以无限期重放」不该是一个我们默认接受的
# 前提——将来任何一处依赖回调做非幂等的事，缺了这一条就是可利用的
CALLBACK_MAX_AGE_SECONDS = 300


def _key_present(inline_env: str, path_env: str) -> bool:
    """密钥可以内联也可以给路径，两者有其一即可。这里只看「配没配」，
    内容合不合法留到 client() 里解析时报错——那时的报错能指明是哪一把、错在哪。"""
    return bool(os.getenv(inline_env, "").strip()) or bool(os.getenv(path_env, "").strip())


def _private_key_present() -> bool:
    return _key_present("WXPAY_PRIVATE_KEY", "WXPAY_PRIVATE_KEY_PATH")


def _public_key_present() -> bool:
    return _key_present("WXPAY_PUBLIC_KEY", "WXPAY_PUBLIC_KEY_PATH")


def configured() -> bool:
    """七项齐了才算配好。少任何一项都当作没配——半配的状态比没配更难查。

    七项里有两组是**成对**的，很容易只填一个：
      商户 API 证书序列号 + 商户私钥   我们签名给微信看（出方向）
      微信支付公钥 + 公钥 ID          我们验微信的签名（入方向）
    公钥和公钥 ID 在商户平台是同一页上的两个字段，只填一个的话，
    这里如果不查公钥本身，就会「看起来配好了」，直到有人真下单才 503。
    """
    return (
        all([MCHID, APIV3_KEY, CERT_SERIAL, PUBLIC_KEY_ID, NOTIFY_URL, APPID])
        and _private_key_present()
        and _public_key_present()
    )


def missing_config() -> list[str]:
    """列出缺了哪几项。只在日志里用，不出接口——出接口等于告诉外面我们缺什么。"""
    missing = []
    for name, value in (
        ("WXPAY_MCHID", MCHID),
        ("WXPAY_APIV3_KEY", APIV3_KEY),
        ("WXPAY_CERT_SERIAL", CERT_SERIAL),
        ("WXPAY_PUBLIC_KEY_ID", PUBLIC_KEY_ID),
        ("WXPAY_NOTIFY_URL", NOTIFY_URL),
        ("WX_APPID", APPID),
    ):
        if not value:
            missing.append(name)
    if not _private_key_present():
        missing.append("WXPAY_PRIVATE_KEY（或 WXPAY_PRIVATE_KEY_PATH）")
    if not _public_key_present():
        missing.append("WXPAY_PUBLIC_KEY（或 WXPAY_PUBLIC_KEY_PATH）")
    return missing


class PayNotConfigured(RuntimeError):
    """支付未配置。调用方翻译成一句人话的 4xx，不要让它变成 500。"""


class PayError(RuntimeError):
    """微信那边返回了非成功状态，或网络出错。"""


class BillNotExist(PayError):
    """微信说这一天没有这份账单。

    **绝大多数日子都会走到这里**，它是常态不是故障：`NO_STATEMENT_EXIST` 同时
    表示「当天没有发生退款」和「账单还没生成」，从接口上分不开。所以调用方
    应当安静跳过，而不是当成错误告警——那样每天都会响。
    """


class PayOrderNotExist(PayError):
    """微信侧根本没有这笔单。

    单独一个类型，是因为它的处置方式和别的失败**完全相反**：一般的查单失败
    （网络抖动、微信 5xx）要重试，而「这笔单微信从来没见过」重试一万次也是同样的
    答案——它意味着下单那一刻就没提交成功（订单按设计仍然保留为待付款）。
    不区分的话，这类单会永远留在超时扫描的取数窗口里，把队头堵死。
    """


_client: WeChatPay | None = None


def client() -> WeChatPay:
    """惰性构造，进程内单例。

    不在模块导入期构造：那样没配支付的机器连 import app.main 都会失败，
    整个服务起不来——而绝大多数功能与支付无关。
    """
    global _client
    if _client is not None:
        return _client

    if not configured():
        raise PayNotConfigured("支付未配置：" + "、".join(missing_config()))

    # configured() 已经确认两把都配了，这里读出来的是内容——
    # 读不出来就是 PEM 格式不对，_normalize_pem 会带着字段名报错
    private_key = _read_key("WXPAY_PRIVATE_KEY", "WXPAY_PRIVATE_KEY_PATH", "商户 API 私钥")
    public_key = _read_key("WXPAY_PUBLIC_KEY", "WXPAY_PUBLIC_KEY_PATH", "微信支付公钥")

    _client = WeChatPay(
        wechatpay_type=WeChatPayType.MINIPROG,
        mchid=MCHID,
        private_key=private_key,
        cert_serial_no=CERT_SERIAL,
        apiv3_key=APIV3_KEY,
        appid=APPID,
        notify_url=NOTIFY_URL,
        # 公钥模式：这两项给了，SDK 就不会去下载和缓存平台证书
        public_key=public_key,
        public_key_id=PUBLIC_KEY_ID,
        logger=sdk_logger,
    )
    logger.info(
        "微信支付已初始化 mchid=%s appid=%s 验签=微信支付公钥 notify=%s",
        MCHID,
        APPID,
        NOTIFY_URL,
    )
    return _client


# 微信表示「这笔单不存在」的错误码。两种拼法都收：文档里是 ORDERNOTEXIST，
# 但部分接口返回的是带下划线的形式，认错了就退回成「普通失败」——那是安全的方向
_ORDER_NOT_EXIST_CODES = {"ORDERNOTEXIST", "ORDER_NOT_EXIST"}

# 「这一天没有这份账单」。实测大多数日子的退款账单都是这个码，见 BillNotExist
_NO_STATEMENT_CODE = "NO_STATEMENT_EXIST"


def _error_code(message: str) -> str:
    """从微信的错误应答里取出 code 字段。取不到返回空串。"""
    import json

    try:
        body = json.loads(message)
    except ValueError:
        return ""
    return body.get("code", "") if isinstance(body, dict) else ""


def _check(code: int, message: str, action: str) -> dict:
    """SDK 的返回是 (http_code, body_text)。非 2xx 一律抛，不让调用方去解析文本。"""
    import json

    if code not in (200, 204):
        # 不把 message 原样透给用户：里面可能有商户号等信息
        logger.error("微信支付%s失败 code=%s body=%s", action, code, message[:400])
        if _error_code(message) in _ORDER_NOT_EXIST_CODES:
            # 可以重试的失败和「重试多少次都是这个答案」必须分开，见 PayOrderNotExist
            raise PayOrderNotExist(f"{action}失败：微信侧没有这笔单")
        raise PayError(f"{action}失败")
    if not message:
        return {}
    try:
        return json.loads(message)
    except ValueError:
        logger.error("微信支付%s返回的不是 JSON: %s", action, message[:200])
        raise PayError(f"{action}失败") from None


def expire_at_of(created_at: datetime) -> datetime:
    """一笔订单的支付截止时刻。**唯一的定义在这里**，两处用：
    下单时告诉微信的 time_expire，与超时扫描判断该不该关单的界线。

    锚点是订单的 created_at，不是「现在」。用「现在 + 30 分钟」的话，用户在第 29
    分钟点一次「去支付」，微信侧的有效期就被推到第 59 分钟，而我们仍然按 created_at
    在第 30 分钟去关单——正是这个函数要避免的那种「微信那边还能付、我们这边已经关了」。
    """
    return created_at + timedelta(minutes=PAY_TIMEOUT_MINUTES)


async def jsapi_order(
    *,
    out_trade_no: str,
    description: str,
    total_cents: int,
    openid: str,
    notify_url: str,
    expire_at: datetime,
) -> dict:
    """JSAPI 下单，返回给小程序 wx.requestPayment 用的那组参数。

    openid 由服务端从 users 表取，**客户端从不接触 openid**——它是用户在本小程序
    内的唯一标识，泄漏了换不掉。

    expire_at 由调用方按订单的 created_at 算好（见 expire_at_of），
    与我们自己的超时关单用的是同一个时刻：两边不一致就会出现「微信那边还能付，
    我们这边已经把单关了」，用户付完钱看到的是已关闭的订单。
    """
    pay = client()
    # 转成东八区再序列化。isoformat 出来的偏移量带冒号（+08:00），
    # 正是微信要的形式——strftime('%z') 给的是 +0800，微信不认
    expire = expire_at.astimezone(CHINA_TZ).isoformat(timespec="seconds")
    code, message = await to_thread.run_sync(
        lambda: pay.pay(
            description=description,
            out_trade_no=out_trade_no,
            amount={"total": total_cents},
            payer={"openid": openid},
            time_expire=expire,
            notify_url=notify_url,
            pay_type=WeChatPayType.JSAPI,
        )
    )
    body = _check(code, message, "下单")
    prepay_id = body.get("prepay_id")
    if not prepay_id:
        logger.error("微信支付下单没有返回 prepay_id: %s", str(body)[:200])
        raise PayError("下单失败")

    # paySign 是**唯一需要我们自己签**的东西。签名串的四行顺序是协议规定的，
    # 与 wx.requestPayment 收到的字段一一对应，顺序错了签名验不过
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    package = f"prepay_id={prepay_id}"
    sign = await to_thread.run_sync(
        lambda: pay.sign([APPID, timestamp, nonce, package])
    )
    return {
        "timeStamp": timestamp,
        "nonceStr": nonce,
        "package": package,
        "signType": "RSA",
        "paySign": sign,
    }


async def query_order(out_trade_no: str) -> dict:
    """按商户订单号查单。掉单兜底与超时关单前都要先查一次。"""
    pay = client()
    code, message = await to_thread.run_sync(lambda: pay.query(out_trade_no=out_trade_no))
    return _check(code, message, "查单")


async def close_order(out_trade_no: str) -> None:
    """关闭订单。

    超时关单必须**先关微信那边再改自己的状态**：反过来的话，用户可能在我们
    置成已关闭之后、微信关单之前完成支付，钱收了但订单是关的。
    关一笔已支付的单微信会报错，由调用方判断是否忽略。
    """
    pay = client()
    code, message = await to_thread.run_sync(lambda: pay.close(out_trade_no=out_trade_no))
    _check(code, message, "关单")


async def refund(*, out_trade_no: str, out_refund_no: str, total_cents: int, reason: str) -> dict:
    """整单退款。只做整退，不做部分退（见 spec 决策）。

    amount 里 refund 与 total 相等即整退；currency 必填。
    """
    pay = client()
    code, message = await to_thread.run_sync(
        lambda: pay.refund(
            out_refund_no=out_refund_no,
            out_trade_no=out_trade_no,
            amount={"refund": total_cents, "total": total_cents, "currency": "CNY"},
            reason=reason or None,
        )
    )
    return _check(code, message, "退款")


def decode_bill(data: bytes | str) -> str:
    """把账单下载的原始应答变成 CSV 文本。

    ## 下载回来的是字节，不是文本

    SDK 的 `core.request` 只在 Content-Type 是 application/json 时返回 str，
    其余一律返回 `response.content`（bytes）。账单是 CSV，走的正是 bytes 那一支。
    直接把它交给 csv/StringIO 会 TypeError。

    ## 为什么要用 utf-8-sig

    微信的账单带 BOM。用 utf-8 解出来，**第一列的列名会多一个 \\ufeff**，
    而 shop_reconcile 是按列名找列的（`header.index('商户订单号')`），
    差这一个字符就会判定「列名对不上」而整批跳过——不报错，只是永远不对账。
    """
    if isinstance(data, str):
        return data
    # gzip 魔数。tar_type=GZIP 时是压缩的；留一条不压缩的路，
    # 免得微信哪天改了默认行为就整批解不开
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data.decode("utf-8-sig")


async def refund_bill(bill_date: str) -> str:
    """下载某一天的**退款**账单，返回 CSV 文本。

    bill_date 形如 '2026-08-12'，只能取**昨天及更早**——账单是 T+1 生成的。

    ## 为什么只要退款那一份

    bill_type='REFUND' 只含当天发生退款的记录，而不是全部交易。对我们要做的事
    （发现系统外退款）来说这是最小的数据集：家具生意一天有一笔退款都算多。
    用 ALL 的话文件大小会随交易量长，白下载。

    ## 为什么必须传 GZIP

    看着像是「不压缩更简单」，但**微信不接受不压缩**这个表达方式：SDK 的
    `trade_bill` 把 tar_type 无条件拼进 query（`...&tar_type=%s`），传 None
    出来的是字面量字符串 `tar_type=None`，微信回 400 PARAM_ERROR
    （「无法映射到合法的压缩类型枚举」）。也就是说传 None 不是「不压缩」，
    是**发了一个非法值**，每一次对账都必然失败。

    真要不压缩就得整个绕过 SDK 自己拼 path，为省一次 gzip.decompress 不值得。

    ## 当天没有退款时

    微信返回 400 NO_STATEMENT_EXIST（「请求的账单文件不存在」），
    **不是**一份只有表头的空文件。而且这个码同时covers「账单还没生成」，
    两者从接口上分不开，所以单独抛 BillNotExist 交给调用方按常态处理。

    两步：先申请拿到下载地址（带 token，有效期短），再下载。
    """
    pay = client()

    code, message = await to_thread.run_sync(
        lambda: pay.trade_bill(bill_date=bill_date, bill_type="REFUND", tar_type="GZIP")
    )
    if code != 200 and _error_code(message) == _NO_STATEMENT_CODE:
        raise BillNotExist(f"{bill_date} 没有退款账单")
    body = _check(code, message, "申请退款账单")
    url = body.get("download_url")
    if not url:
        logger.error("申请退款账单没有返回 download_url: %s", str(body)[:200])
        raise PayError("申请退款账单失败")

    code, data = await to_thread.run_sync(lambda: pay.download_bill(url))
    if code != 200:
        logger.error("下载退款账单失败 code=%s body=%s", code, str(data)[:200])
        raise PayError("下载退款账单失败")
    return decode_bill(data)


def _precheck_callback(headers: dict) -> str:
    """进 SDK 之前的廉价校验。返回空串表示通过，否则是拒绝的理由。

    ## 为什么必须挡在 SDK 外面

    这个接口是**无鉴权**的公网入口，而 SDK 的 _verify_signature 在序列号对不上时
    会走这一段（wechatpayv3/core.py）：

        for cert in self._certificates: ...   # 公钥模式下这个列表恒为空
        if not cert_found:
            self._update_certificates()       # ← 真的发一次 HTTPS 到微信

    也就是说，任何人只要带一个随便写的 Wechatpay-Serial 打过来，**每一个请求都会
    让我们主动向微信发一次证书下载请求**。两层放大：

      * /v3/certificates 有频率限制，打爆之后商户 API 被限流，**真实支付跟着挂**
      * 那是 requests 的阻塞调用，包在 to_thread 里；anyio 默认 40 个线程槽，
        几十个并发伪造请求就能占满，连带 scrypt 校验密码、所有微信调用一起排队

    另外 SDK 在签名类型不对时是 `raise Exception`，落到调用方就是一次
    logger.exception 打整条堆栈——同样是一个不需要任何凭据就能刷满日志的口子。

    这三件事在这里全部变成一次字符串比较。
    """
    if headers.get("wechatpay-signature-type") != "WECHATPAY2-SHA256-RSA2048":
        return "签名类型不是 WECHATPAY2-SHA256-RSA2048"

    # 公钥模式下，微信会把公钥 ID 放在这个头里。对不上就不可能验得过，
    # 更不该让 SDK 拿着它去下载平台证书
    serial = headers.get("wechatpay-serial", "")
    if serial != PUBLIC_KEY_ID:
        return "证书序列号与微信支付公钥 ID 不符"

    raw = headers.get("wechatpay-timestamp", "")
    try:
        sent_at = int(raw)
    except ValueError:
        return "时间戳不是整数"
    # SDK 不校验时间窗口，只验签名。补在这里
    age = abs(time.time() - sent_at)
    if age > CALLBACK_MAX_AGE_SECONDS:
        return f"时间戳超出 {CALLBACK_MAX_AGE_SECONDS} 秒窗口（相差 {int(age)} 秒）"

    if not headers.get("wechatpay-signature") or not headers.get("wechatpay-nonce"):
        return "缺少签名或随机串"

    return ""


async def verify_callback(headers: dict, body: str) -> dict | None:
    """验签 + 解密回调，返回明文的 resource。验不过返回 None。

    这一步是整条支付链路上**唯一防止他人伪造「支付成功」的东西**，分三段：

      1. `_precheck_callback` —— 纯字符串比较，挡掉不带有效头的请求。
         这一段的意义不只是省事，见那个函数的注释
      2. SDK 的 callback() —— 按 timestamp\\nnonce\\nbody\\n 验 RSA-SHA256 签名，
         再用 APIv3 密钥 AES-256-GCM 解密 resource
      3. 商户号与 appid 比对 —— 确认这条通知确实是发给**我们这个商户**的

    body 必须是**原始请求体**，不能是解析后重新序列化的 JSON——键顺序或空格差
    一个字符签名就对不上，而症状是「偶尔失败」，极难查。
    """
    pay = client()

    # 统一成小写再往下传。Starlette 给的本来就是小写，但这个函数的两个调用方
    # （接口与测试）传什么都可能，而 SDK 认的是原样的键名
    headers = {str(k).lower(): v for k, v in headers.items()}

    rejected = _precheck_callback(headers)
    if rejected:
        # 不记 body 也不打堆栈：这个口子公网可达，扫描器会反复来试，
        # 每次打一屏堆栈等于给了别人一个刷日志的开关。一行够定位
        logger.warning("微信支付回调被拒：%s", rejected)
        return None

    result = await to_thread.run_sync(lambda: pay.callback(headers=headers, body=body))
    if not result:
        # 不记 body：里面有支付信息。只记事件本身，够定位「有人在打这个口子」
        logger.warning("微信支付回调验签未通过")
        return None

    resource = result.get("resource") or None
    if resource is None:
        return None

    # 验签已经证明这条通知来自微信，这里再确认它是发给**我们**的。
    # 伪造需要同时持有我们的公钥和 APIv3 密钥，所以这一条挡不住已经泄密的场景；
    # 它挡的是配置错误——比如某台机器的 .env 混用了另一个商户号，
    # 那种情况下没有这两行的话，两个商户的订单会静默地互相结算
    if resource.get("mchid") not in (None, MCHID):
        logger.error("微信支付回调的商户号不是本商户，已丢弃 mchid=%s", resource.get("mchid"))
        return None
    if resource.get("appid") not in (None, APPID):
        logger.error("微信支付回调的 appid 不是本小程序，已丢弃 appid=%s", resource.get("appid"))
        return None

    return resource

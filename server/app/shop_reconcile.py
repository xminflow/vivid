"""每日退款对账 —— 用微信的账单发现「在系统外退掉的款」。

## 这一层要解决的问题

退款的正常入口是后台那个按钮（先调微信再改状态，两边一致）。但三种情况绕得过去，
且都不是靠规范能杜绝的：商户平台的超级管理员、微信客诉强制退款、我们自己故障
期间的应急退款。绕过去之后「钱退了、我们的库还显示已发货」，账面就一直是错的。

## 为什么是账单而不是逐单查

最早写成了定时轮询：每分钟挑几笔订单去问微信「你退款了吗」。那个设计的调用量
**正比于订单存量**——想覆盖已发货和已完成的单，条件就变成「近 30 天所有订单」，
100 单/月是 7.2 万次调用/月，1000 单/月是 72 万次，随业务增长没有上限。

账单是反过来的：**一天一次调用覆盖当天全部退款**，与订单量无关。而且用
bill_type='REFUND' 只拿退款那一份，家具生意绝大多数日子这个文件只有表头。

实物损失（把已退款的单发出去）不靠这里兜，靠发货前查那一次
（shop_pay.sync_refund_state）——那个精确挡在损失发生前一秒。
这里管的是**账面准确**，慢一天没有实质风险。

## 为什么每次跑三天而不是只跑昨天

对账是幂等的（「状态不是已退款就同步」，重复处理是空操作），所以窗口重叠不花钱。
而只取昨天有两个真实的失败模式：

  1. **任务错过了那一天**——服务重启、部署、容器挂掉，那一天就永久跳过了，
     没有任何机制会回头补
  2. **账单还没生成**——微信的账单是 T+1 的，当天早上才出；任务跑在生成之前
     会拿不到，只取昨天就再也没有第二次机会

三天窗口让每一天有三次被处理的机会，代价是每天多两次几 KB 的下载。
"""

import csv
import io
import logging
from datetime import date, timedelta

import psycopg

from . import wxpay
from .db import pool
from .order_no import ORDER_NO_PREFIX, is_ours

logger = logging.getLogger(__name__)

# 每次跑覆盖最近几天（不含今天——账单是 T+1 的）
RECONCILE_DAYS = 3

# 账单 CSV 里我们要的两列。按**列名**取而不是按下标：
# 微信增删列时下标会整体移位，而那种改动不会有任何报错，只会悄悄取错值
_ORDER_NO_COLUMN = "商户订单号"
_REFUND_STATE_COLUMN = "退款状态"

# 退款成功的状态值。只同步成功的——申请中或失败的退款，钱还在我们这儿
_REFUND_SUCCESS = "SUCCESS"


def _unquote(cell: str) -> str:
    """剥掉微信账单里每个字段前的反引号。

    微信在每个字段前加一个 ` 是为了防止 Excel 把长数字变成科学计数法
    （订单号 20 位，不加的话打开就成了 2.02608E+19）。程序读的时候要剥掉。

    顺手也剥 BOM：正常路径上 wxpay.decode_bill 已经用 utf-8-sig 处理掉了，
    这里是第二道——账单文本万一从别处进来（手工粘的文件、以后换实现），
    BOM 会粘在**第一列的列名**上，而我们是按列名找列的，
    差这一个看不见的字符就整批跳过、且不报任何错。
    """
    return cell.lstrip("﻿").lstrip("`").strip()


def parse_refund_bill(text: str) -> list[str]:
    """从退款账单 CSV 里挑出**退款成功**的商户订单号。

    账单的形状：第一行表头，中间是数据行，最后两行是汇总（总退款笔数、总金额等）。
    汇总行的列数与数据行不同，靠「取不到商户订单号就跳过」自然滤掉，
    不用按行号判断——按行号的写法在微信改汇总行数时会静默失效。
    """
    reader = csv.reader(io.StringIO(text.strip()))
    rows = list(reader)
    if not rows:
        return []

    header = [_unquote(cell) for cell in rows[0]]
    try:
        order_no_at = header.index(_ORDER_NO_COLUMN)
        state_at = header.index(_REFUND_STATE_COLUMN)
    except ValueError:
        # 列名对不上说明微信改了账单格式。宁可整批不处理也不猜下标——
        # 猜错会把别的列当订单号，那比不对账更糟
        logger.error("退款账单的列名对不上，跳过本次对账。表头=%s", header[:12])
        return []

    order_nos = []
    for row in rows[1:]:
        if len(row) <= max(order_no_at, state_at):
            continue  # 汇总行
        order_no = _unquote(row[order_no_at])
        state = _unquote(row[state_at])
        # 只认**本环境前缀**的单号。账单是商户级的，而开发环境与生产环境共用
        # 同一个商户号（微信支付没有沙箱），所以这份文件里同时有两个环境的退款。
        # 按「以字母开头」之类的宽松条件过滤，会把开发环境退的测试单同步到
        # 生产库里同号的那笔真实订单上——而这个任务是每天无人值守跑的。
        # 见 app/order_no.py
        if is_ours(order_no) and state == _REFUND_SUCCESS:
            order_nos.append(order_no)
    return order_nos


async def _sync_one(conn: psycopg.AsyncConnection, order_no: str) -> bool:
    """把一笔已在微信退款的订单同步成已退款。返回是否真的改了。

    WHERE 里再挡一次前缀。parse_refund_bill 已经过滤过了，这里是第二道：
    这条 UPDATE 只按 order_no 定位，一旦有人放宽了上面那个过滤条件，
    受损的是**另一个环境的真实订单**，而且没有任何回滚余地。
    两处都写，代价是一个字符串比较。
    """
    updated = await conn.execute(
        """
        UPDATE shop_orders
           SET status = 'refunded', refunded_at = COALESCE(refunded_at, now()),
               refund_reason = COALESCE(NULLIF(refund_reason, ''), %s)
         WHERE order_no = %s
           AND order_no LIKE %s
           AND status IN ('pending_ship', 'pending_receive', 'completed')
        """,
        (
            "在微信商户平台退款（系统外操作，请到商户平台核对）",
            order_no,
            f"{ORDER_NO_PREFIX}%",
        ),
    )
    return bool(updated.rowcount)


async def reconcile_refunds(days: int = RECONCILE_DAYS) -> int:
    """跑一次对账，返回同步了几笔。

    某一天的账单取不到（还没生成、网络失败）不影响其余几天——各天独立，
    而且下次跑还会再覆盖到它。
    """
    if not wxpay.configured():
        return 0

    synced = 0
    today = date.today()

    for offset in range(1, days + 1):
        bill_date = (today - timedelta(days=offset)).isoformat()
        try:
            text = await wxpay.refund_bill(bill_date)
        except wxpay.BillNotExist:
            # **绝大多数日子都走这里**：当天没有退款。微信用同一个码表示
            # 「没有退款」和「账单还没生成」，分不开，所以只能安静跳过。
            # 用 debug 而不是 info：每天三条、常年如此，占着 info 会把真事件淹掉
            logger.debug("这一天没有退款账单，跳过 date=%s", bill_date)
            continue
        except wxpay.PayError:
            # 真的出错了（网络、鉴权、参数）。下一次跑会再覆盖到这一天，
            # 但连续几天都是 warning 就说明对账其实一直没跑成——那必须看得见
            logger.warning("退款账单拉取失败，跳过 date=%s", bill_date)
            continue

        order_nos = parse_refund_bill(text)
        if not order_nos:
            continue

        async with pool.connection() as conn:
            for order_no in order_nos:
                if await _sync_one(conn, order_no):
                    synced += 1
                    # error 级别：这说明有人绕过了后台退款，是要被看见的运营事件。
                    # 走后台按钮退的单在这里会被跳过（状态已经是 refunded）
                    logger.error(
                        "对账发现系统外退款，已同步 order_no=%s（账单日 %s）。"
                        "退款的正常入口是后台的退款按钮，商户平台操作不会回写本系统",
                        order_no,
                        bill_date,
                    )

    if synced:
        logger.warning("退款对账完成，同步了 %d 笔系统外退款", synced)
    return synced

"""订单号 —— 取号与环境前缀。

单独成模块，是因为**下单**和**对账**都要它，而那两个模块不该互相 import：
shop_orders.py 发号，shop_reconcile.py 判断账单里某个号是不是本环境的。

## 为什么前缀必须按环境分

微信支付**没有沙箱**——开发环境与生产环境用的是同一个商户号、同一套密钥
（deploy/dev/.env.example 里写明了这一点）。而订单号的序号取自 shop_order_seq 表，
两个环境是**两个独立的库**（antony_casa / antony_casa_dev），各自从 1 开始发号，
于是同一天必然发出同名的 out_trade_no——可 out_trade_no 在**商户维度**必须唯一。

撞号之后有三条破坏路径，都不需要任何人操作失误：

  1. **关单串台**  超时扫描调 close_order(order_no)，关掉的是商户下那个号，
     也就是**另一个环境**里正被客户支付的那一单
  2. **退款串台**  每日对账下载的是**商户级**账单，按单号 UPDATE，
     开发环境退一笔测试单，生产上同号的单会被自动置成已退款
  3. **下单串台**  用已被对方占用的号下单，微信要么报单号重复，
     要么返回绑定到对方那笔订单的 prepay

前缀是唯一能根治的做法。靠「开发环境不配支付」的约定挡不住：.env 里填了就生效，
而那正是开发环境要验支付时必须做的事。

前缀只有两位、且只允许大写字母，与 schema.sql 的 shop_orders_no_format
（`^[A-Z]{2}[0-9]{14}$`）逐字对应。
"""

import os
import re
from datetime import date
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# 默认 AX = 生产。开发环境在 .env 里配 AD。
#
# 与其他配置项不同，这一项**缺了就用默认值**而不是关掉功能：订单号是每一笔单都要
# 的东西，没有「不配就不发号」这个选项。默认值取生产的那个，是因为生产的 .env
# 是照着 deploy/.env.example 写的，那份里明确列了这一项；真正需要改的是开发环境。
ORDER_NO_PREFIX = os.getenv("ORDER_NO_PREFIX", "AX").strip().upper()

# 导入期就校验，不等到第一笔单。配错了的话每一次下单都会撞库上的 CHECK 变成 500，
# 而那个报错指向的是 INSERT，完全看不出根因在一个环境变量上。
# 这与 db.py 对 DATABASE_URL 的处理是同一个取舍：让它起不来，别让它错着跑。
if not re.fullmatch(r"[A-Z]{2}", ORDER_NO_PREFIX):
    raise RuntimeError(
        f"ORDER_NO_PREFIX 必须是两位大写字母（生产 AX、开发 AD），当前是：{ORDER_NO_PREFIX!r}"
    )


def is_ours(order_no: str) -> bool:
    """这个订单号是不是本环境发的。

    对账时用来过滤**商户级**账单：账单里既有本环境的单，也有另一个环境的单
    （同一个商户号），只按「以字母开头」判断会把对方的退款同步到自己身上。
    """
    return order_no.startswith(ORDER_NO_PREFIX)


async def next_order_no(conn: psycopg.AsyncConnection) -> str:
    """取一个订单号：前缀 + YYYYMMDD + 6 位序号。

    单条语句原子取号。冲突分支里的 UPDATE 会持有该行的行锁直到事务结束，
    并发下自然串行，不需要应用层加锁，也不需要 advisory lock。

    day 从 RETURNING 里拿而不是用 Python 的 date.today()：日期必须和序号
    出自同一次判断，否则跨零点的那一刻会算出「昨天的日期 + 今天的序号」，
    而那个组合可能已经被用过了。
    """
    row = await (
        await conn.execute(
            """
            INSERT INTO shop_order_seq (day, next) VALUES (CURRENT_DATE, 1)
            ON CONFLICT (day) DO UPDATE SET next = shop_order_seq.next + 1
            RETURNING day, next
            """
        )
    ).fetchone()
    day: date = row["day"]
    return f"{ORDER_NO_PREFIX}{day:%Y%m%d}{row['next']:06d}"

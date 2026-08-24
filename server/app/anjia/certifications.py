"""企业认证这件事的全部数据逻辑。

这里**没有路由**：小程序侧的提交接口在 app/anjia/users.py，后台侧的审核接口在
app/anjia/admin.py，两边都从这里取数据函数。这么分是为了不出现循环 import——
用户信息接口要带上认证状态，审核接口又要按用户查人，两个方向都要用同一批查询。

模型上最要紧的一点：认证是**记录**不是状态。一个用户可以有多条（驳回后改完重交、
撤销后再申请各是一条新记录），构成完整的申请历史。「是不是企业认证账号」是查出来
的——有一条 approved 就是——用户表上没有对应的冗余列，撤销时也就不存在「忘了改
第二个地方」这回事。
"""

import logging

from psycopg import AsyncConnection

from ..snowflake import next_id

logger = logging.getLogger(__name__)

# 与 schema.anjia.sql 的 CHECK 约束逐字一致
PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
REVOKED = "revoked"

COLUMNS = """
    id, user_id, company_name, contact_name, contact_phone,
    status, reject_reason, reviewed_by, reviewed_at, created_at
"""


def to_json(row: dict) -> dict:
    """一条认证记录出接口的样子。"""
    return {
        # 雪花 ID 有 18 位，超过 JS 的 Number.MAX_SAFE_INTEGER，出接口一律转字符串
        "id": str(row["id"]),
        "status": row["status"],
        "companyName": row["company_name"],
        "contactName": row["contact_name"],
        "contactPhone": row["contact_phone"],
        "rejectReason": row["reject_reason"],
        "submittedAt": row["created_at"].isoformat(),
        "reviewedAt": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
    }


async def latest(conn: AsyncConnection, user_id: int) -> dict | None:
    """这个人最新的一条申请。决定他在小程序里看到什么。"""
    return await (
        await conn.execute(
            f"""
            SELECT {COLUMNS}
              FROM company_certifications
             WHERE user_id = %s
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            (user_id,),
        )
    ).fetchone()


async def approved(conn: AsyncConnection, user_id: int) -> dict | None:
    """这个人生效中的那条认证。决定他是不是企业认证账号。

    不复用 latest()：通过之后确实不可能再产生更新的记录（要再申请必须先被撤销，
    而撤销会把这行改掉），但那是**当下业务规则的推论**，不是数据模型的保证。
    照着推论写，将来加一条「已认证也能提交改名申请」的规则，企业账号就会在
    提交的瞬间悄悄变回个人账号，而且没有任何一处报错。
    """
    return await (
        await conn.execute(
            f"SELECT {COLUMNS} FROM company_certifications"
            " WHERE user_id = %s AND status = %s",
            (user_id, APPROVED),
        )
    ).fetchone()


async def identity(conn: AsyncConnection, user_id: int) -> dict:
    """用户信息接口里与企业认证有关的那几个字段。

    accountType 是**推导**出来的，不是存出来的；companyName 取生效认证的公司全称，
    没有认证时是空串——小程序据此决定展示公司名还是微信昵称，撤销后自动落回，
    不需要任何补偿逻辑。
    """
    live = await approved(conn, user_id)
    newest = await latest(conn, user_id)
    return {
        "accountType": "company" if live else "personal",
        "companyName": live["company_name"] if live else "",
        # 历史记录不出小程序接口，只出最新这一条
        "certification": to_json(newest) if newest else None,
    }


async def has_pending(conn: AsyncConnection, user_id: int) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM company_certifications WHERE user_id = %s AND status = %s",
            (user_id, PENDING),
        )
    ).fetchone()
    return row is not None


async def get(conn: AsyncConnection, cert_id: int) -> dict | None:
    return await (
        await conn.execute(
            f"SELECT {COLUMNS} FROM company_certifications WHERE id = %s", (cert_id,)
        )
    ).fetchone()


async def set_status(
    conn: AsyncConnection,
    cert_id: int,
    expected: str,
    new_status: str,
    reviewed_by: str,
    reject_reason: str = "",
) -> dict | None:
    """改一条记录的状态，只在它当前正处于 expected 时才改。

    状态写在 WHERE 里而不是先查再改：管理员开着两个标签页各点一次，两条请求会同时
    到，先查再改会让两条都通过检查、后一条把前一条的审核结果覆盖掉。放进 WHERE，
    第二条自然改不到行，调用方据此回 409。

    返回 None 有两种可能（记录不存在 / 状态不符），调用方再查一次区分——这是低频的
    错误路径，多一次查询换一句准确的话是划算的。
    """
    return await (
        await conn.execute(
            f"""
            UPDATE company_certifications
               SET status        = %s,
                   reject_reason = %s,
                   reviewed_by   = %s,
                   reviewed_at   = now()
             WHERE id = %s AND status = %s
            RETURNING {COLUMNS}
            """,
            (new_status, reject_reason, reviewed_by, cert_id, expected),
        )
    ).fetchone()


async def insert(
    conn: AsyncConnection,
    user_id: int,
    company_name: str,
    contact_name: str,
    contact_phone: str,
) -> dict:
    """落一条新的待审申请。旧记录一律保留，这是申请历史的来源。"""
    return await (
        await conn.execute(
            f"""
            INSERT INTO company_certifications
              (id, user_id, company_name, contact_name, contact_phone, status)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING {COLUMNS}
            """,
            (next_id(), user_id, company_name, contact_name, contact_phone, PENDING),
        )
    ).fetchone()

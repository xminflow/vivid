"""安家立业的企业认证：提交、审核、撤销、重提的整条链。

用户侧与后台侧放在同一个文件里，因为它们是同一条流程的两端——把「提交」和
「审核」拆到两个文件，任何一条端到端的用例都得跨文件读才看得懂。

后台侧用的超管请求头来自 tests/conftest.py 的 auth_headers。
"""

import psycopg
import pytest

from app.anjia import db as anjia_db
from app.db import pool as antony_pool
from app.snowflake import next_id

from .conftest import auth, login

pytestmark = pytest.mark.skipif(
    not anjia_db.configured(), reason="没配 DATABASE_URL_ANJIA，安家立业的接口没注册"
)

GOOD = {"companyName": "杭州安家立业设计有限公司", "contactName": "李明", "contactPhone": "13800138000"}


async def submit(client, headers, **overrides):
    return await client.post(
        "/api/anjia/certifications", headers=headers, json={**GOOD, **overrides}
    )


async def submit_ok(client, headers, **overrides) -> dict:
    r = await submit(client, headers, **overrides)
    assert r.status_code == 200, r.text
    return r.json()["user"]


# ---------------------------------------------------------------- 提交


async def test_new_user_is_a_personal_account(client):
    body = await login(client)
    assert body["user"]["accountType"] == "personal"
    assert body["user"]["companyName"] == ""
    assert body["user"]["certification"] is None


async def test_submit_puts_you_in_review(client):
    headers = await auth(client)
    user = await submit_ok(client, headers)

    assert user["certification"]["status"] == "pending"
    assert user["certification"]["companyName"] == GOOD["companyName"]
    assert user["certification"]["rejectReason"] == ""
    # 提交不等于通过：审核前仍然是个人账号
    assert user["accountType"] == "personal"
    assert user["companyName"] == ""


async def test_submission_survives_a_reload(client):
    headers = await auth(client)
    await submit_ok(client, headers)

    r = await client.get("/api/anjia/users/me", headers=headers)
    assert r.json()["user"]["certification"]["status"] == "pending"


async def test_bad_phone_is_rejected(client):
    headers = await auth(client)
    r = await submit(client, headers, contactPhone="12345")
    assert r.status_code == 400
    assert r.json()["ok"] is False


async def test_blank_company_name_is_rejected(client):
    headers = await auth(client)
    r = await submit(client, headers, companyName=" ")
    assert r.status_code == 400


async def test_cannot_submit_twice_while_pending(client):
    headers = await auth(client)
    await submit_ok(client, headers)

    r = await submit(client, headers, companyName="另一家公司有限公司")
    assert r.status_code == 409

    # 库里仍然只有那一条
    async with anjia_db.get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT company_name FROM company_certifications WHERE status = 'pending'"
            )
        ).fetchall()
    assert [r["company_name"] for r in rows] == [GOOD["companyName"]]


async def test_submit_requires_login(client):
    r = await client.post("/api/anjia/certifications", json=GOOD)
    assert r.status_code == 401


async def test_certification_is_per_user(client):
    mine = await auth(client, "1")
    theirs = await auth(client, "2")
    await submit_ok(client, mine)

    r = await client.get("/api/anjia/users/me", headers=theirs)
    assert r.json()["user"]["certification"] is None


async def test_membership_is_not_required_to_submit(client):
    """认证与会员正交：没开通会员照样能提交认证。"""
    headers = await auth(client)
    user = await submit_ok(client, headers)
    assert user["isMember"] is False


# ---------------------------------------------------------------- 库层约束


async def test_database_forbids_two_approved_rows(client):
    """「一个账号最多一条生效认证」由部分唯一索引保证，不由应用层保证。

    所以这条直接打到库上：绕过接口插两条 approved，第二条必须被数据库拒绝。
    测应用层等于没测——应用层的先查再写在并发下本来就会漏。
    """
    body = await login(client)
    user_id = int(body["user"]["id"])

    async def insert(status_value: str):
        async with anjia_db.get_pool().connection() as conn:
            await conn.execute(
                """
                INSERT INTO company_certifications
                  (id, user_id, company_name, contact_name, contact_phone, status)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (next_id(), user_id, "某某公司有限公司", "李明", "13800138000", status_value),
            )

    await insert("approved")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert("approved")


async def test_database_forbids_two_pending_rows(client):
    body = await login(client)
    user_id = int(body["user"]["id"])

    async def insert():
        async with anjia_db.get_pool().connection() as conn:
            await conn.execute(
                """
                INSERT INTO company_certifications
                  (id, user_id, company_name, contact_name, contact_phone)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (next_id(), user_id, "某某公司有限公司", "李明", "13800138000"),
            )

    await insert()
    with pytest.raises(psycopg.errors.UniqueViolation):
        await insert()


async def test_writes_to_anjia_database(client):
    """认证记录落在安家立业的库，安东尼之家的库里没有这张表的数据。

    两个池连错库不抛异常，只会安静地写到别人家，所以正反两边都断言。
    """
    headers = await auth(client)
    user = await submit_ok(client, headers)
    user_id = int(user["id"])

    async with anjia_db.get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT status FROM company_certifications WHERE user_id = %s", (user_id,)
            )
        ).fetchone()
    assert row["status"] == "pending"

    async with antony_pool.connection() as conn:
        leaked = await (
            await conn.execute(
                "SELECT to_regclass('public.company_certifications') AS t"
            )
        ).fetchone()
    assert leaked["t"] is None, "企业认证表建到了安东尼之家的库里"


# ---------------------------------------------------------------- 后台审核


async def cert_id(client, headers) -> str:
    """提交一份申请，返回它的 id。后台侧的用例都从这里起步。"""
    user = await submit_ok(client, headers)
    return user["certification"]["id"]


async def review(client, admin, cert, action, **body):
    return await client.post(
        f"/api/admin/anjia/certifications/{cert}/{action}", headers=admin, json=body
    )


async def me(client, headers) -> dict:
    r = await client.get("/api/anjia/users/me", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["user"]


async def test_queue_lists_the_pending_application(client, auth_headers):
    headers = await auth(client)
    await submit_ok(client, headers)

    r = await client.get(
        "/api/admin/anjia/certifications?status=pending", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    mine = [i for i in body["items"] if i["companyName"] == GOOD["companyName"]]
    assert len(mine) == 1
    row = mine[0]
    # 审核时要判断「这是不是一个刚注册来蹭认证的空号」，所以申请人的情况得一起来，
    # 不能让前端再发一次请求
    assert row["contactPhone"] == GOOD["contactPhone"]
    assert "userCreatedAt" in row
    assert row["isMember"] is False
    assert isinstance(row["userId"], str)


async def test_queue_can_search_by_company_name(client, auth_headers):
    headers = await auth(client)
    await submit_ok(client, headers)

    r = await client.get(
        "/api/admin/anjia/certifications?keyword=安家立业设计", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert any(i["companyName"] == GOOD["companyName"] for i in r.json()["items"])

    r = await client.get(
        "/api/admin/anjia/certifications?keyword=绝不可能有这家公司", headers=auth_headers
    )
    assert all(i["companyName"] != GOOD["companyName"] for i in r.json()["items"])


async def test_queue_requires_an_admin(client):
    r = await client.get("/api/admin/anjia/certifications")
    assert r.status_code == 401


async def test_approve_makes_a_company_account(client, auth_headers):
    headers = await auth(client)
    cert = await cert_id(client, headers)

    r = await review(client, auth_headers, cert, "approve")
    assert r.status_code == 200, r.text

    user = await me(client, headers)
    assert user["accountType"] == "company"
    assert user["companyName"] == GOOD["companyName"]
    assert user["certification"]["status"] == "approved"


async def test_approve_records_who_did_it(client, auth_headers):
    headers = await auth(client)
    cert = await cert_id(client, headers)
    await review(client, auth_headers, cert, "approve")

    async with anjia_db.get_pool().connection() as conn:
        row = await (
            await conn.execute(
                "SELECT reviewed_by, reviewed_at FROM company_certifications WHERE id = %s",
                (int(cert),),
            )
        ).fetchone()
    assert row["reviewed_by"]
    assert row["reviewed_at"] is not None


async def test_reject_needs_a_reason(client, auth_headers):
    headers = await auth(client)
    cert = await cert_id(client, headers)

    r = await review(client, auth_headers, cert, "reject", reason="  ")
    assert r.status_code == 400

    # 驳回没成，记录还在待审
    user = await me(client, headers)
    assert user["certification"]["status"] == "pending"


async def test_reject_shows_the_reason_to_the_user(client, auth_headers):
    headers = await auth(client)
    cert = await cert_id(client, headers)

    r = await review(client, auth_headers, cert, "reject", reason="公司名称与联系人不符")
    assert r.status_code == 200, r.text

    user = await me(client, headers)
    assert user["certification"]["status"] == "rejected"
    assert user["certification"]["rejectReason"] == "公司名称与联系人不符"
    assert user["accountType"] == "personal"


async def test_rejected_user_can_resubmit_and_history_is_kept(client, auth_headers):
    headers = await auth(client)
    cert = await cert_id(client, headers)
    await review(client, auth_headers, cert, "reject", reason="名称不完整")

    user = await submit_ok(client, headers, companyName="杭州安家立业设计集团有限公司")
    assert user["certification"]["status"] == "pending"
    assert user["certification"]["companyName"] == "杭州安家立业设计集团有限公司"

    # 旧的那条没被覆盖——管理员据此看得出这个人改过公司名
    async with anjia_db.get_pool().connection() as conn:
        rows = await (
            await conn.execute(
                "SELECT status FROM company_certifications WHERE user_id = %s"
                " ORDER BY created_at",
                (int(user["id"]),),
            )
        ).fetchall()
    assert [r["status"] for r in rows] == ["rejected", "pending"]


async def test_revoke_sends_you_back_to_personal(client, auth_headers):
    headers = await auth(client)
    cert = await cert_id(client, headers)
    await review(client, auth_headers, cert, "approve")

    r = await review(client, auth_headers, cert, "revoke")
    assert r.status_code == 200, r.text

    user = await me(client, headers)
    assert user["accountType"] == "personal"
    assert user["companyName"] == ""
    assert user["certification"]["status"] == "revoked"


async def test_revoke_leaves_membership_alone(client, auth_headers):
    """认证与会员正交：撤销认证不能顺手把会员也撤了。"""
    headers = await auth(client)
    await client.post("/api/anjia/users/me/membership", headers=headers)
    cert = await cert_id(client, headers)
    await review(client, auth_headers, cert, "approve")
    await review(client, auth_headers, cert, "revoke")

    user = await me(client, headers)
    assert user["isMember"] is True


async def test_revoked_user_can_apply_again_immediately(client, auth_headers):
    headers = await auth(client)
    cert = await cert_id(client, headers)
    await review(client, auth_headers, cert, "approve")
    await review(client, auth_headers, cert, "revoke")

    user = await submit_ok(client, headers)
    assert user["certification"]["status"] == "pending"


async def test_approved_user_cannot_resubmit(client, auth_headers):
    """通过后不能自行改公司名，要改得联系运营。"""
    headers = await auth(client)
    cert = await cert_id(client, headers)
    await review(client, auth_headers, cert, "approve")

    r = await submit(client, headers, companyName="换一个名字有限公司")
    assert r.status_code == 409


async def test_actions_reject_wrong_states(client, auth_headers):
    """开着两个标签页的管理员点第二次，该得到一句明确的话，不是把状态改坏。"""
    headers = await auth(client)
    cert = await cert_id(client, headers)

    # 待审的不能撤销
    assert (await review(client, auth_headers, cert, "revoke")).status_code == 409

    await review(client, auth_headers, cert, "approve")
    # 已通过的不能再通过、也不能驳回
    assert (await review(client, auth_headers, cert, "approve")).status_code == 409
    assert (
        await review(client, auth_headers, cert, "reject", reason="反悔了")
    ).status_code == 409


async def test_unknown_certification_is_404(client, auth_headers):
    r = await review(client, auth_headers, "1234567890123456", "approve")
    assert r.status_code == 404


async def test_a_plain_admin_can_review(client, auth_headers, plain_admin):
    """审核不是超管专属。卡在超管身上只会让审核积压。"""
    headers = await auth(client)
    cert = await cert_id(client, headers)

    r = await review(client, plain_admin, cert, "approve")
    assert r.status_code == 200, r.text
    assert (await me(client, headers))["accountType"] == "company"

"""安家立业的案例：上传、发布、审核、首页流、详情与浏览计数。

小程序侧与后台侧放在同一个文件里，因为它们是同一条流程的两端——「发布」和
「审核」拆到两个文件的话，任何一条端到端的用例都得跨文件读才看得懂
（同 tests/anjia/test_certifications.py 的取舍）。

第一刀只做到「发布 → 审核 → 上首页」，所以这里没有编辑、删除、下架的用例：
那几个接口还不存在，为它们先写测试只会写出一堆猜测。
"""

import pytest

from app import cos
from app.anjia import cases
from app.anjia import db as anjia_db

from .conftest import auth

pytestmark = pytest.mark.skipif(
    not anjia_db.configured(), reason="没配 DATABASE_URL_ANJIA，安家立业的接口没注册"
)

CERT = {
    "companyName": "杭州安家立业设计有限公司",
    "contactName": "李明",
    "contactPhone": "13800138000",
}


def jpeg(width: int, height: int) -> bytes:
    """一段最小但**合法**的 JPEG：SOI + SOF0 + EOI。

    不能像 tests/test_home_media.py 那样用 `b"\\xff\\xd8\\xff\\xe0" + 一串零`：
    那个只够骗过按文件头判类型的 sniff_image_ext，而案例图这条链路还要真的从
    SOF 段里读出宽高来，读不到就按「图片已损坏」拒收。
    """
    sof = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")  # 段长包含它自己：2+1+2+2+1+9
        + b"\x08"  # 精度
        + height.to_bytes(2, "big")  # 注意高在前
        + width.to_bytes(2, "big")
        + b"\x03"  # 三个分量
        + b"\x00" * 9
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"


def img_key(width: int = 1200, height: int = 1600, stem: str = "a" * 32) -> str:
    """一个格式正确的案例图对象键。

    发布接口只认键的格式、从键上读宽高，不回头去桶里核对对象在不在——所以发布这
    一侧的用例可以完全不碰 COS。上传那一侧另测。
    """
    return f"static/{cases.SCENE}/20260820/{stem}_{width}x{height}.jpg"


CASE = {"title": "杭州 140㎡ 现代东方", "body": "客厅去掉主灯之后", "images": [img_key()]}


async def make_company(client, auth_headers, code: str = "1") -> dict:
    """造一个企业认证账号，返回它的请求头。

    走的是真接口而不是直接插库：认证通过与否决定发布权，用例要打的正是这条闸，
    绕过它去改库就等于把被测的东西换成了自己造的状态。
    """
    headers = await auth(client, code)
    r = await client.post("/api/anjia/certifications", headers=headers, json=CERT)
    assert r.status_code == 200, r.text
    cert_id = r.json()["user"]["certification"]["id"]

    r = await client.post(
        f"/api/admin/anjia/certifications/{cert_id}/approve", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    return headers


async def publish(client, headers, **overrides):
    return await client.post("/api/anjia/cases", headers=headers, json={**CASE, **overrides})


async def publish_ok(client, headers, **overrides) -> str:
    r = await publish(client, headers, **overrides)
    assert r.status_code == 200, r.text
    return r.json()["case"]["id"]


async def approve(client, auth_headers, case_id: str):
    return await client.post(
        f"/api/admin/anjia/cases/{case_id}/approve", headers=auth_headers
    )


async def live_case(client, auth_headers, headers, **overrides) -> str:
    """发布并过审，返回案例 ID。首页流与详情的用例都从这里起步。"""
    case_id = await publish_ok(client, headers, **overrides)
    r = await approve(client, auth_headers, case_id)
    assert r.status_code == 200, r.text
    return case_id


async def set_status(case_id: str, status_value: str) -> None:
    """把一条案例改回某个状态。

    只用来造第二刀才会由接口产生的局面（比如「作者改完重新提交」）。测的是审核
    接口在那种局面下的行为，不是这条 UPDATE 本身。
    """
    async with anjia_db.get_pool().connection() as conn:
        await conn.execute(
            "UPDATE cases SET status = %s WHERE id = %s", (status_value, int(case_id))
        )


# ---------------------------------------------------------------- 发布权


async def test_personal_account_cannot_publish(client):
    headers = await auth(client)
    r = await publish(client, headers)
    # 403 不是 401：他登录了，只是还不够格——小程序据此弹「去认证」而不是重新登录
    assert r.status_code == 403
    assert "企业认证" in r.json()["message"]


async def test_publishing_requires_login(client):
    r = await client.post("/api/anjia/cases", json=CASE)
    assert r.status_code == 401


async def test_company_account_can_publish(client, auth_headers):
    headers = await make_company(client, auth_headers)
    r = await publish(client, headers)

    assert r.status_code == 200, r.text
    # 提交不等于发布：人工先审后发是需求文档 2.3.1 对首页 UGC 的硬性要求
    assert r.json()["case"]["status"] == "pending"
    assert r.json()["case"]["statusLabel"] == "待审核"


async def test_revoked_certification_blocks_new_posts(client, auth_headers):
    """认证被撤销就发不了新的了——这是 CONTEXT.md 说的「即刻退回个人账号」。"""
    headers = await make_company(client, auth_headers)
    r = await client.get("/api/anjia/users/me", headers=headers)
    cert_id = r.json()["user"]["certification"]["id"]

    await client.post(
        f"/api/admin/anjia/certifications/{cert_id}/revoke", headers=auth_headers
    )
    assert (await publish(client, headers)).status_code == 403


# ---------------------------------------------------------------- 入参


async def test_blank_title_is_rejected(client, auth_headers):
    headers = await make_company(client, auth_headers)
    assert (await publish(client, headers, title="   ")).status_code == 400


async def test_at_least_one_image(client, auth_headers):
    headers = await make_company(client, auth_headers)
    assert (await publish(client, headers, images=[])).status_code == 400


async def test_at_most_nine_images(client, auth_headers):
    headers = await make_company(client, auth_headers)
    keys = [img_key(stem=f"{i:032x}") for i in range(10)]
    assert (await publish(client, headers, images=keys)).status_code == 400


async def test_tampered_image_key_is_rejected(client, auth_headers):
    """键里的宽高被改过，它就指向一个不存在的对象——整条拒收，不是丢掉那一张。

    这条守的是瀑布流：宽高若可由客户端左右，任何人都能报个 1×9999 把自己的卡片
    撑成一整列。键由服务端生成，改一个字符就对不上正则。
    """
    headers = await make_company(client, auth_headers)
    for bad in [
        "static/anjia-case/20260820/aaa_1200x1600.jpg",  # 主体不是 32 位 hex
        "static/home-media/20260820/" + "a" * 32 + "_1200x1600.jpg",  # 运营的首页图
        "uploads/anjia-case/20260820/" + "a" * 32 + "_1200x1600.jpg",  # 私有前缀
        "static/anjia-case/20260820/" + "a" * 32 + ".jpg",  # 没带宽高
        "https://example.com/x.jpg",
    ]:
        r = await publish(client, headers, images=[bad])
        assert r.status_code == 400, f"这个键本该被拒：{bad}"


async def test_image_size_is_read_from_the_key(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, headers, images=[img_key(600, 900)])

    r = await client.get(f"/api/anjia/cases/{case_id}", headers=headers)
    cover = r.json()["case"]["cover"]
    assert (cover["w"], cover["h"]) == (600, 900)


# ---------------------------------------------------------------- 上传


async def test_upload_requires_a_company_account(client, monkeypatch):
    """上传口与发布口挂同一道闸。

    只在发布时校验身份的话，任何一个登录用户都能往公开可读的 static/ 前缀里塞
    东西——那不是内容问题，是在公开目录上开了个可写位。
    """

    async def must_not_be_called(*a, **kw):
        raise AssertionError("没过认证这一关就不该落桶")

    monkeypatch.setattr(cos, "put_object", must_not_be_called)
    headers = await auth(client)
    r = await client.post("/api/anjia/cases/images", headers=headers, content=jpeg(600, 900))
    assert r.status_code == 403


async def test_upload_returns_the_size_it_read(client, auth_headers, monkeypatch):
    """宽高由服务端读文件头得出，并焊进对象键——这是它此后不可篡改的根据。"""
    seen: dict = {}

    async def fake_put(key: str, data: bytes, content_type: str) -> None:
        seen["key"] = key

    monkeypatch.setattr(cos, "put_object", fake_put)
    headers = await make_company(client, auth_headers)

    r = await client.post(
        "/api/anjia/cases/images", headers=headers, content=jpeg(1200, 1600)
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["w"], body["h"]) == (1200, 1600)
    assert body["key"].endswith("_1200x1600.jpg")
    assert body["key"].startswith(f"static/{cases.SCENE}/")
    assert seen["key"] == body["key"]
    # 上传出来的键必须能被发布接口认回来，否则这条链路是断的
    assert cases.image_key_size(body["key"]) == (1200, 1600)


async def test_upload_rejects_a_non_image(client, auth_headers, monkeypatch):
    async def must_not_be_called(*a, **kw):
        raise AssertionError("不是图片就不该落桶")

    monkeypatch.setattr(cos, "put_object", must_not_be_called)
    headers = await make_company(client, auth_headers)

    r = await client.post("/api/anjia/cases/images", headers=headers, content=b"not an image")
    assert r.status_code == 400


async def test_upload_rejects_a_broken_header(client, auth_headers, monkeypatch):
    """文件头认得出是 JPEG，却读不出宽高——收下去只会在首页塌出一个洞。"""

    async def must_not_be_called(*a, **kw):
        raise AssertionError("读不出宽高就不该落桶")

    monkeypatch.setattr(cos, "put_object", must_not_be_called)
    headers = await make_company(client, auth_headers)

    r = await client.post(
        "/api/anjia/cases/images", headers=headers, content=b"\xff\xd8\xff\xe0" + b"\x00" * 64
    )
    assert r.status_code == 400
    assert "损坏" in r.json()["message"]


async def test_upload_rejects_an_oversized_body(client, auth_headers, monkeypatch):
    async def must_not_be_called(*a, **kw):
        raise AssertionError("超限就不该落桶")

    monkeypatch.setattr(cos, "put_object", must_not_be_called)
    monkeypatch.setattr(cases, "MAX_IMAGE_BYTES", 32)
    headers = await make_company(client, auth_headers)

    r = await client.post(
        "/api/anjia/cases/images", headers=headers, content=jpeg(600, 900) + b"\x00" * 200
    )
    assert r.status_code == 413


# ---------------------------------------------------------------- 首页流


async def test_feed_hides_unreviewed_cases(client, auth_headers):
    """没过审的不在前台露面，一秒都不行——这就是「先审后发」的全部含义。"""
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)

    r = await client.get("/api/anjia/cases", headers=headers)
    assert case_id not in [item["id"] for item in r.json()["items"]]


async def test_feed_shows_approved_cases(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, headers)

    r = await client.get("/api/anjia/cases", headers=headers)
    item = next(i for i in r.json()["items"] if i["id"] == case_id)
    assert item["title"] == CASE["title"]
    # 作者展示名取生效认证的公司全称
    assert item["author"]["name"] == CERT["companyName"]
    assert item["cover"]["w"] == 1200


async def test_detail_is_404_before_approval(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)

    r = await client.get(f"/api/anjia/cases/{case_id}", headers=headers)
    assert r.status_code == 404


async def test_rejected_case_stays_off_the_feed(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)
    await client.post(
        f"/api/admin/anjia/cases/{case_id}/reject",
        headers=auth_headers,
        json={"reason": "图片模糊"},
    )

    r = await client.get("/api/anjia/cases", headers=headers)
    assert case_id not in [item["id"] for item in r.json()["items"]]
    assert (await client.get(f"/api/anjia/cases/{case_id}", headers=headers)).status_code == 404


async def test_cursor_pages_without_repeating(client, auth_headers):
    """游标分页不用 offset：内容流一直有新条目插到最前，offset 翻页会重复或漏条。"""
    headers = await make_company(client, auth_headers)
    mine = [await live_case(client, auth_headers, headers) for _ in range(3)]

    first = await client.get("/api/anjia/cases?size=2", headers=headers)
    assert first.status_code == 200, first.text
    page1 = first.json()
    assert len(page1["items"]) == 2
    assert page1["nextCursor"]

    second = await client.get(
        f"/api/anjia/cases?size=2&cursor={page1['nextCursor']}", headers=headers
    )
    ids1 = [i["id"] for i in page1["items"]]
    ids2 = [i["id"] for i in second.json()["items"]]
    assert not set(ids1) & set(ids2)
    # 最新的在前：三条里最后过审的那条排在第一页首位
    assert ids1[0] == mine[-1]
    assert set(mine) <= set(ids1 + ids2)


async def test_a_bad_cursor_is_an_error_not_page_one(client, auth_headers):
    """游标是我们自己发出去的。传错时静默当成第一页，用户就会莫名其妙回到顶部。"""
    headers = await make_company(client, auth_headers)
    r = await client.get("/api/anjia/cases?cursor=nonsense", headers=headers)
    assert r.status_code == 400


async def test_published_case_survives_a_revoked_certification(client, auth_headers):
    """认证被撤销，已发布的案例照旧挂在首页（Q8 的取舍，见 CONTEXT.md「企业认证账号」）。

    撤销针对的是账号资格，不是已过审的内容——那些案例是人工审过的。要清掉得由
    运营逐条下架。若这里改成 JOIN 而不是 LEFT JOIN，它们会在撤销的瞬间集体消失。
    """
    headers = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, headers)

    r = await client.get("/api/anjia/users/me", headers=headers)
    cert_id = r.json()["user"]["certification"]["id"]
    await client.post(
        f"/api/admin/anjia/certifications/{cert_id}/revoke", headers=auth_headers
    )

    r = await client.get("/api/anjia/cases", headers=headers)
    assert case_id in [item["id"] for item in r.json()["items"]]


# ---------------------------------------------------------------- 浏览量


async def test_views_count_each_person_once(client, auth_headers):
    """views 的语义是「多少**人**看过」，不是「被点开多少次」（CONTEXT.md「浏览量」）。"""
    author = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, author)
    reader = await auth(client, "2")

    first = await client.get(f"/api/anjia/cases/{case_id}", headers=reader)
    assert first.json()["case"]["views"] == 1
    second = await client.get(f"/api/anjia/cases/{case_id}", headers=reader)
    assert second.json()["case"]["views"] == 1


async def test_the_author_does_not_count(client, auth_headers):
    """不然作者每天点开看一眼就能刷榜——在只有十几个企业账号的阶段这不是假设。"""
    author = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, author)

    r = await client.get(f"/api/anjia/cases/{case_id}", headers=author)
    assert r.json()["case"]["views"] == 0


async def test_two_readers_count_twice(client, auth_headers):
    author = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, author)

    await client.get(f"/api/anjia/cases/{case_id}", headers=await auth(client, "2"))
    r = await client.get(f"/api/anjia/cases/{case_id}", headers=await auth(client, "3"))
    assert r.json()["case"]["views"] == 2


# ---------------------------------------------------------------- 后台审核


async def test_queue_lists_the_pending_case(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)

    r = await client.get("/api/admin/anjia/cases?status=pending", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    item = next(i for i in body["items"] if i["id"] == case_id)
    assert item["author"]["companyName"] == CERT["companyName"]
    # 图片给全，不只给封面：只给封面的话运营点「通过」时其实没看过后面那几张
    assert len(item["images"]) == len(CASE["images"])
    # 四个分页的数量一次给全，前端不为一个角标再发一次请求
    assert set(body["counts"]) == {"pending", "published", "rejected", "delisted"}
    assert body["counts"]["pending"] >= 1


async def test_queue_requires_an_admin(client):
    assert (await client.get("/api/admin/anjia/cases")).status_code == 401


async def test_queue_can_search_by_title(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers, title="独一无二的标题关键词")

    r = await client.get("/api/admin/anjia/cases?keyword=独一无二", headers=auth_headers)
    assert case_id in [i["id"] for i in r.json()["items"]]


async def test_approve_puts_it_on_the_feed(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)

    r = await approve(client, auth_headers, case_id)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    r = await client.get(f"/api/admin/anjia/cases/{case_id}", headers=auth_headers)
    assert r.json()["case"]["publishedAt"]
    assert r.json()["case"]["reviewedBy"]


async def test_reject_needs_a_reason(client, auth_headers):
    """作者看到的就是这句话。没有它，他只能靠反复提交去猜。"""
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)

    for body in [{}, {"reason": "   "}]:
        r = await client.post(
            f"/api/admin/anjia/cases/{case_id}/reject", headers=auth_headers, json=body
        )
        assert r.status_code == 400, r.text


async def test_reject_records_the_reason(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)

    r = await client.post(
        f"/api/admin/anjia/cases/{case_id}/reject",
        headers=auth_headers,
        json={"reason": "图片模糊", "note": "第三张看不清楚"},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/admin/anjia/cases/{case_id}", headers=auth_headers)
    case = r.json()["case"]
    assert case["status"] == "rejected"
    assert case["rejectReason"] == "图片模糊"
    assert case["rejectNote"] == "第三张看不清楚"


async def test_admin_detail_shows_rejected_cases(client, auth_headers):
    """后台不限状态。处理完就再也打不开自己处理过的东西，运营没法复核。"""
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)
    await client.post(
        f"/api/admin/anjia/cases/{case_id}/reject",
        headers=auth_headers,
        json={"reason": "图片模糊"},
    )
    assert (
        await client.get(f"/api/admin/anjia/cases/{case_id}", headers=auth_headers)
    ).status_code == 200


async def test_second_review_loses_the_race(client, auth_headers):
    """两个管理员各开一个标签页同时点，后一条不能把前一条的结果覆盖掉。

    这就是那道乐观锁：期望状态写在 UPDATE 的 WHERE 里，第二条自然改不到行。
    """
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)

    assert (await approve(client, auth_headers, case_id)).status_code == 200
    second = await approve(client, auth_headers, case_id)
    assert second.status_code == 409
    assert "已发布" in second.json()["message"]


async def test_reject_after_approve_is_refused(client, auth_headers):
    headers = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, headers)

    r = await client.post(
        f"/api/admin/anjia/cases/{case_id}/reject",
        headers=auth_headers,
        json={"reason": "想想还是不行"},
    )
    assert r.status_code == 409


async def test_unknown_case_is_404(client, auth_headers):
    assert (await approve(client, auth_headers, "1234567890123456")).status_code == 404


async def test_a_plain_admin_can_review(client, auth_headers, plain_admin):
    """内容审核刻意没有收紧到超管：它是日常高频动作，卡在一个人身上只会积压。

    用超管测这条永远是绿的，哪天权限被误改成 current_super 也发现不了。
    """
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)
    assert (await approve(client, plain_admin, case_id)).status_code == 200


async def test_reapproval_keeps_the_original_position(client, auth_headers):
    """作者改完错别字重新过审，不该重新抢一次首页头部的位置。

    published_at 用 COALESCE 而不是无条件 now()，就是为了这个。造局面用的是直接
    改库（编辑接口在第二刀），但被测的是审核接口在那个局面下的行为。
    """
    headers = await make_company(client, auth_headers)
    case_id = await live_case(client, auth_headers, headers)
    r = await client.get(f"/api/admin/anjia/cases/{case_id}", headers=auth_headers)
    first_published_at = r.json()["case"]["publishedAt"]

    await set_status(case_id, cases.PENDING)
    assert (await approve(client, auth_headers, case_id)).status_code == 200

    r = await client.get(f"/api/admin/anjia/cases/{case_id}", headers=auth_headers)
    assert r.json()["case"]["publishedAt"] == first_published_at


async def test_approve_clears_a_stale_reject_reason(client, auth_headers):
    """否则作者会在「我的发布」里看到一条「已发布」，却挂着一句「图片模糊」。"""
    headers = await make_company(client, auth_headers)
    case_id = await publish_ok(client, headers)
    await client.post(
        f"/api/admin/anjia/cases/{case_id}/reject",
        headers=auth_headers,
        json={"reason": "图片模糊", "note": "第三张看不清楚"},
    )

    await set_status(case_id, cases.PENDING)
    assert (await approve(client, auth_headers, case_id)).status_code == 200

    r = await client.get(f"/api/admin/anjia/cases/{case_id}", headers=auth_headers)
    assert r.json()["case"]["rejectReason"] == ""
    assert r.json()["case"]["rejectNote"] == ""

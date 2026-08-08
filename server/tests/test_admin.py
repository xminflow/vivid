"""后台列表接口测试。需要库可连，跑完会清掉自己造的数据。

造数一律走 POST 接口而不是直接 INSERT：这样测的是「用户提交进来的数据后台能不能查到」
这条完整链路，字段名或校验改了测试会跟着红。
"""

from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app import cos
from app.db import pool
from app.main import app

TEST_PHONE_PREFIX = "1355"
FUTURE = date.today() + timedelta(days=30)


def appointment(**overrides) -> dict:
    base = {
        "name": "测试用户",
        "phone": "13551000001",
        "visitorType": "设计师",
        "visitDate": FUTURE.isoformat(),
        "partySize": 2,
        "purpose": "展厅参观",
        "note": "",
        "spaceId": "sala",
    }
    base.update(overrides)
    return base


def application(**overrides) -> dict:
    base = {
        "serviceId": "design",
        "name": "测试客户",
        "phone": "13551000001",
        "fields": {"projectType": "住宅项目", "area": "120"},
        "images": {},
    }
    base.update(overrides)
    return base


async def clean() -> None:
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM appointments WHERE phone LIKE %s", (f"{TEST_PHONE_PREFIX}%",)
        )
        await conn.execute(
            "DELETE FROM service_applications WHERE phone LIKE %s", (f"{TEST_PHONE_PREFIX}%",)
        )


@pytest.fixture
async def client(auth_headers):
    await clean()
    transport = ASGITransport(app=app)
    # 头挂在 client 上，所有请求都带。小程序那几个公开接口也会带上这个头，
    # 但它们查的是 users.token，对不上就当没登录，行为不变
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers
    ) as c:
        yield c
    await clean()


def mine(body: dict) -> list[dict]:
    """只看本次测试造的数据。库里有真实数据，断言不能建立在「一共几条」上。"""
    return [it for it in body["items"] if it["phone"].startswith(TEST_PHONE_PREFIX)]


# ---------------------------------------------------------------------------
# 展厅预约


async def test_appointment_shows_up_in_admin_list(client):
    r = await client.post("/api/appointments", json=appointment(name="列表校验"))
    assert r.status_code == 201, r.text

    r = await client.get("/api/admin/appointments", params={"keyword": "13551000001"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    items = mine(body)
    assert len(items) == 1
    item = items[0]
    # 出接口是驼峰，与小程序侧一致
    assert item["name"] == "列表校验"
    assert item["visitorType"] == "设计师"
    assert item["visitDate"] == FUTURE.isoformat()
    assert item["spaceId"] == "sala"
    assert item["status"] == "new"
    # client 带的是后台超管的 Bearer 头，不是小程序用户 token（后者走
    # /api/auth/login 发的、查的是 users.token），current_user_or_none 查不到
    # 匹配的用户就吞掉异常返回 None，所以这条提交照样没有归属人。真正的匿名
    # 提交路径（完全不带 Authorization 头）由 test_users.py 守着
    assert item["userId"] is None


async def test_pagination_keeps_total_stable_across_pages(client):
    for i in range(3):
        r = await client.post(
            "/api/appointments",
            json=appointment(phone=f"1355100000{i + 1}", name=f"翻页{i}"),
        )
        assert r.status_code == 201, r.text

    first = await client.get(
        "/api/admin/appointments", params={"keyword": "翻页", "pageSize": 2, "page": 1}
    )
    assert first.status_code == 200
    assert first.json()["total"] == 3
    assert len(first.json()["items"]) == 2

    second = await client.get(
        "/api/admin/appointments", params={"keyword": "翻页", "pageSize": 2, "page": 2}
    )
    assert second.status_code == 200
    # 翻到最后一页时总数不能变成 0，否则前端分页器会跳回第一页
    assert second.json()["total"] == 3
    assert len(second.json()["items"]) == 1

    ids = {it["id"] for it in first.json()["items"]} | {it["id"] for it in second.json()["items"]}
    assert len(ids) == 3


async def test_page_beyond_range_is_empty_but_total_is_right(client):
    await client.post("/api/appointments", json=appointment(name="越界"))
    r = await client.get("/api/admin/appointments", params={"keyword": "越界", "page": 99})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"] == []


async def test_filters_narrow_the_result(client):
    await client.post(
        "/api/appointments",
        json=appointment(phone="13551000001", visitorType="设计师", purpose="展厅参观"),
    )
    await client.post(
        "/api/appointments",
        json=appointment(phone="13551000002", visitorType="艺术圈", purpose="商务合作"),
    )

    r = await client.get(
        "/api/admin/appointments", params={"keyword": "1355100000", "visitorType": "艺术圈"}
    )
    items = mine(r.json())
    assert [it["phone"] for it in items] == ["13551000002"]

    r = await client.get(
        "/api/admin/appointments", params={"keyword": "1355100000", "purpose": "展厅参观"}
    )
    items = mine(r.json())
    assert [it["phone"] for it in items] == ["13551000001"]

    # 新提交的都是 new，筛已到店应该一条都没有
    r = await client.get(
        "/api/admin/appointments", params={"keyword": "1355100000", "status": "visited"}
    )
    assert mine(r.json()) == []


async def test_visit_date_range_filters_on_visit_date(client):
    soon = date.today() + timedelta(days=1)
    later = date.today() + timedelta(days=60)
    await client.post(
        "/api/appointments", json=appointment(phone="13551000001", visitDate=soon.isoformat())
    )
    await client.post(
        "/api/appointments", json=appointment(phone="13551000002", visitDate=later.isoformat())
    )

    r = await client.get(
        "/api/admin/appointments",
        params={"keyword": "1355100000", "visitDateTo": soon.isoformat()},
    )
    assert [it["phone"] for it in mine(r.json())] == ["13551000001"]

    r = await client.get(
        "/api/admin/appointments",
        params={"keyword": "1355100000", "visitDateFrom": later.isoformat()},
    )
    assert [it["phone"] for it in mine(r.json())] == ["13551000002"]


async def test_keyword_wildcards_are_literal(client):
    await client.post("/api/appointments", json=appointment(name="百分百满意"))
    # 不转义的话 '%' 会匹配到库里所有人
    r = await client.get("/api/admin/appointments", params={"keyword": "%"})
    assert r.status_code == 200
    assert mine(r.json()) == []


async def test_bad_query_params_are_rejected(client):
    r = await client.get("/api/admin/appointments", params={"visitorType": "路人"})
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False

    r = await client.get("/api/admin/appointments", params={"status": "已到店"})
    assert r.status_code == 400

    # 单页上限挡住，否则一次能把全部客户手机号拉走
    r = await client.get("/api/admin/appointments", params={"pageSize": 1000})
    assert r.status_code == 400

    r = await client.get("/api/admin/appointments", params={"page": 0})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 服务申请


async def test_service_application_fields_come_back_as_submitted(client):
    r = await client.post("/api/service-applications", json=application())
    assert r.status_code == 201, r.text

    r = await client.get(
        "/api/admin/service-applications", params={"keyword": "13551000001"}
    )
    assert r.status_code == 200, r.text
    items = mine(r.json())
    assert len(items) == 1
    item = items[0]
    assert item["serviceId"] == "design"
    # 各服务的表单字段整体存 jsonb，后台按字段 id 原样拿到
    assert item["fields"] == {"projectType": "住宅项目", "area": "120"}
    assert item["status"] == "new"
    assert item["images"] == {}


async def test_service_id_filter(client):
    await client.post("/api/service-applications", json=application(serviceId="design"))
    await client.post(
        "/api/service-applications",
        json=application(serviceId="resale", phone="13551000002"),
    )

    r = await client.get(
        "/api/admin/service-applications",
        params={"keyword": "1355100000", "serviceId": "resale"},
    )
    items = mine(r.json())
    assert [it["serviceId"] for it in items] == ["resale"]

    r = await client.get(
        "/api/admin/service-applications", params={"keyword": "1355100000"}
    )
    assert len(mine(r.json())) == 2


async def test_images_carry_key_and_signed_url(client):
    key = "uploads/test/admin-list-check.jpg"
    r = await client.post(
        "/api/service-applications", json=application(images={"floorplan": [key]})
    )
    assert r.status_code == 201, r.text

    r = await client.get(
        "/api/admin/service-applications", params={"keyword": "13551000001"}
    )
    images = mine(r.json())[0]["images"]
    assert list(images) == ["floorplan"]
    assert images["floorplan"][0]["key"] == key
    if cos.configured():
        assert images["floorplan"][0]["url"].startswith("http")
    else:
        # COS 没配时给 null，而不是把整组图悄悄丢掉
        assert images["floorplan"][0]["url"] is None


async def test_created_range_includes_today(client):
    await client.post("/api/service-applications", json=application())
    today = date.today().isoformat()
    r = await client.get(
        "/api/admin/service-applications",
        params={"keyword": "13551000001", "createdFrom": today, "createdTo": today},
    )
    assert len(mine(r.json())) == 1

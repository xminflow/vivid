"""接口测试。需要 antony_casa 库可连，跑完会清掉自己造的数据。"""

from datetime import date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import pool
from app.main import app
from app.models import PURPOSES, VISITOR_TYPES

FUTURE = (date.today() + timedelta(days=30)).isoformat()
PAST = (date.today() - timedelta(days=1)).isoformat()
TEST_PHONE_PREFIX = "1355"


def form(**overrides) -> dict:
    base = {
        "name": "测试用户",
        "phone": "13550000001",
        "visitorType": "设计师",
        "visitDate": FUTURE,
        "visitTime": "14:30",
        "partySize": 2,
        "purpose": "展厅参观",
        "note": "",
        "spaceId": "sala",
    }
    base.update(overrides)
    return base


@pytest.fixture
async def client():
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM appointments WHERE phone LIKE %s", (f"{TEST_PHONE_PREFIX}%",))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM appointments WHERE phone LIKE %s", (f"{TEST_PHONE_PREFIX}%",))


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


async def test_submit_ok(client):
    r = await client.post("/api/appointments", json=form())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["ok"] is True
    assert isinstance(body["id"], int)


async def test_phone_must_be_a_mobile_number(client):
    r = await client.post("/api/appointments", json=form(phone="123"))
    assert r.status_code == 400
    assert r.json()["message"] == "电话格式不正确"


async def test_visitor_type_must_be_one_of_the_options(client):
    r = await client.post("/api/appointments", json=form(visitorType="路人"))
    assert r.status_code == 400
    assert r.json()["message"] == "请选择来者身份"


async def test_purpose_must_be_one_of_the_options(client):
    r = await client.post("/api/appointments", json=form(purpose="随便看看"))
    assert r.status_code == 400
    assert r.json()["message"] == "请选择预约需求"


async def test_visit_date_cannot_be_in_the_past(client):
    r = await client.post("/api/appointments", json=form(visitDate=PAST))
    assert r.status_code == 400
    assert r.json()["message"] == "到访日期不能早于今天"


async def test_visit_time_is_optional(client):
    """服务端先于小程序上线的那几天，旧版本提交的表单没有这一项，必须照收。"""
    payload = form(phone="13550000004")
    payload.pop("visitTime")
    r = await client.post("/api/appointments", json=payload)
    assert r.status_code == 201, r.text


async def test_visit_time_must_be_a_time(client):
    r = await client.post("/api/appointments", json=form(visitTime="下午两点"))
    assert r.status_code == 400
    assert r.json()["message"] == "请选择到访时间"


async def test_visit_time_today_cannot_be_in_the_past(client):
    """日期那道只比到「天」，今天已经过去的钟点要靠这一道拦下。"""
    r = await client.post(
        "/api/appointments",
        json=form(phone="13550000005", visitDate=date.today().isoformat(), visitTime="00:00"),
    )
    assert r.status_code == 400
    assert r.json()["message"] == "到访时间已经过了，请重新选择"


async def test_visit_time_today_in_the_future_is_accepted(client):
    """23:59 之后跑这条会没有「今天还没到的时刻」可用，那时跳过——
    这条测的是校验放行，不是营业时段。"""
    if datetime.now().hour >= 23:
        pytest.skip("当前时刻之后今天已无可选时间")
    later = f"{datetime.now().hour + 1:02d}:00"
    r = await client.post(
        "/api/appointments",
        json=form(phone="13550000006", visitDate=date.today().isoformat(), visitTime=later),
    )
    assert r.status_code == 201, r.text


async def test_party_size_out_of_range(client):
    r = await client.post("/api/appointments", json=form(partySize=99))
    assert r.status_code == 400
    assert r.json()["message"] == "到访人数需在 1 至 50 之间"


async def test_name_is_required(client):
    r = await client.post("/api/appointments", json=form(name="   "))
    assert r.status_code == 400
    assert r.json()["message"] == "请填写您的称呼"


async def test_same_phone_same_day_is_rejected(client):
    first = await client.post("/api/appointments", json=form(phone="13550000002"))
    assert first.status_code == 201
    again = await client.post("/api/appointments", json=form(phone="13550000002", purpose="商务合作"))
    assert again.status_code == 409
    assert "已有预约" in again.json()["message"]


async def test_note_is_optional_and_space_id_may_be_absent(client):
    payload = form(phone="13550000003")
    payload.pop("note")
    payload.pop("spaceId")
    r = await client.post("/api/appointments", json=payload)
    assert r.status_code == 201


async def test_every_option_combination_is_accepted(client):
    n = 0
    for vt in VISITOR_TYPES:
        for pp in PURPOSES:
            n += 1
            r = await client.post(
                "/api/appointments",
                json=form(phone=f"{TEST_PHONE_PREFIX}{n:07d}", visitorType=vt, purpose=pp),
            )
            assert r.status_code == 201, f"{vt} / {pp} -> {r.status_code} {r.text}"
    assert n == 36


# 列表接口现在是后台的 GET /api/admin/appointments，覆盖在 test_admin.py

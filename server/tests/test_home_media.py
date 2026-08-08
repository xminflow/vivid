"""首页配图：后台写、小程序读。需要库可连。

`home_media` 是一张**全局配置表**，不像预约那样能靠手机号前缀把测试数据圈出来。
所以这里在整个测试会话开始时把表整个备份下来、结束时原样写回：本机的库里可能
已经配了真实的首页图，测试不能把它洗掉（见 conftest 里的 preserve_home_media）。
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app import cos
from app.db import pool
from app.main import app
from app.models import HOME_SLOT_MAX

# 测试用的对象键。不真传文件，只验从对象键往后的落库、排序和出参
HERO_KEYS = [
    "static/home-media/20260101/testhero1.jpg",
    "static/home-media/20260101/testhero2.jpg",
]
ACTIVITY_KEY = "static/home-media/20260101/testactivity.jpg"

# 一段最小的合法 JPEG 头，够 sniff_image_ext 认出来
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture(scope="session", autouse=True)
async def preserve_home_media(db):
    """把库里原有的首页配图备份出来，测试跑完原样还回去。"""
    async with pool.connection() as conn:
        saved = await (
            await conn.execute(
                "SELECT id, slot, image_key, sort_order FROM home_media ORDER BY slot, sort_order"
            )
        ).fetchall()

    yield

    async with pool.connection() as conn:
        await conn.execute("DELETE FROM home_media")
        if saved:
            await conn.cursor().executemany(
                """
                INSERT INTO home_media (id, slot, image_key, sort_order)
                VALUES (%(id)s, %(slot)s, %(image_key)s, %(sort_order)s)
                """,
                saved,
            )


async def wipe() -> None:
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM home_media")


@pytest.fixture
async def client():
    await wipe()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await wipe()


# ---------------------------------------------------------------------------
# 小程序读


async def test_empty_config_falls_back_to_bundled_defaults(client):
    """没配过时三个位都是空的——小程序据此用包内默认图，首页不会开天窗。"""
    r = await client.get("/api/home")
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["ok"] is True
    assert body["hero"] == []
    assert body["showroom"] == []
    assert body["activity"] is None
    assert body["updatedAt"] is None


@pytest.mark.skipif(not cos.configured(), reason="没配 COS 时接口按约定返回空配置")
async def test_saved_hero_shows_up_in_order(client):
    r = await client.put("/api/admin/home-media/hero", json={"keys": HERO_KEYS})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "slot": "hero", "count": 2}

    body = (await client.get("/api/home")).json()
    # 出参是公开直链，顺序必须与提交时的数组一致
    assert body["hero"] == [cos.object_url(key) for key in HERO_KEYS]
    assert body["updatedAt"] is not None


@pytest.mark.skipif(not cos.configured(), reason="没配 COS 时接口按约定返回空配置")
async def test_reordering_takes_effect(client):
    await client.put("/api/admin/home-media/hero", json={"keys": HERO_KEYS})
    await client.put("/api/admin/home-media/hero", json={"keys": list(reversed(HERO_KEYS))})

    body = (await client.get("/api/home")).json()
    assert body["hero"] == [cos.object_url(key) for key in reversed(HERO_KEYS)]


@pytest.mark.skipif(not cos.configured(), reason="没配 COS 时接口按约定返回空配置")
async def test_activity_is_a_single_url_not_a_list(client):
    await client.put("/api/admin/home-media/activity", json={"keys": [ACTIVITY_KEY]})

    body = (await client.get("/api/home")).json()
    assert body["activity"] == cos.object_url(ACTIVITY_KEY)


async def test_clearing_a_slot_restores_the_empty_state(client):
    await client.put("/api/admin/home-media/hero", json={"keys": HERO_KEYS})

    r = await client.put("/api/admin/home-media/hero", json={"keys": []})
    assert r.status_code == 200, r.text

    assert (await client.get("/api/home")).json()["hero"] == []


async def test_slots_are_independent(client):
    """改一个位置不能把别的位置一起清掉——整组替换只作用于自己的 slot。"""
    await client.put("/api/admin/home-media/hero", json={"keys": HERO_KEYS})
    await client.put("/api/admin/home-media/activity", json={"keys": [ACTIVITY_KEY]})
    await client.put("/api/admin/home-media/hero", json={"keys": []})

    slots = (await client.get("/api/admin/home-media")).json()["slots"]
    assert slots["hero"] == []
    assert [it["key"] for it in slots["activity"]] == [ACTIVITY_KEY]


# ---------------------------------------------------------------------------
# 后台读


async def test_admin_list_carries_keys_and_limits(client):
    await client.put("/api/admin/home-media/hero", json={"keys": HERO_KEYS})

    body = (await client.get("/api/admin/home-media")).json()
    assert body["ok"] is True
    assert body["limits"] == HOME_SLOT_MAX
    assert [it["key"] for it in body["slots"]["hero"]] == HERO_KEYS
    # 三个位置都要出现，哪怕是空的：后台据此渲染三个上传区
    assert set(body["slots"]) == set(HOME_SLOT_MAX)

    first = body["slots"]["hero"][0]
    # 雪花 ID 超出 JS 安全整数范围，必须是字符串
    assert isinstance(first["id"], str)
    if cos.configured():
        assert first["url"].startswith("http")
    else:
        # 同服务申请的取舍：宁可「有图但显示不出来」，也不能看起来像没配过
        assert first["url"] is None


# ---------------------------------------------------------------------------
# 写入校验


async def test_rejects_keys_outside_static_prefix(client):
    """只有 static/ 下的键能进首页。uploads/ 是客户上传的私有图，不能放上首页。"""
    r = await client.put(
        "/api/admin/home-media/hero", json={"keys": ["uploads/avatar/20260101/x.jpg"]}
    )
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False


async def test_rejects_path_traversal(client):
    r = await client.put("/api/admin/home-media/hero", json={"keys": ["static/../secret.jpg"]})
    assert r.status_code == 400, r.text


async def test_rejects_duplicate_keys_in_one_slot(client):
    """小程序的 wx:for 拿图片地址当 wx:key，重复会让节点复用错位。"""
    r = await client.put("/api/admin/home-media/hero", json={"keys": [HERO_KEYS[0]] * 2})
    assert r.status_code == 400, r.text


async def test_rejects_unknown_slot(client):
    """位置是路径参数且被 Literal 卡死，写错的 slot 进不到查库那一步。

    校验失败统一被 main.py 的 handler 转成 400 + 中文提示，不是 FastAPI 默认的 422。
    """
    r = await client.put("/api/admin/home-media/banner", json={"keys": []})
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False


async def test_rejects_more_images_than_the_slot_allows(client):
    keys = [f"static/home-media/20260101/over{i}.jpg" for i in range(HOME_SLOT_MAX["hero"] + 1)]
    r = await client.put("/api/admin/home-media/hero", json={"keys": keys})
    assert r.status_code == 400, r.text
    assert "最多" in r.json()["message"]


async def test_activity_takes_only_one_poster(client):
    """首页是整张海报铺开、不是轮播，第二张会被接口挡下（库上也有唯一索引）。"""
    r = await client.put(
        "/api/admin/home-media/activity",
        json={"keys": [ACTIVITY_KEY, "static/home-media/20260101/second.jpg"]},
    )
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------------------
# 上传


async def test_upload_rejects_non_image_bytes(client, monkeypatch):
    """按文件头判类型：改个扩展名混进来的非图片必须被挡在落桶之前。"""

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("非图片不该被写进 COS")

    monkeypatch.setattr(cos, "put_object", must_not_be_called)

    r = await client.post("/api/admin/home-media/upload", content=b"%PDF-1.4 not an image")
    assert r.status_code == 400, r.text
    assert "JPG" in r.json()["message"]


async def test_upload_rejects_empty_body(client):
    r = await client.post("/api/admin/home-media/upload", content=b"")
    assert r.status_code == 400, r.text


async def test_upload_rejects_oversized_body(client, monkeypatch):
    """上限要在读完之前就生效，不能先把整个请求收进内存再说太大了。"""

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("超限的请求不该被写进 COS")

    monkeypatch.setattr(cos, "put_object", must_not_be_called)
    monkeypatch.setattr(cos, "MAX_UPLOAD_BYTES", 1024)

    r = await client.post("/api/admin/home-media/upload", content=JPEG_BYTES + b"\x00" * 2048)
    assert r.status_code == 413, r.text


async def test_upload_returns_a_static_key(client, monkeypatch):
    """上传只返回键，不写库——运营点了保存才算数。"""
    written: dict = {}

    async def fake_put(key: str, data: bytes, content_type: str) -> None:
        written.update(key=key, size=len(data), content_type=content_type)

    monkeypatch.setattr(cos, "put_object", fake_put)

    r = await client.post("/api/admin/home-media/upload", content=JPEG_BYTES)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["key"].startswith("static/home-media/")
    assert body["key"].endswith(".jpg")
    assert written["key"] == body["key"]
    assert written["content_type"] == "image/jpeg"
    assert written["size"] == len(JPEG_BYTES)

    # 没点保存，首页配置不该有任何变化
    assert (await client.get("/api/home")).json()["hero"] == []

"""雪花 ID 的机器号解析与生成。

不连库，纯单元测试。机器号解析是这里的重点：多实例拿到同一个机器号会算出
重复主键，而线上是弹性扩容，靠人工配置保证不了唯一。
"""

import pytest

from app.snowflake import MAX_WORKER_ID, Snowflake, resolve_worker_id


def test_显式配置优先(monkeypatch):
    monkeypatch.setenv("WORKER_ID", "7")
    assert resolve_worker_id() == 7


def test_显式配置为非法值时直接报错(monkeypatch):
    """不静默回落到主机名推导：配了却不生效比直接失败更难查。"""
    monkeypatch.setenv("WORKER_ID", "abc")
    with pytest.raises(ValueError):
        resolve_worker_id()


def test_空字符串按未配置处理(monkeypatch):
    """容器平台常把未填的变量注入成空串，不能当成 int('') 崩掉。"""
    monkeypatch.setenv("WORKER_ID", "   ")
    monkeypatch.setattr("socket.gethostname", lambda: "vivid-server-abc123-x9k2p")
    assert 0 <= resolve_worker_id() <= MAX_WORKER_ID


def test_未配置时按主机名推导且稳定(monkeypatch):
    """同一实例重启后必须还是同一个机器号，否则重启期间可能与别的实例撞上。"""
    monkeypatch.delenv("WORKER_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "vivid-server-abc123-x9k2p")

    first = resolve_worker_id()
    assert 0 <= first <= MAX_WORKER_ID
    assert resolve_worker_id() == first


def test_不同主机名分散到不同机器号(monkeypatch):
    """碰撞不可能为零（1024 个槽），但同批实例名只差后缀时不该扎堆。"""
    monkeypatch.delenv("WORKER_ID", raising=False)

    ids = set()
    for suffix in range(50):
        monkeypatch.setattr("socket.gethostname", lambda s=suffix: f"vivid-server-7c9d-{s:05d}")
        ids.add(resolve_worker_id())

    # 50 个名字压进 1024 个槽，理论期望碰撞约 1.2 个，留足余量
    assert len(ids) >= 45


def test_同一实例生成的_id_递增且不重复():
    gen = Snowflake(3)
    ids = [gen.next_id() for _ in range(5000)]
    assert len(set(ids)) == 5000
    assert ids == sorted(ids)


def test_不同机器号同时生成不会撞():
    """这正是 WORKER_ID 要逐实例唯一的原因：机器号一样，同毫秒会算出同一个 id。"""
    a, b = Snowflake(1), Snowflake(2)
    ids_a = {a.next_id() for _ in range(1000)}
    ids_b = {b.next_id() for _ in range(1000)}
    assert not (ids_a & ids_b)


def test_机器号越界直接拒绝():
    with pytest.raises(ValueError):
        Snowflake(MAX_WORKER_ID + 1)

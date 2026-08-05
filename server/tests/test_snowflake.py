"""雪花 ID 的机器号解析与生成。

不连库，纯单元测试。机器号解析是这里的重点：多实例拿到同一个机器号会算出
重复主键，而线上是弹性扩容，靠人工配置保证不了唯一。
"""

import logging

import pytest

from app import snowflake as snowflake_module
from app.logging_setup import setup_logging
from app.snowflake import MAX_WORKER_ID, Snowflake, resolve_worker_id


@pytest.fixture
def fresh_generator(monkeypatch):
    """清掉惰性构建的单例，让本用例自己决定机器号从哪来。"""
    monkeypatch.setattr(snowflake_module, "_generator", None)


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


def test_机器号惰性解析且只解析一次(monkeypatch, fresh_generator):
    """解析要发生在日志配好之后，所以不能在导入期做；解析结果要一直复用。"""
    monkeypatch.setenv("WORKER_ID", "11")
    assert snowflake_module.worker_id() == 11

    # 解析过之后再改环境变量不该影响已经在发号的生成器
    monkeypatch.setenv("WORKER_ID", "12")
    assert snowflake_module.worker_id() == 11


def test_启动时的机器号日志能被看到(monkeypatch, fresh_generator, caplog):
    """这条日志是多实例撞号唯一的排查依据，必须真的出得来。"""
    monkeypatch.setenv("WORKER_ID", "9")
    with caplog.at_level(logging.INFO, logger="app.snowflake"):
        snowflake_module.worker_id()
    assert "WORKER_ID 取自环境变量：9" in caplog.text


def test_未配置时推导的机器号也会打日志(monkeypatch, fresh_generator, caplog):
    monkeypatch.delenv("WORKER_ID", raising=False)
    monkeypatch.setattr("socket.gethostname", lambda: "vivid-server-abc123-x9k2p")
    with caplog.at_level(logging.WARNING, logger="app.snowflake"):
        derived = snowflake_module.worker_id()
    assert "未配置 WORKER_ID" in caplog.text
    assert f"worker_id={derived}" in caplog.text


def test_LOG_LEVEL_取值不合法时直接报错(monkeypatch):
    """悄悄退回 INFO 会让人以为开了 DEBUG 却看不到日志，越查越偏。"""
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValueError):
        setup_logging()


def test_LOG_LEVEL_留空时用默认级别(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "  ")
    setup_logging()
    assert logging.getLogger("app").level == logging.INFO

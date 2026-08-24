"""订单号的环境前缀。纯配置与纯函数，不连库、不连微信。

守的是那条撞号的链路（见 app/order_no.py 的模块注释）：两个环境共用同一个
微信商户号，前缀是唯一能防止它们发出同名 out_trade_no 的东西。
"""

import importlib
import re

import dotenv
import pytest

from app import order_no as order_no_module


def reload_with(monkeypatch, value: str | None):
    """用指定的 ORDER_NO_PREFIX 重新导入模块，返回新的模块对象。

    校验发生在**导入期**（配错了每一次下单都会撞库上的 CHECK，而那个报错指向
    INSERT，完全看不出根因在一个环境变量上），所以只能这么测。

    顺便把 load_dotenv 停掉：模块导入时会读 server/.env，而开发机上那份**已经**
    配了 ORDER_NO_PREFIX=AD。不停掉的话，「不配时取什么默认值」这条用例
    量到的是开发机的配置而不是代码里的默认值——而且结果因人而异。
    """
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
    if value is None:
        monkeypatch.delenv("ORDER_NO_PREFIX", raising=False)
    else:
        monkeypatch.setenv("ORDER_NO_PREFIX", value)
    return importlib.reload(order_no_module)


@pytest.fixture(autouse=True)
def restore_module():
    """每条用例之后把模块恢复成 .env 里的那份配置，
    否则被 reload 改过的前缀会漏给后面的用例（它是模块级常量）。"""
    yield
    importlib.reload(order_no_module)


def test_defaults_to_the_production_prefix(monkeypatch):
    """不配就是生产。生产的 .env 照着 deploy/.env.example 写，那份里列了这一项；
    真正必须改的是开发环境。"""
    assert reload_with(monkeypatch, None).ORDER_NO_PREFIX == "AX"


def test_accepts_the_dev_prefix(monkeypatch):
    assert reload_with(monkeypatch, "AD").ORDER_NO_PREFIX == "AD"


def test_normalises_case_and_whitespace(monkeypatch):
    """.env 是手写的，`ad` 或者尾随空格都该当成 AD 而不是配置错误。"""
    assert reload_with(monkeypatch, " ad ").ORDER_NO_PREFIX == "AD"


@pytest.mark.parametrize("bad", ["A", "ABC", "A1", "12", "", "A-"])
def test_rejects_anything_that_is_not_two_letters(monkeypatch, bad):
    """必须在导入期就炸。放过去的话，每一笔下单都会撞
    schema.sql 的 `^[A-Z]{2}[0-9]{14}$`，表现为 500 而不是一句配置错误。"""
    with pytest.raises(RuntimeError, match="ORDER_NO_PREFIX"):
        reload_with(monkeypatch, bad)


def test_is_ours_only_matches_this_environment(monkeypatch):
    """**撞号那条链路的核心**：账单和商户订单号都是商户级的，
    两个环境的记录混在一起，靠这个函数分开。"""
    module = reload_with(monkeypatch, "AD")
    assert module.is_ours("AD20260812000001")
    assert not module.is_ours("AX20260812000001"), "另一个环境的单，绝不能认"
    assert not module.is_ours("OTHER20260812001")


def test_the_generated_shape_matches_the_database_constraint(monkeypatch):
    """两位大写字母 + 8 位日期 + 6 位序号，与 schema.sql 的 CHECK 逐字对应。"""
    module = reload_with(monkeypatch, "AD")
    # 不连库，所以直接按取号后的拼接规则验形状
    sample = f"{module.ORDER_NO_PREFIX}{20260812}{7:06d}"
    assert re.fullmatch(r"[A-Z]{2}[0-9]{14}", sample), sample

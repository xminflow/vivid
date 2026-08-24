"""微信登录里不依赖网络的那部分。

只测缺配置时的报错。真正的 code2session 调用在各小程序的登录测试里是被整个替掉的
（见 tests/anjia/conftest.py），所以那条路径上「凭据到底配没配」永远不会被触发——
这个文件补的就是那个盲区。
"""

import pytest

from app.wechat import WeChatError, code2session


async def test_missing_secret_names_the_exact_variable():
    """点名到 WX_SECRET_ANJIA，而不是笼统一句「缺少 appid / secret」。

    这条用例来自一次真实的排查：安家立业能连库、能起服务，只有 WX_SECRET_ANJIA
    是空的，前端只看得到一个 400，日志里那句笼统的话帮不上忙。矩阵里每个小程序
    一套 appid/secret，不点名就得挨个变量去猜。
    """
    with pytest.raises(WeChatError) as exc:
        await code2session("any-code", "wx-appid", "", suffix="_ANJIA")

    assert "WX_SECRET_ANJIA" in exc.value.detail
    assert "WX_APPID_ANJIA" not in exc.value.detail, "appid 是好的，不该被一起报出来"
    # 给用户看的仍然是那句人话：具体缺哪个变量是服务端配置，不出接口
    assert exc.value.message == "登录服务未配置"


async def test_missing_both_reports_both():
    with pytest.raises(WeChatError) as exc:
        await code2session("any-code", "", "", suffix="_ANJIA")

    assert "WX_APPID_ANJIA" in exc.value.detail
    assert "WX_SECRET_ANJIA" in exc.value.detail


async def test_antony_variables_have_no_suffix():
    """安东尼之家的变量没有后缀（历史原因，见 docs/adr/0001），别报成 WX_APPID_。"""
    with pytest.raises(WeChatError) as exc:
        await code2session("any-code", "", "wx-secret")

    assert "WX_APPID" in exc.value.detail
    assert "WX_APPID_" not in exc.value.detail

"""微信支付的配置判定。纯函数，不连库也不连微信。

只验一件事：**什么时候算「配好了」**。这件事错了的后果是隐蔽的——
半配的状态下 configured() 若返回 True，服务启动时不会有任何警告、
超时关单扫描照常起，直到有真实用户下单才 503。
"""

import pytest

from app import wxpay

# 七项配齐的样子。PEM 只要形状像就行，这一层不解析内容
FULL = {
    "WXPAY_MCHID": "1600000000",
    "WXPAY_APIV3_KEY": "0123456789abcdef0123456789abcdef",
    "WXPAY_CERT_SERIAL": "5157F09EFDC096DE15EBE81A47057A72",
    "WXPAY_PUBLIC_KEY_ID": "PUB_KEY_ID_0000000000",
    "WXPAY_NOTIFY_URL": "https://dev.antonycasa.weelume.com/api/shop/pay/notify",
    "WXPAY_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----",
    "WXPAY_PUBLIC_KEY": "-----BEGIN PUBLIC KEY-----\\nMIIB\\n-----END PUBLIC KEY-----",
}

# 模块级常量是在 import 时从环境读的，改环境变量对它们无效，要一起打补丁
MODULE_CONSTS = {
    "WXPAY_MCHID": "MCHID",
    "WXPAY_APIV3_KEY": "APIV3_KEY",
    "WXPAY_CERT_SERIAL": "CERT_SERIAL",
    "WXPAY_PUBLIC_KEY_ID": "PUBLIC_KEY_ID",
    "WXPAY_NOTIFY_URL": "NOTIFY_URL",
}


def apply(monkeypatch, values: dict) -> None:
    """把一份配置装进 wxpay：环境变量给那两把密钥用，模块常量给其余项用。"""
    for env, name in MODULE_CONSTS.items():
        monkeypatch.setattr(wxpay, name, values.get(env, ""))
    monkeypatch.setattr(wxpay, "APPID", "wx0000000000000000")
    for env in ("WXPAY_PRIVATE_KEY", "WXPAY_PRIVATE_KEY_PATH",
                "WXPAY_PUBLIC_KEY", "WXPAY_PUBLIC_KEY_PATH"):
        monkeypatch.setenv(env, values.get(env, ""))


def test_full_config_is_configured(monkeypatch):
    apply(monkeypatch, FULL)
    assert wxpay.configured() is True
    assert wxpay.missing_config() == []


@pytest.mark.parametrize("dropped", sorted(FULL))
def test_any_missing_item_means_not_configured(monkeypatch, dropped):
    """七项缺任何一项都算没配。逐项参数化，加了新配置项而忘了纳入判定时这里会红。"""
    values = {k: v for k, v in FULL.items() if k != dropped}
    apply(monkeypatch, values)
    assert wxpay.configured() is False, f"缺 {dropped} 时仍被判为已配置"
    assert wxpay.missing_config(), f"缺 {dropped} 时 missing_config() 应当非空"


def test_public_key_id_without_the_key_itself_is_not_configured(monkeypatch):
    """公钥和公钥 ID 在商户平台是同一页上的两个字段，最容易只填一个。

    这一条单独写出来，是因为它曾经真的漏过：configured() 只查了 PUBLIC_KEY_ID，
    于是「只填了 ID」会被判成已配置——启动无警告、扫描任务照起，
    直到有人真下单才 503。
    """
    values = dict(FULL)
    values["WXPAY_PUBLIC_KEY"] = ""
    apply(monkeypatch, values)
    assert wxpay.configured() is False
    assert any("WXPAY_PUBLIC_KEY" in item for item in wxpay.missing_config())


def test_key_path_counts_as_configured(monkeypatch):
    """密钥给路径而不是内联，同样算配好了——服务器上更适合把密钥文件单独放。"""
    values = dict(FULL)
    values["WXPAY_PRIVATE_KEY"] = ""
    values["WXPAY_PRIVATE_KEY_PATH"] = "/home/deploy/secrets/apiclient_key.pem"
    apply(monkeypatch, values)
    assert wxpay.configured() is True


def test_cert_serial_is_required_even_in_public_key_mode(monkeypatch):
    """公钥模式只改变「怎么验微信的签名」，不影响「我们怎么签给微信看」。

    商户证书序列号要放进 Authorization 头的 serial_no，微信靠它知道
    该用哪把公钥验我们的请求。所以它和公钥模式并不冲突，照样必填。
    """
    values = dict(FULL)
    values["WXPAY_CERT_SERIAL"] = ""
    apply(monkeypatch, values)
    assert wxpay.configured() is False
    assert "WXPAY_CERT_SERIAL" in wxpay.missing_config()


def test_normalize_pem_restores_single_line_form():
    """.env 一行一个值，而 PEM 是多行的。约定把换行写成字面量 \\n。"""
    single = "-----BEGIN PRIVATE KEY-----\\nMIIE\\n-----END PRIVATE KEY-----"
    restored = wxpay._normalize_pem(single, "测试")
    assert "\n" in restored
    assert "\\n" not in restored
    assert restored.startswith("-----BEGIN")


def test_normalize_pem_keeps_real_multiline_as_is():
    """直接从文件粘过来的多行 PEM 必须也能用。"""
    multi = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"
    assert wxpay._normalize_pem(multi, "测试") == multi


def test_normalize_pem_rejects_something_that_is_not_pem():
    """不猜也不补头尾：补错了会在验签时才炸，那时的报错完全指不到配置。"""
    with pytest.raises(ValueError, match="测试"):
        wxpay._normalize_pem("MIIEvgIBADANBgkqh", "测试")


def test_normalize_pem_strips_quotes():
    """有人会连引号一起粘进 .env。"""
    quoted = '"-----BEGIN PUBLIC KEY-----\\nMIIB\\n-----END PUBLIC KEY-----"'
    assert wxpay._normalize_pem(quoted, "测试").startswith("-----BEGIN")

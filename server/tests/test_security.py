"""密码哈希的单元测试。纯函数，不需要数据库。"""

from app.security import hash_password, verify_password


def test_same_password_hashes_differently_but_both_verify():
    a = hash_password("correct horse")
    b = hash_password("correct horse")
    # 每次盐不同，两条哈希不能一样——一样就说明没加盐，
    # 拖库的人一眼能看出哪些人用了同一个密码
    assert a != b
    assert verify_password("correct horse", a)
    assert verify_password("correct horse", b)


def test_wrong_password_is_rejected():
    stored = hash_password("correct horse")
    assert not verify_password("Correct horse", stored)
    assert not verify_password("correct hors", stored)
    assert not verify_password("", stored)


def test_stored_format_carries_its_own_parameters():
    # 参数存在哈希串里，将来调大 n 时存量密码仍按各自的参数校验，
    # 不需要强制所有人改密码
    parts = hash_password("whatever").split("$")
    assert len(parts) == 6
    assert parts[0] == "scrypt"
    assert parts[1].isdigit()


def test_malformed_stored_value_is_rejected_not_raised():
    # 库里混进格式不对的值时，登录接口该回「密码错误」，不该 500
    for junk in ("", "plaintext", "scrypt$bad", "bcrypt$1$2$3$4$5", "scrypt$a$b$c$d$e"):
        assert not verify_password("x", junk)

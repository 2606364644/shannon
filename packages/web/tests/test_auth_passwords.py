from supernova_web.auth.passwords import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert h.startswith("$2")  # bcrypt
    assert verify_password("s3cret-pass", h) is True


def test_wrong_password_rejected():
    h = hash_password("s3cret-pass")
    assert verify_password("wrong", h) is False


def test_each_hash_unique_salt():
    assert hash_password("same") != hash_password("same")

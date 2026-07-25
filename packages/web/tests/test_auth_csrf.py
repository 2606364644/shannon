from supernova_web.auth.csrf import generate_csrf_token, verify_csrf


def test_generate_unique():
    assert generate_csrf_token() != generate_csrf_token()
    assert len(generate_csrf_token()) > 20


def test_verify_match():
    tok = generate_csrf_token()
    assert verify_csrf(tok, tok) is True


def test_verify_mismatch():
    assert verify_csrf("a", "b") is False


def test_verify_missing():
    assert verify_csrf(None, "x") is False
    assert verify_csrf("x", None) is False
    assert verify_csrf(None, None) is False

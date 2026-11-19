from urllib.parse import parse_qsl, urlsplit

import pytest

from imgresize_api.signing import SignatureError, canonical_query, compute_signature, sign_url, verify

SECRET = b"test-secret-0123456789abcdef"


def params_of(url: str) -> list[tuple[str, str]]:
    return parse_qsl(urlsplit(url).query, keep_blank_values=True)


def test_canonical_query_sorts_and_encodes():
    assert canonical_query([("w", "10"), ("url", "https://a.test/x y.jpg?v=1"), ("s", "ignored")]) == (
        "url=https%3A%2F%2Fa.test%2Fx%20y.jpg%3Fv%3D1&w=10"
    )


def test_known_vector():
    # Published in the README so other languages can check their implementation.
    key = b"my-very-secret-signing-key"
    params = [("w", "300"), ("url", "https://example.com/cat.jpg")]
    assert canonical_query(params) == "url=https%3A%2F%2Fexample.com%2Fcat.jpg&w=300"
    assert compute_signature(key, "/resize", params) == "rmEfEey7svQPEn7A42EFmg3WXV_yv4ZVVQNuQ1ciIT0"


def test_round_trip_and_order_independence():
    signed = sign_url(SECRET, "/resize?w=300&url=https://example.com/a.jpg&format=webp")
    verify(SECRET, "/resize", params_of(signed))
    shuffled = list(reversed(params_of(signed)))
    verify(SECRET, "/resize", shuffled)


def test_full_url_keeps_origin():
    signed = sign_url(SECRET, "https://img.example.com/resize?url=https://example.com/a.jpg&w=10")
    assert signed.startswith("https://img.example.com/resize?url=")
    verify(SECRET, "/resize", params_of(signed))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: [(k, v) for k, v in p if k != "s"], "missing"),
        (lambda p: [(k, "999" if k == "w" else v) for k, v in p], "invalid"),
        (lambda p: [*p, ("h", "100")], "invalid"),
        (lambda p: [*p, ("s", "again")], "more than one"),
    ],
)
def test_rejections(mutate, message):
    signed = params_of(sign_url(SECRET, "/resize?url=https://example.com/a.jpg&w=300"))
    with pytest.raises(SignatureError, match=message):
        verify(SECRET, "/resize", mutate(signed))


def test_wrong_secret_and_path():
    signed = params_of(sign_url(SECRET, "/resize?url=https://example.com/a.jpg"))
    with pytest.raises(SignatureError):
        verify(b"another-secret-0123456789", "/resize", signed)
    with pytest.raises(SignatureError):
        verify(SECRET, "/other", signed)


def test_expiry():
    signed = params_of(sign_url(SECRET, "/resize?url=https://example.com/a.jpg", expires_in=60, now=1_000))
    assert ("expires", "1060") in signed
    verify(SECRET, "/resize", signed, now=1_059)
    with pytest.raises(SignatureError, match="expired"):
        verify(SECRET, "/resize", signed, now=1_061)


def test_bad_expires_value_is_rejected():
    params = [("url", "https://example.com/a.jpg"), ("expires", "soon")]
    params.append(("s", compute_signature(SECRET, "/resize", params)))
    with pytest.raises(SignatureError, match="Unix timestamp"):
        verify(SECRET, "/resize", params)


def test_example_signer_matches_the_server():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "examples" / "sign.py"
    spec = importlib.util.spec_from_file_location("example_sign", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    url = module.sign("my-very-secret-signing-key", {"url": "https://example.com/a b(1)!*'.jpg?x=ü", "w": "300"})
    verify(b"my-very-secret-signing-key", "/resize", params_of(url))

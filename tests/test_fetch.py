import asyncio
from dataclasses import replace

import httpx
import pytest

from conftest import Origin, make_image
from imgresize_api.fetch import Fetcher, FetchError, is_public_address, source_allowed
from imgresize_api.settings import Settings

JPEG = make_image()


def run(coro):
    return asyncio.run(coro)


def fetcher(origin: Origin, **overrides) -> Fetcher:
    settings = replace(Settings(allow_unsigned=True, cache_dir=""), **overrides)
    return Fetcher(settings, transport=origin.transport, resolver=origin.resolve)


async def fetch(origin: Origin, url: str, **overrides):
    f = fetcher(origin, **overrides)
    try:
        return await f.fetch(url)
    finally:
        await f.aclose()


def expect(origin: Origin, url: str, status: int, match: str = "", **overrides) -> FetchError:
    with pytest.raises(FetchError, match=match) as info:
        run(fetch(origin, url, **overrides))
    assert info.value.status == status
    return info.value


@pytest.mark.parametrize(
    "address",
    ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0", "224.0.0.1",
     "255.255.255.255", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::a00:1",
     "2002:0a00:0001::1", "::", "198.18.0.1", "192.0.2.10", "not-an-ip"],
)  # fmt: skip
def test_private_and_reserved_addresses_are_blocked(address):
    assert not is_public_address(address)


@pytest.mark.parametrize("address", ["93.184.215.14", "1.1.1.1", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_public_addresses(address):
    assert is_public_address(address)


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://cdn.example.com/a.jpg", True),
        ("https://CDN.example.com./a.jpg", True),
        ("https://img.assets.test/a.jpg", True),
        ("https://assets.test/a.jpg", False),
        ("https://evilassets.test/a.jpg", False),
        ("https://media.example.org/uploads/2026/a.jpg", True),
        ("https://media.example.org/private/a.jpg", False),
        ("http://media.example.org/uploads/a.jpg", False),
        ("https://example.com/a.jpg", False),
    ],
)
def test_allowlist(url, allowed):
    entries = ("cdn.example.com", "*.assets.test", "https://media.example.org/uploads/")
    assert source_allowed(url, entries) is allowed


def test_empty_allowlist_allows_everything():
    assert source_allowed("https://anything.test/x", ())


def test_fetch_pins_ip_and_keeps_host_and_sni(origin):
    origin.image("images.example.com", "/cat.jpg", JPEG)
    fetched = run(fetch(origin, "https://images.example.com/cat.jpg"))
    assert fetched.content == JPEG
    request = origin.requests[0]
    assert request.url.host == "93.184.215.14"
    assert request.headers["host"] == "images.example.com"
    assert request.extensions["sni_hostname"] == "images.example.com"
    assert request.headers["user-agent"].startswith("imgresize-api/")


@pytest.mark.parametrize(
    ("url", "status", "match"),
    [
        ("ftp://example.com/a.jpg", 400, "scheme"),
        ("file:///etc/passwd", 400, "scheme"),
        ("https://user:pw@example.com/a.jpg", 400, "credentials"),
        ("https:///a.jpg", 400, "no host"),
        ("https://example.com:8443/a.jpg", 403, "port 8443"),
        ("https://example.com:99999/a.jpg", 400, "not valid"),
        ("https://127.0.0.1/a.jpg", 403, "private"),
        ("http://[::1]/a.jpg", 403, "private"),
        ("http://169.254.169.254/latest/meta-data/", 403, "private"),
        ("https://unknown.example/a.jpg", 502, "resolve"),
    ],
)
def test_url_rejections(origin, url, status, match):
    expect(origin, url, status, match)
    assert origin.requests == []


def test_dns_answer_with_any_private_address_is_blocked(origin):
    origin.image("rebind.example", "/a.jpg", JPEG)
    origin.dns["rebind.example"] = ["93.184.215.14", "10.0.0.5"]
    expect(origin, "https://rebind.example/a.jpg", 403, "private")
    assert origin.requests == []


def test_http_can_be_disabled(origin):
    origin.image("example.com", "/a.jpg", JPEG)
    expect(origin, "http://example.com/a.jpg", 400, "https", allow_http=False)


def test_allowlist_enforced(origin):
    origin.image("other.example", "/a.jpg", JPEG)
    expect(origin, "https://other.example/a.jpg", 403, "allowlist", allowed_sources=("cdn.example.com",))


def test_redirect_is_followed_and_rechecked(origin):
    origin.add("a.example", "/start", httpx.Response(302, headers={"location": "https://b.example/final.jpg"}))
    origin.image("b.example", "/final.jpg", JPEG)
    fetched = run(fetch(origin, "https://a.example/start"))
    assert fetched.url == "https://b.example/final.jpg"
    assert fetched.content == JPEG


def test_relative_redirect(origin):
    origin.add("a.example", "/old.jpg", httpx.Response(301, headers={"location": "/new.jpg"}))
    origin.image("a.example", "/new.jpg", JPEG)
    assert run(fetch(origin, "https://a.example/old.jpg")).url == "https://a.example/new.jpg"


@pytest.mark.parametrize(
    "location",
    ["http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data/", "https://internal.example/a.jpg",
     "gopher://a.example/", "https://a.example:2375/containers/json"],
)  # fmt: skip
def test_redirect_to_internal_targets_is_blocked(origin, location):
    origin.add("a.example", "/start", httpx.Response(302, headers={"location": location}))
    origin.dns["internal.example"] = ["192.168.0.10"]
    with pytest.raises(FetchError) as info:
        run(fetch(origin, "https://a.example/start"))
    assert info.value.status in (400, 403)
    assert len(origin.requests) == 1


def test_redirect_outside_allowlist_is_blocked(origin):
    origin.add("cdn.example.com", "/start", httpx.Response(302, headers={"location": "https://evil.example/a.jpg"}))
    origin.image("evil.example", "/a.jpg", JPEG)
    expect(origin, "https://cdn.example.com/start", 403, "allowlist", allowed_sources=("cdn.example.com",))


def test_too_many_redirects(origin):
    origin.add("loop.example", "/a", httpx.Response(302, headers={"location": "/a"}))
    expect(origin, "https://loop.example/a", 502, "too many redirects", max_redirects=2)
    assert len(origin.requests) == 3


def test_redirect_without_location(origin):
    origin.add("a.example", "/x", httpx.Response(302))
    expect(origin, "https://a.example/x", 502, "Location")


def test_upstream_errors(origin):
    origin.add("a.example", "/500", httpx.Response(500))
    expect(origin, "https://a.example/500", 502, "HTTP 500")
    expect(origin, "https://a.example/missing", 404, "not found")


def test_non_image_content_type(origin):
    origin.add("a.example", "/page", httpx.Response(200, text="<html>", headers={"content-type": "text/html"}))
    expect(origin, "https://a.example/page", 415, "not an image")


def test_octet_stream_is_accepted(origin):
    origin.image("a.example", "/blob", JPEG, content_type="application/octet-stream")
    assert run(fetch(origin, "https://a.example/blob")).content == JPEG


def test_declared_size_limit(origin):
    origin.add("a.example", "/big.jpg", httpx.Response(200, content=b"x" * 10, headers={"content-length": "5000000"}))
    expect(origin, "https://a.example/big.jpg", 413, "larger", max_source_bytes=1024 * 1024)


def test_streamed_size_limit_without_content_length(origin):
    async def body():
        for _ in range(100):
            yield b"x" * 4096

    origin.add("a.example", "/stream.jpg", lambda request: httpx.Response(200, content=body()))
    expect(origin, "https://a.example/stream.jpg", 413, "larger", max_source_bytes=64 * 1024)


def test_timeouts_and_network_errors(origin):
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def broken(request):
        raise httpx.ConnectError("refused", request=request)

    origin.add("a.example", "/slow", slow)
    origin.add("a.example", "/broken", broken)
    expect(origin, "https://a.example/slow", 504, "timed out")
    expect(origin, "https://a.example/broken", 502, "ConnectError")


def test_private_networks_can_be_allowed_for_development(origin):
    origin.image("localhost:8000", "/a.jpg", JPEG)
    origin.dns["localhost"] = ["127.0.0.1"]
    fetched = run(fetch(origin, "http://localhost:8000/a.jpg", allow_private_networks=True, allowed_ports=()))
    assert fetched.content == JPEG
    assert origin.requests[0].headers["host"] == "localhost:8000"
    assert "sni_hostname" not in origin.requests[0].extensions


def test_ipv6_literal_is_pinned_with_brackets(origin):
    origin.image("[2606:4700:4700::1111]", "/a.jpg", JPEG)
    fetched = run(fetch(origin, "https://[2606:4700:4700::1111]/a.jpg"))
    assert fetched.content == JPEG
    assert origin.requests[0].url.host == "2606:4700:4700::1111"

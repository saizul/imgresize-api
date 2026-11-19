import io
from dataclasses import replace

import httpx
import pytest
from starlette.testclient import TestClient

from conftest import SECRET, make_image, open_response
from imgresize_api.app import create_app
from imgresize_api.processing import avif_available
from imgresize_api.signing import sign_url

JPEG = make_image((800, 600))
SOURCE = "https://images.example.com/photo.jpg"


@pytest.fixture(autouse=True)
def photo(origin):
    origin.image("images.example.com", "/photo.jpg", JPEG)


def test_resize_remote_image(client, origin):
    resp = client.get("/resize", params={"url": SOURCE, "w": 200, "format": "webp", "q": 70})
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["x-cache"] == "MISS"
    assert resp.headers["cache-control"] == "public, max-age=86400"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-image-size"] == "200x150"
    assert resp.headers["etag"].startswith('"')
    assert "vary" not in resp.headers
    assert open_response(resp).size == (200, 150)


def test_cache_hit_skips_fetch(client, origin):
    params = {"url": SOURCE, "w": 100}
    first = client.get("/resize", params=params)
    second = client.get("/resize", params=params)
    assert second.headers["x-cache"] == "HIT"
    assert second.content == first.content
    assert second.headers["etag"] == first.headers["etag"]
    assert len(origin.requests) == 1


def test_cache_can_be_disabled(make_client, origin):
    client = make_client(cache_dir="")
    client.get("/resize", params={"url": SOURCE, "w": 100})
    client.get("/resize", params={"url": SOURCE, "w": 100})
    assert len(origin.requests) == 2


def test_etag_conditional_request(client):
    first = client.get("/resize", params={"url": SOURCE, "w": 100})
    etag = first.headers["etag"]
    resp = client.get("/resize", params={"url": SOURCE, "w": 100}, headers={"If-None-Match": f'W/"other", {etag}'})
    assert resp.status_code == 304
    assert resp.content == b""
    assert resp.headers["etag"] == etag
    assert (
        client.get("/resize", params={"url": SOURCE, "w": 100}, headers={"If-None-Match": '"nope"'}).status_code == 200
    )


def test_auto_format_varies_on_accept(client):
    webp = client.get(
        "/resize", params={"url": SOURCE, "w": 50, "format": "auto"}, headers={"Accept": "image/webp,*/*"}
    )
    assert webp.headers["content-type"] == "image/webp"
    assert webp.headers["vary"] == "Accept"
    legacy = client.get("/resize", params={"url": SOURCE, "w": 50, "format": "auto"}, headers={"Accept": "*/*"})
    assert legacy.headers["content-type"] == "image/jpeg"
    assert legacy.headers["x-cache"] == "MISS"
    if avif_available():
        avif = client.get(
            "/resize", params={"url": SOURCE, "w": 50, "format": "auto"}, headers={"Accept": "image/avif"}
        )
        assert avif.headers["content-type"] == "image/avif"


@pytest.mark.parametrize(
    ("params", "status", "message"),
    [
        ({}, 400, "missing 'url'"),
        ({"url": SOURCE, "w": "huge"}, 400, "whole number"),
        ({"url": SOURCE, "width": "10"}, 400, "unknown parameter"),
        ({"url": "http://127.0.0.1/x.jpg"}, 403, "private"),
        ({"url": "https://images.example.com/missing.jpg"}, 404, "not found"),
        ({"url": "file:///etc/passwd"}, 400, "scheme"),
    ],
)
def test_errors(client, params, status, message):
    resp = client.get("/resize", params=params)
    assert resp.status_code == status
    assert message in resp.json()["error"]
    assert resp.headers["cache-control"] == "no-store"


def test_duplicate_parameters_rejected(client):
    resp = client.get(f"/resize?url={SOURCE}&w=10&w=20")
    assert resp.status_code == 400
    assert "more than once" in resp.json()["error"]


def test_remote_non_image(client, origin):
    origin.add(
        "images.example.com",
        "/fake.jpg",
        httpx.Response(200, content=b"not an image", headers={"content-type": "image/jpeg"}),
    )
    resp = client.get("/resize", params={"url": "https://images.example.com/fake.jpg"})
    assert resp.status_code == 415


def test_signed_urls_required_when_secret_set(make_client):
    client = make_client(secret=SECRET, allow_unsigned=False)
    unsigned = client.get("/resize", params={"url": SOURCE, "w": 100})
    assert unsigned.status_code == 403
    assert "missing signature" in unsigned.json()["error"]
    signed = sign_url(SECRET, f"/resize?url={SOURCE}&w=100")
    assert client.get(signed).status_code == 200
    tampered = signed.replace("w=100", "w=101")
    assert client.get(tampered).status_code == 403
    expired = sign_url(SECRET, f"/resize?url={SOURCE}&w=100", expires_in=-10)
    assert "expired" in client.get(expired).json()["error"]


def test_upload_raw_body(client):
    resp = client.post(
        "/resize?w=80&format=png", content=make_image((160, 120)), headers={"content-type": "image/jpeg"}
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["cache-control"] == "private, no-store"
    assert open_response(resp).size == (80, 60)


def test_upload_multipart(client):
    files = {"file": ("photo.jpg", make_image((300, 300)), "image/jpeg")}
    resp = client.post("/resize?w=100&h=50&fit=cover", files=files)
    assert resp.status_code == 200
    assert open_response(resp).size == (100, 50)
    again = client.post("/resize?w=100&h=50&fit=cover", files=files)
    assert again.headers["x-cache"] == "HIT"


def test_upload_errors(make_client):
    client = make_client(max_upload_bytes=2048)
    assert client.post("/resize", content=b"").status_code == 400
    assert client.post("/resize", files={"other": ("a.txt", b"hello", "text/plain")}).status_code == 400
    big = client.post("/resize", content=b"x" * (200 * 1024))
    assert big.status_code == 413
    assert client.post(f"/resize?url={SOURCE}", content=make_image((10, 10))).status_code == 400
    assert client.post("/resize", content=b"garbage bytes").status_code == 415
    broken = client.post(
        "/resize", content=b"--x\r\nbroken", headers={"content-type": "multipart/form-data; boundary=x"}
    )
    assert broken.status_code == 400


def test_signed_upload(make_client):
    client = make_client(secret=SECRET, allow_unsigned=False)
    assert client.post("/resize?w=10", content=make_image((20, 20))).status_code == 403
    assert client.post(sign_url(SECRET, "/resize?w=10"), content=make_image((20, 20))).status_code == 200


def test_health(client):
    with client:
        data = client.get("/healthz").json()
    assert data["status"] == "ok"
    assert data["signing"] is False
    assert data["cache"] is True


def test_lifespan_prunes_cache(settings, origin, tmp_path):
    app = create_app(replace(settings, cache_ttl=1), transport=origin.transport, resolver=origin.resolve)
    with TestClient(app) as client:
        assert client.get("/resize", params={"url": SOURCE, "w": 10}).status_code == 200
    assert any((tmp_path / "cache").rglob("*.bin"))


def test_metadata_is_stripped_end_to_end(client, origin):
    from PIL import Image

    image = Image.new("RGB", (100, 100), (1, 2, 3))
    exif = Image.Exif()
    exif[0x010F] = "Camera Maker"
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", exif=exif)
    origin.image("images.example.com", "/exif.jpg", buffer.getvalue())
    resp = client.get("/resize", params={"url": "https://images.example.com/exif.jpg", "w": 50})
    assert b"Camera Maker" not in resp.content
    assert not open_response(resp).getexif()

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import replace

import httpx
import pytest
from PIL import Image
from starlette.testclient import TestClient

from imgresize_api.app import create_app
from imgresize_api.settings import Settings

SECRET = b"test-secret-0123456789abcdef"


def make_image(
    size: tuple[int, int] = (400, 300),
    fmt: str = "JPEG",
    mode: str = "RGB",
    color: tuple[int, ...] = (200, 40, 40),
    **save: object,
) -> bytes:
    image = Image.new(mode, size, color)
    if mode in ("RGB", "RGBA"):
        # A small marker in the top-left corner, to check orientation and cropping.
        marker = (0, 0, 255, 255) if mode == "RGBA" else (0, 0, 255)
        for x in range(size[0] // 4):
            for y in range(size[1] // 4):
                image.putpixel((x, y), marker)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


class Origin:
    """A fake set of remote hosts served through httpx.MockTransport."""

    def __init__(self) -> None:
        self.files: dict[tuple[str, str], httpx.Response | Callable[[httpx.Request], httpx.Response]] = {}
        self.dns: dict[str, list[str]] = {}
        self.requests: list[httpx.Request] = []

    def add(self, host: str, path: str, response: httpx.Response | Callable[[httpx.Request], httpx.Response]) -> None:
        self.files[(host, path)] = response
        self.dns.setdefault(host.split(":")[0], ["93.184.215.14"])

    def image(self, host: str, path: str, data: bytes, content_type: str = "image/jpeg") -> None:
        self.add(host, path, httpx.Response(200, content=data, headers={"content-type": content_type}))

    async def resolve(self, host: str, port: int) -> list[str]:
        if host not in self.dns:
            raise OSError("no such host")
        return self.dns[host]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.headers["host"]
        found = self.files.get((host, request.url.path))
        if found is None:
            return httpx.Response(404)
        return found(request) if callable(found) else found

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture
def origin() -> Origin:
    return Origin()


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(secret=None, allow_unsigned=True, cache_dir=str(tmp_path / "cache"), concurrency=2)


@pytest.fixture
def make_client(origin: Origin, settings: Settings) -> Callable[..., TestClient]:
    def build(**overrides: object) -> TestClient:
        app = create_app(replace(settings, **overrides), transport=origin.transport, resolver=origin.resolve)
        return TestClient(app)

    return build


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()


def open_response(resp: httpx.Response) -> Image.Image:
    return Image.open(io.BytesIO(resp.content))

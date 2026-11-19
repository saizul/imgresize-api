"""Starlette application: ``GET /resize``, ``POST /resize`` and ``GET /healthz``."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from imgresize_api._version import __version__
from imgresize_api.cache import DiskCache, Entry, make_etag, make_key
from imgresize_api.fetch import Fetcher, FetchError, Resolver
from imgresize_api.processing import ImageError, Params, avif_available, negotiate, parse_params, process
from imgresize_api.settings import Settings
from imgresize_api.signing import SignatureError, verify

log = logging.getLogger("imgresize_api")

KNOWN_PARAMS = {"url", "w", "h", "fit", "format", "fm", "q", "dpr", "enlarge", "gravity", "bg", "expires", "s"}
CACHE_KEY_VERSION = 1
PRUNE_EVERY_WRITES = 200


def error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status, headers={"Cache-Control": "no-store"})


class Service:
    def __init__(
        self, settings: Settings, transport: httpx.AsyncBaseTransport | None, resolver: Resolver | None
    ) -> None:
        self.settings = settings
        self.fetcher = Fetcher(settings, transport=transport, resolver=resolver)
        self.cache = (
            DiskCache(settings.cache_dir, settings.cache_ttl, settings.cache_max_bytes)
            if settings.cache_enabled
            else None
        )
        self.semaphore = asyncio.Semaphore(settings.concurrency)
        self.prune_task: asyncio.Future[Any] | None = None

    # -- helpers ------------------------------------------------------------
    def check_signature(self, request: Request) -> None:
        if self.settings.secret is None:
            return
        verify(self.settings.secret, request.url.path, list(request.query_params.multi_items()))

    @staticmethod
    def single_values(request: Request) -> dict[str, str]:
        values: dict[str, str] = {}
        for key, value in request.query_params.multi_items():
            if key in values:
                raise ImageError(400, f"parameter {key!r} is given more than once")
            if key not in KNOWN_PARAMS:
                raise ImageError(400, f"unknown parameter {key!r}")
            values[key] = value
        return values

    def maybe_prune(self) -> None:
        if self.cache is None or self.cache.writes_since_prune < PRUNE_EVERY_WRITES:
            return
        if self.prune_task is not None and not self.prune_task.done():
            return
        self.cache.writes_since_prune = 0
        self.prune_task = asyncio.get_running_loop().run_in_executor(None, self.cache.prune)

    async def render(self, data: bytes, params: Params, negotiated: str | None, key: str) -> Entry:
        async with self.semaphore:
            result = await run_in_threadpool(process, data, params, self.settings, negotiated)
        if self.cache is not None:
            try:
                entry = await run_in_threadpool(
                    self.cache.put, key, result.content, result.content_type, result.width, result.height
                )
                self.maybe_prune()
                return entry
            except OSError as exc:
                log.warning("cache write failed: %s", exc)
        return Entry(
            result.content, result.content_type, make_etag(result.content), time.time(), result.width, result.height
        )

    def respond(self, request: Request, entry: Entry, hit: bool, vary_accept: bool, public: bool) -> Response:
        headers = {
            "ETag": entry.etag,
            "Cache-Control": f"public, max-age={self.settings.browser_max_age}" if public else "private, no-store",
            "X-Cache": "HIT" if hit else "MISS",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        }
        if entry.width and entry.height:
            headers["X-Image-Size"] = f"{entry.width}x{entry.height}"
        if vary_accept:
            headers["Vary"] = "Accept"
        if public and _etag_matches(request.headers.get("if-none-match", ""), entry.etag):
            return Response(status_code=304, headers={k: v for k, v in headers.items() if k != "X-Image-Size"})
        return Response(entry.content, media_type=entry.content_type, headers=headers)

    # -- endpoints ----------------------------------------------------------
    async def resize_url(self, request: Request) -> Response:
        try:
            self.check_signature(request)
        except SignatureError as exc:
            return error(403, str(exc))
        try:
            query = self.single_values(request)
            source = query.get("url", "").strip()
            if not source:
                raise ImageError(400, "missing 'url' parameter (or POST an image to /resize)")
            params = parse_params(query, self.settings)
        except ImageError as exc:
            return error(exc.status, exc.message)

        negotiated = negotiate(request.headers.get("accept", ""), self.settings) if params.format == "auto" else None
        key = make_key(CACHE_KEY_VERSION, "url", source, params.key(), params.format, negotiated)
        vary = params.format == "auto"
        if self.cache is not None:
            cached = await run_in_threadpool(self.cache.get, key)
            if cached is not None:
                return self.respond(request, cached, True, vary, public=True)
        try:
            fetched = await self.fetcher.fetch(source)
            entry = await self.render(fetched.content, params, negotiated, key)
        except FetchError as exc:
            log.info("fetch rejected: %s (%s)", exc.message, exc.status)
            return error(exc.status, exc.message)
        except ImageError as exc:
            return error(exc.status, exc.message)
        return self.respond(request, entry, False, vary, public=True)

    async def resize_upload(self, request: Request) -> Response:
        try:
            self.check_signature(request)
        except SignatureError as exc:
            return error(403, str(exc))
        try:
            query = self.single_values(request)
            if "url" in query:
                raise ImageError(400, "use GET /resize?url=... for remote images; POST takes an uploaded image")
            params = parse_params(query, self.settings)
            data = await self.read_upload(request)
        except ImageError as exc:
            return error(exc.status, exc.message)
        negotiated = negotiate(request.headers.get("accept", ""), self.settings) if params.format == "auto" else None
        digest = hashlib.sha256(data).hexdigest()
        key = make_key(CACHE_KEY_VERSION, "upload", digest, params.key(), params.format, negotiated)
        if self.cache is not None:
            cached = await run_in_threadpool(self.cache.get, key)
            if cached is not None:
                return self.respond(request, cached, True, params.format == "auto", public=False)
        try:
            entry = await self.render(data, params, negotiated, key)
        except ImageError as exc:
            return error(exc.status, exc.message)
        return self.respond(request, entry, False, params.format == "auto", public=False)

    async def read_upload(self, request: Request) -> bytes:
        limit = self.settings.max_upload_bytes
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit + 64 * 1024:
            raise ImageError(413, f"upload is larger than {limit} bytes")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > limit + 64 * 1024:  # allow for multipart framing
                raise ImageError(413, f"upload is larger than {limit} bytes")
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            data = await _multipart_file(request, bytes(body))
        else:
            data = bytes(body)
        if not data:
            raise ImageError(400, "empty upload: send the image as the body or as a multipart 'file' field")
        if len(data) > limit:
            raise ImageError(413, f"upload is larger than {limit} bytes")
        return data

    async def health(self, request: Request) -> Response:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "signing": self.settings.signing_required,
                "avif": self.settings.enable_avif and avif_available(),
                "cache": self.cache is not None,
            },
            headers={"Cache-Control": "no-store"},
        )


def _etag_matches(header: str, etag: str) -> bool:
    if not header:
        return False
    if header.strip() == "*":
        return True
    candidates = [c.strip().removeprefix("W/") for c in header.split(",")]
    return etag in candidates


async def _multipart_file(request: Request, body: bytes) -> bytes:
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    replay = Request(request.scope, receive)
    try:
        async with replay.form(max_files=1, max_fields=20) as form:
            upload = form.get("file") or form.get("image")
            if not isinstance(upload, UploadFile):
                raise ImageError(400, "multipart upload needs a 'file' field")
            return await upload.read()
    except ImageError:
        raise
    except Exception as exc:  # malformed multipart bodies
        raise ImageError(400, "malformed multipart body") from exc


def create_app(
    settings: Settings | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: Resolver | None = None,
) -> Starlette:
    settings = settings or Settings.from_env()
    service = Service(settings, transport, resolver)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        if service.cache is not None:
            stats = await run_in_threadpool(service.cache.prune)
            log.info("cache %s: %s entries, %s bytes", settings.cache_dir, stats["entries"], stats["bytes"])
        if settings.secret is None:
            log.warning("IMGRESIZE_SECRET is not set: unsigned requests are accepted")
        if settings.secret is None and not settings.allowed_sources:
            log.warning("no IMGRESIZE_ALLOWED_SOURCES: any public host can be used as a source")
        yield
        await service.fetcher.aclose()

    app = Starlette(
        routes=[
            Route("/resize", service.resize_url, methods=["GET"]),
            Route("/resize", service.resize_upload, methods=["POST"]),
            Route("/healthz", service.health, methods=["GET"]),
        ],
        lifespan=lifespan,
    )
    app.state.service = service
    return app

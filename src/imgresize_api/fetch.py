"""Fetch remote source images without becoming an SSRF proxy.

Every URL, including every redirect target, goes through the same checks:

1. scheme is ``https`` (or ``http`` when allowed), no user info, port in the allowed list;
2. the URL matches the source allowlist, when one is configured;
3. the host name is resolved once and **every** address must be public (not private,
   loopback, link-local, CGNAT, multicast, reserved or unspecified, including IPv4
   addresses embedded in IPv6);
4. the connection is made to the checked address (the IP is pinned; ``Host`` and TLS SNI
   keep the original name), so a second DNS answer cannot point somewhere else.

Bodies are streamed with a hard byte limit.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from imgresize_api.settings import Settings

Resolver = Callable[[str, int], Awaitable[list[str]]]
REDIRECT_CODES = {301, 302, 303, 307, 308}
ACCEPTED_CONTENT_TYPES = ("image/", "application/octet-stream", "binary/octet-stream")


class FetchError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class Fetched:
    url: str
    content: bytes
    content_type: str


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if embedded is not None and not is_public_address(str(embedded)):
            return False
        # NAT64 well-known prefix 64:ff9b::/96 carries an IPv4 address in the last 32 bits.
        if ip in ipaddress.IPv6Network("64:ff9b::/96"):
            return is_public_address(str(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)))
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not ip.is_unspecified


def source_allowed(url: str, allowlist: tuple[str, ...]) -> bool:
    """Allowlist entries: ``cdn.example.com``, ``*.example.com`` or a URL prefix ``https://example.com/img/``."""
    if not allowlist:
        return True
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().rstrip(".")
    for entry in allowlist:
        entry = entry.strip()
        lowered = entry.lower()
        if "://" in lowered:
            prefix = urlsplit(entry)
            if (
                parts.scheme == prefix.scheme.lower()
                and host == (prefix.hostname or "").lower()
                and parts.port == prefix.port
                and parts.path.startswith(prefix.path or "/")
            ):
                return True
        elif lowered.startswith("*."):
            suffix = lowered[1:]
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == lowered.rstrip("."):
            return True
    return False


class Fetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
    ) -> None:
        self.settings = settings
        self.resolver = resolver or system_resolver
        self.client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(settings.fetch_timeout),
            headers={"User-Agent": settings.user_agent, "Accept": "image/avif,image/webp,image/*;q=0.9,*/*;q=0.5"},
            trust_env=False,  # never route through a proxy from the environment
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    def check_url(self, url: str) -> tuple[str, str, int]:
        """Static checks. Returns (scheme, host, port) or raises :class:`FetchError`."""
        if len(url) > 4096:
            raise FetchError(400, "source URL is too long")
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as exc:
            raise FetchError(400, "source URL is not valid") from exc
        scheme = parts.scheme.lower()
        allowed_schemes = ("https", "http") if self.settings.allow_http else ("https",)
        if scheme not in allowed_schemes:
            raise FetchError(400, f"source URL scheme must be {' or '.join(allowed_schemes)}")
        if parts.username is not None or parts.password is not None:
            raise FetchError(400, "source URL must not contain credentials")
        host = (parts.hostname or "").rstrip(".")
        if not host:
            raise FetchError(400, "source URL has no host")
        port = port or (443 if scheme == "https" else 80)
        if self.settings.allowed_ports and port not in self.settings.allowed_ports:
            raise FetchError(403, f"source port {port} is not allowed")
        if not source_allowed(url, self.settings.allowed_sources):
            raise FetchError(403, "source is not in the allowlist")
        return scheme, host, port

    async def resolve(self, host: str, port: int) -> str:
        try:
            ipaddress.ip_address(host)
            addresses = [host]
        except ValueError:
            try:
                addresses = await self.resolver(host, port)
            except (OSError, UnicodeError) as exc:
                raise FetchError(502, f"cannot resolve source host {host}") from exc
        if not addresses:
            raise FetchError(502, f"cannot resolve source host {host}")
        if not self.settings.allow_private_networks:
            for address in addresses:
                if not is_public_address(address):
                    raise FetchError(403, "source host resolves to a private or reserved address")
        return addresses[0]

    async def fetch(self, url: str) -> Fetched:
        try:
            return await asyncio.wait_for(self._fetch(url), timeout=self.settings.fetch_timeout * 2)
        except TimeoutError as exc:
            raise FetchError(504, "timed out fetching the source image") from exc

    async def _fetch(self, url: str) -> Fetched:
        current = url
        for _hop in range(self.settings.max_redirects + 1):
            scheme, host, port = self.check_url(current)
            address = await self.resolve(host, port)
            request_url = httpx.URL(current).copy_with(host=address)
            default_port = 443 if scheme == "https" else 80
            host_name = f"[{host}]" if ":" in host else host
            host_header = host_name if port == default_port else f"{host_name}:{port}"
            extensions = {"sni_hostname": host} if scheme == "https" else {}
            request = self.client.build_request(
                "GET", request_url, headers={"Host": host_header}, extensions=extensions
            )
            try:
                response = await self.client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                raise FetchError(504, "timed out fetching the source image") from exc
            except httpx.HTTPError as exc:
                raise FetchError(502, f"cannot fetch the source image: {type(exc).__name__}") from exc
            try:
                if response.status_code in REDIRECT_CODES:
                    location = response.headers.get("location")
                    if not location:
                        raise FetchError(502, "source redirect without a Location header")
                    current = urljoin(current, location)
                    continue
                if response.status_code == 404:
                    raise FetchError(404, "source image not found")
                if response.status_code != 200:
                    raise FetchError(502, f"source answered HTTP {response.status_code}")
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type and not content_type.startswith(ACCEPTED_CONTENT_TYPES):
                    raise FetchError(415, f"source is not an image ({content_type})")
                declared = response.headers.get("content-length")
                limit = self.settings.max_source_bytes
                if declared and declared.isdigit() and int(declared) > limit:
                    raise FetchError(413, f"source image is larger than {limit} bytes")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > limit:
                        raise FetchError(413, f"source image is larger than {limit} bytes")
                    chunks.append(chunk)
                return Fetched(current, b"".join(chunks), content_type)
            except httpx.TimeoutException as exc:
                raise FetchError(504, "timed out reading the source image") from exc
            except httpx.HTTPError as exc:
                raise FetchError(502, f"error reading the source image: {type(exc).__name__}") from exc
            finally:
                await response.aclose()
        raise FetchError(502, f"too many redirects (limit {self.settings.max_redirects})")

"""Configuration from environment variables (``IMGRESIZE_*``)."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field

from imgresize_api._version import __version__

KNOWN_INPUT_FORMATS = ("jpeg", "png", "webp", "gif", "avif", "bmp", "tiff")


class SettingsError(ValueError):
    """An environment variable has an invalid value."""


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise SettingsError(f"{name} must be true or false, got {raw!r}")


def _int(env: Mapping[str, str], name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw.strip().replace("_", ""))
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" and {maximum}" if maximum is not None else ""
        raise SettingsError(f"{name} must be between {minimum}{upper}, got {value}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, minimum: float = 0.0) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be a number, got {raw!r}") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be >= {minimum}")
    return value


def _list(env: Mapping[str, str], name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = env.get(name)
    if raw is None:
        return default
    return tuple(item.strip() for item in raw.replace("\n", ",").split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    secret: bytes | None = None
    allow_unsigned: bool = False
    allowed_sources: tuple[str, ...] = ()
    allow_private_networks: bool = False
    allowed_ports: tuple[int, ...] = (80, 443)
    allow_http: bool = True
    max_source_bytes: int = 20 * 1024 * 1024
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_dimension: int = 4096
    fetch_timeout: float = 10.0
    max_redirects: int = 3
    cache_dir: str = field(default_factory=lambda: os.path.join(tempfile.gettempdir(), "imgresize-cache"))
    cache_ttl: int = 7 * 86400
    cache_max_bytes: int = 1024 * 1024 * 1024
    browser_max_age: int = 86400
    default_quality: int = 82
    input_formats: tuple[str, ...] = ("jpeg", "png", "webp", "gif", "avif", "bmp")
    enable_avif: bool = True
    keep_icc: bool = True
    concurrency: int = field(default_factory=lambda: max(2, os.cpu_count() or 2))
    user_agent: str = f"imgresize-api/{__version__}"

    @property
    def cache_enabled(self) -> bool:
        return bool(self.cache_dir) and self.cache_ttl > 0

    @property
    def signing_required(self) -> bool:
        return self.secret is not None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        secret_raw = env.get("IMGRESIZE_SECRET", "")
        secret = secret_raw.encode("utf-8") if secret_raw else None
        if secret is not None and len(secret) < 16:
            raise SettingsError("IMGRESIZE_SECRET must be at least 16 characters (use: openssl rand -hex 32)")
        allow_unsigned = _bool(env, "IMGRESIZE_ALLOW_UNSIGNED", False)
        if secret is None and not allow_unsigned:
            raise SettingsError(
                "set IMGRESIZE_SECRET to require signed URLs, or IMGRESIZE_ALLOW_UNSIGNED=true to accept "
                "unsigned requests (only behind an allowlist or on a private network)"
            )
        ports_raw = _list(env, "IMGRESIZE_ALLOWED_PORTS", ("80", "443"))
        try:
            ports = tuple(int(p) for p in ports_raw if p != "*")
        except ValueError as exc:
            raise SettingsError("IMGRESIZE_ALLOWED_PORTS must be a comma-separated list of ports or '*'") from exc
        if "*" in ports_raw:
            ports = ()
        formats = tuple(
            f.lower().replace("jpg", "jpeg") for f in _list(env, "IMGRESIZE_INPUT_FORMATS", cls.input_formats)
        )
        unknown = sorted(set(formats) - set(KNOWN_INPUT_FORMATS))
        if unknown:
            raise SettingsError(f"IMGRESIZE_INPUT_FORMATS: unknown format(s) {', '.join(unknown)}")
        defaults = cls()
        return cls(
            secret=secret,
            allow_unsigned=allow_unsigned,
            allowed_sources=_list(env, "IMGRESIZE_ALLOWED_SOURCES"),
            allow_private_networks=_bool(env, "IMGRESIZE_ALLOW_PRIVATE_NETWORKS", False),
            allowed_ports=ports,
            allow_http=_bool(env, "IMGRESIZE_ALLOW_HTTP", True),
            max_source_bytes=_int(env, "IMGRESIZE_MAX_SOURCE_BYTES", defaults.max_source_bytes, 1024),
            max_upload_bytes=_int(env, "IMGRESIZE_MAX_UPLOAD_BYTES", defaults.max_upload_bytes, 1024),
            max_pixels=_int(env, "IMGRESIZE_MAX_PIXELS", defaults.max_pixels, 1),
            max_dimension=_int(env, "IMGRESIZE_MAX_DIMENSION", defaults.max_dimension, 1, 16384),
            fetch_timeout=_float(env, "IMGRESIZE_FETCH_TIMEOUT", defaults.fetch_timeout, 0.1),
            max_redirects=_int(env, "IMGRESIZE_MAX_REDIRECTS", defaults.max_redirects, 0, 10),
            cache_dir=env.get("IMGRESIZE_CACHE_DIR", defaults.cache_dir),
            cache_ttl=_int(env, "IMGRESIZE_CACHE_TTL", defaults.cache_ttl, 0),
            cache_max_bytes=_int(env, "IMGRESIZE_CACHE_MAX_BYTES", defaults.cache_max_bytes, 0),
            browser_max_age=_int(env, "IMGRESIZE_BROWSER_MAX_AGE", defaults.browser_max_age, 0),
            default_quality=_int(env, "IMGRESIZE_DEFAULT_QUALITY", defaults.default_quality, 1, 100),
            input_formats=formats,
            enable_avif=_bool(env, "IMGRESIZE_ENABLE_AVIF", True),
            keep_icc=_bool(env, "IMGRESIZE_KEEP_ICC", True),
            concurrency=_int(env, "IMGRESIZE_CONCURRENCY", defaults.concurrency, 1, 256),
            user_agent=env.get("IMGRESIZE_USER_AGENT", defaults.user_agent),
        )

"""HMAC-SHA256 URL signatures.

The signed message is the path, a ``?`` and the canonical query string: every parameter
except ``s``, sorted by name and then value, percent-encoded (RFC 3986) and joined with
``&``. The signature is the unpadded base64url HMAC-SHA256 of that message and is sent as
``s``. An optional ``expires`` parameter (Unix seconds) is covered by the signature and
checked on every request.

Equivalent code in other languages is in the README.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Iterable
from urllib.parse import parse_qsl, quote, urlsplit

SIGNATURE_PARAM = "s"


class SignatureError(Exception):
    """The request signature is missing, invalid or expired."""


def canonical_query(params: Iterable[tuple[str, str]]) -> str:
    pairs = sorted((k, v) for k, v in params if k != SIGNATURE_PARAM)
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in pairs)


def compute_signature(secret: bytes, path: str, params: Iterable[tuple[str, str]]) -> str:
    message = f"{path}?{canonical_query(params)}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def verify(
    secret: bytes,
    path: str,
    params: list[tuple[str, str]],
    now: float | None = None,
) -> None:
    provided = [v for k, v in params if k == SIGNATURE_PARAM]
    if not provided:
        raise SignatureError("missing signature parameter 's'")
    if len(provided) > 1:
        raise SignatureError("more than one signature parameter")
    expected = compute_signature(secret, path, params)
    if not hmac.compare_digest(provided[0].encode(), expected.encode()):
        raise SignatureError("invalid signature")
    expires = [v for k, v in params if k == "expires"]
    if expires:
        try:
            deadline = int(expires[-1])
        except ValueError as exc:
            raise SignatureError("expires must be a Unix timestamp") from exc
        if (time.time() if now is None else now) > deadline:
            raise SignatureError("signed URL has expired")


def sign_url(secret: bytes, url: str, expires_in: int | None = None, now: float | None = None) -> str:
    """Sign ``/resize?url=...&w=...`` (a path with a query, or a full URL). Returns the same URL with ``s=``."""
    parts = urlsplit(url)
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != SIGNATURE_PARAM]
    if expires_in is not None:
        params = [(k, v) for k, v in params if k != "expires"]
        params.append(("expires", str(int((time.time() if now is None else now) + expires_in))))
    path = parts.path or "/resize"
    signature = compute_signature(secret, path, params)
    query = canonical_query(params)
    prefix = f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
    return f"{prefix}{path}?{query}&{SIGNATURE_PARAM}={signature}" if query else f"{prefix}{path}?s={signature}"

"""Sign imgresize-api URLs on your backend (Python standard library only).

IMGRESIZE_SECRET=... python examples/sign.py https://images.example.com/cat.jpg 400
"""

import base64
import hashlib
import hmac
import os
import sys
import time
from urllib.parse import quote


def sign(secret: str, params: dict[str, str], path: str = "/resize") -> str:
    pairs = sorted((k, str(v)) for k, v in params.items() if k != "s")
    query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in pairs)
    digest = hmac.new(secret.encode(), f"{path}?{query}".encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"{path}?{query}&s={signature}"


if __name__ == "__main__":
    source, width = sys.argv[1], sys.argv[2]
    params = {"url": source, "w": width, "format": "auto", "expires": str(int(time.time()) + 3600)}
    print(sign(os.environ["IMGRESIZE_SECRET"], params))

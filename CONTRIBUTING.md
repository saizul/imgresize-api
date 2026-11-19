# Contributing to imgresize-api

Thank you for helping! New transformations, security hardening, documentation and bug
reports are all welcome.

- [Ways to contribute](#ways-to-contribute)
- [Good first issues](#good-first-issues)
- [Development setup](#development-setup)
- [Project layout](#project-layout)
- [Add a query parameter](#add-a-query-parameter)
- [Guidelines](#guidelines)
- [Pull request process](#pull-request-process)

## Ways to contribute

| Size | Idea |
| --- | --- |
| 10 minutes | Fix a typo, add a FAQ entry, add a signer snippet for another language to `examples/` |
| 30 minutes | Improve an error message or add a missing test case |
| 1-2 hours | One of the good first issues below |
| Larger | Smart crop, S3 sources, a shared cache backend (open an issue first) |

Comment on an issue before you start so nobody else picks the same one.

## Good first issues

Each of these is self-contained and touches one or two files plus tests.

1. **`blur` parameter.** `blur=0.5..50` applies `ImageFilter.GaussianBlur` after resizing.
   Remember the cache key and `KNOWN_PARAMS`. (`processing.py`, `app.py`, tests)
2. **`rotate` and `flip`.** `rotate=90|180|270` and `flip=h|v`, applied after the EXIF
   orientation fix. (`processing.py`, tests)
3. **`fit=pad`.** Contain the image, then paste it centred on a `bg` canvas of exactly
   `w`x`h`. (`processing.py`, tests)
4. **Signer snippets.** `examples/sign.php`, `examples/sign.go` or `examples/sign.rb` that
   reproduce the README test vector, with a comment showing the expected output.
5. **`/metrics` endpoint.** Prometheus text format: request count by status, cache hits
   and misses, processing time histogram, without a new dependency. (`app.py`, tests)
6. **Presets.** `IMGRESIZE_PRESETS="thumb:w=200,h=200,fit=cover;hero:w=1600,format=auto"`
   and `preset=thumb` in the query. (`settings.py`, `app.py`, tests)
7. **`Retry-After` on 503.** When the concurrency limit is saturated for more than N
   seconds, answer `503` with `Retry-After` instead of queueing forever. (`app.py`, tests)
8. **Cache statistics in `/healthz`.** Number of entries and bytes, computed at most once
   a minute. (`cache.py`, `app.py`, tests)

## Development setup

```bash
git clone https://github.com/saizul/imgresize-api
cd imgresize-api
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e .[dev]
pytest
ruff check . && ruff format --check .
```

Python 3.11+ is needed. Tests never use the network. Images are created with Pillow in
the test (`make_image` in `tests/conftest.py`). Remote hosts are simulated by the `Origin`
fixture, which provides an `httpx.MockTransport` and a fake DNS resolver. Add a source with
`origin.image("images.example.com", "/cat.jpg", data)`, or simulate DNS answers with
`origin.dns["host"] = ["10.0.0.1"]`.

To try the server by hand:

```bash
IMGRESIZE_ALLOW_UNSIGNED=true IMGRESIZE_CACHE_DIR=./.cache imgresize serve --log-level debug
curl --data-binary @photo.jpg "http://127.0.0.1:8080/resize?w=300&format=webp" -o out.webp
```

## Project layout

| Path | Responsibility |
| --- | --- |
| `src/imgresize_api/settings.py` | `Settings` from `IMGRESIZE_*` environment variables, validation |
| `src/imgresize_api/signing.py` | canonical query, HMAC signatures, expiry, `sign_url` |
| `src/imgresize_api/fetch.py` | SSRF-safe fetcher: URL checks, allowlist, DNS + IP checks, pinning, redirects, limits |
| `src/imgresize_api/processing.py` | parameter parsing, safe decoding, orientation, resize, encode, metadata stripping |
| `src/imgresize_api/cache.py` | disk cache with TTL, size cap and atomic writes |
| `src/imgresize_api/app.py` | Starlette app: `/resize` (GET and POST), `/healthz`, headers, ETag |
| `src/imgresize_api/cli.py` | `serve`, `sign`, `check-config`, `prune-cache` |
| `tests/` | pytest suite; `conftest.py` has `make_image`, `Origin` and client fixtures |

## Add a query parameter

1. Parse and validate it in `parse_params` (`processing.py`). Reject bad values with
   `ImageError(400, "...")`. Add a field to `Params` and include it in `Params.key()` so that
   it is part of the cache key.
2. Add its name to `KNOWN_PARAMS` in `app.py`. Unknown parameters are rejected on purpose.
3. Apply it in `_process`. Keep the order: orient, then resize, then your step, then encode.
4. Test valid values, invalid values (400) and that two different values produce two cache
   entries.
5. Document it in the README parameter table and add a line under "Unreleased" in
   `CHANGELOG.md`.

## Guidelines

- **Security first.** Every change to `fetch.py` must keep all checks for the first URL and
  every redirect. Add a test for the bypass you are guarding against.
- Never log or return the secret, full signatures or source URLs containing credentials.
- Keep runtime dependencies to Starlette, httpx, uvicorn, Pillow and python-multipart;
  discuss new ones first.
- Image work runs in the thread pool behind the concurrency semaphore; do not block the
  event loop.
- Type hints everywhere; `ruff check` and `ruff format` clean.
- Tests are fast and deterministic: no network, no sleeping, generated images only.

## Pull request process

1. Fork and create a branch (`feat/blur`, `fix/redirect-port`).
2. Keep pull requests focused; fill in the checklist in the template.
3. Add an entry to `CHANGELOG.md` under "Unreleased".
4. A maintainer reviews within a few days.

By contributing you agree that your work is released under the MIT License and
that you follow the [Code of Conduct](CODE_OF_CONDUCT.md).

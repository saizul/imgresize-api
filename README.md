# imgresize-api

[![CI](https://github.com/saizul/imgresize-api/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/imgresize-api/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/saizul/imgresize-api?sort=semver)](https://github.com/saizul/imgresize-api/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Container image](https://img.shields.io/badge/ghcr.io-saizul%2Fimgresize--api-2496ED?logo=docker&logoColor=white)](https://github.com/saizul/imgresize-api/pkgs/container/imgresize-api)
![Python](https://img.shields.io/badge/python-3.11%E2%80%933.13-blue)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

**A small, self-hosted image resize and convert service, like a tiny imgproxy in
Python.** It resizes, crops and converts remote or uploaded images to WebP, AVIF, JPEG or
PNG on the fly. URLs are HMAC-signed so nobody can abuse your server. A strict SSRF guard
protects remote fetches, and decompression bombs are rejected before decoding. Metadata
is stripped, and results are cached on disk with proper `ETag` and `Cache-Control`
headers.

```text
<img src="https://img.example.com/resize?url=https%3A%2F%2Fcdn.example.com%2Fcat.jpg&w=400&format=auto&s=...">
                     │
                     ▼
   verify signature ─▶ disk cache? ─▶ SSRF-checked fetch ─▶ Pillow: orient, resize, strip ─▶ WebP/AVIF/JPEG/PNG
                                      (allowlist, public IPs only,       (pixel limits,
                                       pinned IP, redirects re-checked)   format allowlist)
```

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [API](#api)
- [Signing URLs](#signing-urls)
- [Security model](#security-model)
- [Caching](#caching)
- [Configuration](#configuration)
- [CLI](#cli)
- [FAQ](#faq)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Resize and crop.** `w`, `h`, `fit=contain|cover|fill`, `gravity`, `dpr` for retina
  screens, and `enlarge` to allow upscaling.
- **Convert.** WebP, AVIF, JPEG (progressive) and PNG, with quality `q`. `format=auto`
  picks AVIF or WebP from the browser's `Accept` header.
- **Remote URLs and uploads.** `GET /resize?url=...` or `POST /resize` with the image as
  the body or as a multipart `file` field.
- **Signed URLs.** HMAC-SHA256 over the path and every parameter, with an optional expiry.
  Signer snippets for Python and Node are included.
- **SSRF guard.** Only `http(s)` on allowed ports, with no credentials in the URL. Hosts
  must match the allowlist and every resolved IP must be public. The checked IP is pinned
  for the connection, which defeats DNS rebinding, and every redirect is validated again.
- **Bomb-safe decoding.** Byte limits are enforced while the source streams in. The pixel
  count is checked from the header before decoding, Pillow's decompression-bomb warning is
  an error, output dimensions are capped, and only allowlisted input decoders run.
- **Privacy.** EXIF orientation is applied, then EXIF (including GPS), XMP and comments are
  removed. The ICC colour profile is kept so colours stay correct.
- **Fast repeats.** A disk cache with TTL and a size cap, atomic writes, `ETag` with `304
  Not Modified`, `Cache-Control`, `Vary: Accept` and `X-Cache: HIT|MISS`. JPEG sources are
  shrunk on load.
- **Small and boring.** Starlette, httpx and Pillow, configured with environment variables.
  The container runs as a non-root user.

## Quick start

### Docker

```bash
export IMGRESIZE_SECRET=$(openssl rand -hex 32)
docker run -d --name imgresize -p 8080:8080 \
  -e IMGRESIZE_SECRET -e IMGRESIZE_ALLOWED_SOURCES="upload.wikimedia.org" \
  -v imgresize-cache:/cache ghcr.io/saizul/imgresize-api

# Sign a URL (any machine with the same secret can do this, see "Signing URLs")
URL=$(docker exec -e IMGRESIZE_SECRET imgresize imgresize sign \
  "/resize?url=https://upload.wikimedia.org/wikipedia/commons/a/a9/Example.jpg&w=300&format=webp")
curl -o example.webp "http://127.0.0.1:8080$URL"
```

To try it without signatures on your own machine:

```bash
docker run --rm -p 8080:8080 -e IMGRESIZE_ALLOW_UNSIGNED=true ghcr.io/saizul/imgresize-api
curl -o small.webp --data-binary @photo.jpg "http://127.0.0.1:8080/resize?w=400&format=webp"
```

### pip

```bash
pip install git+https://github.com/saizul/imgresize-api
IMGRESIZE_SECRET=$(openssl rand -hex 32) imgresize serve --port 8080
```

Pillow 11.3 and newer can write AVIF out of the box. With an older Pillow, install the
`avif` extra (`pip install "imgresize-api[avif] @ git+https://github.com/saizul/imgresize-api"`).
`GET /healthz` reports whether AVIF is available.

## API

### `GET /resize`

| Parameter | Values | Default |
| --- | --- | --- |
| `url` | the source image, `http(s)://...` (URL-encode it) | required |
| `w`, `h` | output width / height in CSS pixels, `1`-`IMGRESIZE_MAX_DIMENSION` | source size |
| `fit` | `contain` (fit inside, keep aspect), `cover` (fill and crop), `fill` (stretch) | `contain` |
| `gravity` | crop anchor for `cover`: `center`, `north`, `south`, `east`, `west`, `northeast`, `northwest`, `southeast`, `southwest` | `center` |
| `enlarge` | `1` to allow upscaling beyond the source size | `0` |
| `dpr` | device pixel ratio `1`-`4`, multiplies `w` and `h` | `1` |
| `format` (or `fm`) | `webp`, `avif`, `jpeg` (`jpg`), `png`, `auto` | source format family |
| `q` | quality `1`-`100` (JPEG, WebP, AVIF) | `IMGRESIZE_DEFAULT_QUALITY` (82) |
| `bg` | background hex colour for transparent images saved as JPEG | `ffffff` |
| `expires` | Unix time after which a signed URL stops working | none |
| `s` | signature (required when `IMGRESIZE_SECRET` is set) | |

If you give only `w` or only `h`, the other side follows the aspect ratio. With neither,
the image keeps its size, capped at `IMGRESIZE_MAX_DIMENSION`. Without `enlarge=1`,
images are never upscaled. `cover` then keeps the requested aspect ratio at the largest
size the source allows. Without `format`, JPEG stays JPEG, PNG stays PNG, WebP stays WebP,
and GIF and BMP become PNG. `format=auto` returns AVIF, WebP, or JPEG/PNG (PNG when the
image has transparency) depending on `Accept`, and adds `Vary: Accept`. Animated images
use their first frame. Unknown or repeated parameters are rejected with `400`, so typos
never silently produce a different image.

Response headers:

| Header | Value |
| --- | --- |
| `Content-Type` | `image/webp`, `image/avif`, `image/jpeg` or `image/png` |
| `ETag` | strong hash of the output; `If-None-Match` answers `304` |
| `Cache-Control` | `public, max-age=IMGRESIZE_BROWSER_MAX_AGE` (`private, no-store` for uploads) |
| `X-Cache` | `HIT` or `MISS` (disk cache) |
| `X-Image-Size` | output `WIDTHxHEIGHT` |
| `Vary` | `Accept` when `format=auto` |

Errors are JSON, `{"error": "..."}`, with `Cache-Control: no-store`:

| Status | Meaning |
| --- | --- |
| `400` | invalid parameter, URL or upload |
| `403` | missing, invalid or expired signature; source not allowed (allowlist, port, private IP) |
| `404` | source answered 404 |
| `413` | source or upload too large, or too many pixels |
| `415` | not an image, or a format that is not enabled |
| `422` | image could not be decoded |
| `502` / `504` | source unreachable or too slow |

### `POST /resize`

The same query parameters, except `url`. Send the image as the request body (any
`Content-Type`), or as a `multipart/form-data` field called `file` (or `image`):

```bash
curl --data-binary @photo.jpg "http://127.0.0.1:8080/resize?w=800&format=avif" -o photo.avif
curl -F file=@photo.jpg "http://127.0.0.1:8080/resize?w=200&h=200&fit=cover" -o thumb.jpg
```

When a secret is set, uploads need a signed query string too. Your backend can then hand
out "you may upload with these parameters" URLs. Identical uploads with identical
parameters are served from the cache.

### `GET /healthz`

`{"status": "ok", "version": "0.1.0", "signing": true, "avif": true, "cache": true}`

## Signing URLs

The signature is computed like this:

```text
message   = path + "?" + canonical_query
canonical = all parameters except "s", sorted by name (then value),
            each name and value percent-encoded per RFC 3986 (unreserved: A-Z a-z 0-9 - _ . ~),
            joined as name=value with "&"
s         = base64url( HMAC-SHA256(secret, message) ) without "=" padding
```

Test vector: secret `my-very-secret-signing-key`, path `/resize`, parameters
`url=https://example.com/cat.jpg` and `w=300`. The canonical query is
`url=https%3A%2F%2Fexample.com%2Fcat.jpg&w=300` and
`s=rmEfEey7svQPEn7A42EFmg3WXV_yv4ZVVQNuQ1ciIT0`.

Ready-to-copy signers: [`examples/sign.py`](examples/sign.py) (Python, standard library)
and [`examples/sign.mjs`](examples/sign.mjs) (Node.js). From the shell:

```bash
IMGRESIZE_SECRET=... imgresize sign "/resize?url=https://cdn.example.com/a.jpg&w=400" --expires-in 3600
```

Because the server re-encodes the parameters it received, a client can encode the query
string however it likes. Only the decoded names and values count. The signed path is the
path the service sees. If a proxy strips a prefix such as `/img`, sign `/resize`.

## Security model

imgresize-api assumes that **anyone can send it requests**. The defaults are:

- **No open proxy by accident.** It refuses to start without `IMGRESIZE_SECRET` unless you
  set `IMGRESIZE_ALLOW_UNSIGNED=true`. If you do that, set `IMGRESIZE_ALLOWED_SOURCES` or
  keep the service on a private network.
- **SSRF.** Before every connection, including every redirect hop, up to
  `IMGRESIZE_MAX_REDIRECTS`, the fetcher runs these checks:
  1. the scheme must be `https` or `http` (`IMGRESIZE_ALLOW_HTTP=false` for HTTPS only);
  2. the URL must not contain `user:password@`;
  3. the port must be in `IMGRESIZE_ALLOWED_PORTS` (default `80,443`);
  4. the URL must match the allowlist, when one is set;
  5. the host is resolved once and **all** addresses must be globally routable. Loopback,
     RFC 1918, link-local (`169.254.169.254` cloud metadata), CGNAT, multicast,
     documentation, reserved and unspecified ranges are refused, also when they are
     hidden in IPv4-mapped, 6to4, Teredo or NAT64 IPv6 addresses;
  6. the request goes to the checked IP. The original host name is still used for `Host`,
     TLS SNI and certificate checks, so a second DNS lookup cannot be steered somewhere else.

  Proxy environment variables are ignored and responses must be `200` with an image (or
  `application/octet-stream`) content type.
- **Resource limits.** `IMGRESIZE_MAX_SOURCE_BYTES` and `IMGRESIZE_MAX_UPLOAD_BYTES` are
  enforced while streaming. `IMGRESIZE_MAX_PIXELS` is checked from the image header before
  any pixel is decoded. `IMGRESIZE_MAX_DIMENSION` caps the output, and
  `IMGRESIZE_CONCURRENCY` limits parallel Pillow work.
- **Decoder surface.** Only `IMGRESIZE_INPUT_FORMATS` decoders run (default `jpeg, png,
  webp, gif, avif, bmp`). TIFF is opt-in, and SVG, PDF and PSD are never decoded.
- **Metadata.** Output never contains EXIF, GPS, XMP or comments.

See [SECURITY.md](SECURITY.md) for the hardening checklist and how to report a problem.

## Caching

Each result is stored under `IMGRESIZE_CACHE_DIR` (default `/cache` in the container). The
key is a SHA-256 of the source (URL, or upload digest), every transformation parameter and
the negotiated format. Entries expire after `IMGRESIZE_CACHE_TTL` seconds. Expired entries
are removed at startup, every 200 writes and with `imgresize prune-cache`. The oldest
entries go first when the cache grows beyond `IMGRESIZE_CACHE_MAX_BYTES`. Set
`IMGRESIZE_CACHE_DIR=` (empty) or `IMGRESIZE_CACHE_TTL=0` to disable the cache.

For high traffic, put a CDN or a caching reverse proxy in front.
[`examples/nginx.conf`](examples/nginx.conf) shows an nginx setup that keys on `Accept`
for `format=auto`.

## Configuration

All settings are environment variables:

| Variable | Default | Description |
| --- | --- | --- |
| `IMGRESIZE_SECRET` | | HMAC key (16+ characters); when set, every request must be signed |
| `IMGRESIZE_ALLOW_UNSIGNED` | `false` | allow running without a secret |
| `IMGRESIZE_ALLOWED_SOURCES` | any public host | comma-separated: `cdn.example.com`, `*.example.net`, `https://example.org/images/` |
| `IMGRESIZE_ALLOWED_PORTS` | `80,443` | source ports, or `*` for any |
| `IMGRESIZE_ALLOW_HTTP` | `true` | allow `http://` sources |
| `IMGRESIZE_ALLOW_PRIVATE_NETWORKS` | `false` | allow private/loopback sources (local development only) |
| `IMGRESIZE_MAX_SOURCE_BYTES` | `20971520` | largest remote image (bytes) |
| `IMGRESIZE_MAX_UPLOAD_BYTES` | `20971520` | largest upload (bytes) |
| `IMGRESIZE_MAX_PIXELS` | `40000000` | largest source in pixels (width x height) |
| `IMGRESIZE_MAX_DIMENSION` | `4096` | largest output width or height |
| `IMGRESIZE_FETCH_TIMEOUT` | `10` | seconds per network operation (the whole fetch gets twice that) |
| `IMGRESIZE_MAX_REDIRECTS` | `3` | redirects to follow (each re-validated) |
| `IMGRESIZE_INPUT_FORMATS` | `jpeg,png,webp,gif,avif,bmp` | decoders that may run (`tiff` available) |
| `IMGRESIZE_DEFAULT_QUALITY` | `82` | quality when `q` is not given |
| `IMGRESIZE_ENABLE_AVIF` | `true` | offer AVIF output (when Pillow supports it) |
| `IMGRESIZE_KEEP_ICC` | `true` | keep the ICC colour profile |
| `IMGRESIZE_CACHE_DIR` | temp dir (`/cache` in Docker) | disk cache location; empty disables |
| `IMGRESIZE_CACHE_TTL` | `604800` | cache lifetime in seconds; `0` disables |
| `IMGRESIZE_CACHE_MAX_BYTES` | `1073741824` | cache size cap; `0` for no cap |
| `IMGRESIZE_BROWSER_MAX_AGE` | `86400` | `Cache-Control: max-age` for browsers and CDNs |
| `IMGRESIZE_CONCURRENCY` | CPU count (min 2) | parallel image operations |
| `IMGRESIZE_USER_AGENT` | `imgresize-api/<version>` | `User-Agent` for source fetches |
| `IMGRESIZE_HOST`, `IMGRESIZE_PORT` | `127.0.0.1`, `8080` (`0.0.0.0` in Docker) | bind address |
| `IMGRESIZE_LOG_LEVEL` | `info` | `debug`, `info`, `warning`, `error` |

Run `imgresize check-config` to validate the environment and print a summary. The summary
never includes the secret.

## CLI

```text
imgresize serve [--host H] [--port P] [--log-level L] [--proxy-headers]
imgresize sign "/resize?url=...&w=400" [--expires-in SECONDS]
imgresize check-config
imgresize prune-cache
imgresize --version
```

## FAQ

**How does it compare to imgproxy or thumbor?** imgproxy (Go, libvips) is much faster and
has many more features. Use it if you need them. imgresize-api is about 1,300 lines of
readable Python. You can audit, extend and run it anywhere Pillow runs, and it is secure
by default. It handles typical website traffic well, especially behind a CDN.

**Why are my images not upscaled?** Upscaling is off by default because it makes files
larger without adding detail. Add `enlarge=1`.

**Why is my URL rejected with "private or reserved address"?** The source host resolves
to an internal IP. For local development, set `IMGRESIZE_ALLOW_PRIVATE_NETWORKS=true`
(and `IMGRESIZE_ALLOWED_PORTS=*` for ports such as `3000`). Never do this in production.

**Can I put the secret in the browser?** No. Sign URLs on your server, for example when
rendering HTML, and give the browser the finished URL. Add `expires` for links that should
stop working.

**Does it support SVG, HEIC or PDF?** Not as input. These formats need large, risky
decoders. HEIC could be added behind an opt-in setting with `pillow-heif` (see the roadmap).

**Is the colour profile kept?** Yes. Only the ICC profile survives metadata stripping,
because dropping it changes colours for wide-gamut photos. Set `IMGRESIZE_KEEP_ICC=false`
to remove it too.

## Roadmap

Ideas, many of them marked **good first issue**:

- [ ] `blur`, `sharpen`, `rotate` and `flip` parameters
- [ ] `fit=pad` (contain with a background colour to an exact size)
- [ ] Smart crop (`gravity=smart`, based on entropy)
- [ ] Prometheus `/metrics` (requests, cache hit ratio, processing time)
- [ ] Presets (`preset=thumb` defined in an environment variable)
- [ ] HEIC input through an optional `pillow-heif` extra
- [ ] S3-compatible storage as a source (`s3://bucket/key`) with an allowlist
- [ ] Redis or S3 cache backend for multi-instance deployments
- [ ] Animated WebP output for animated GIF sources

## Contributing

Contributions are welcome, from documentation fixes to new transformations. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the setup, the project layout and a list of good
first issues. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md) and report security
issues privately as described in [SECURITY.md](SECURITY.md).

```bash
git clone https://github.com/saizul/imgresize-api && cd imgresize-api
python -m venv .venv && . .venv/bin/activate
pip install -e .[dev]
pytest && ruff check . && ruff format --check .
```

## License

[MIT](LICENSE) © Saizul Amin

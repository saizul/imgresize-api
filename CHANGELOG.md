# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-19

### Added

- `GET /resize?url=...` for remote images and `POST /resize` for uploads (raw body or
  multipart `file` field).
- Parameters `w`, `h`, `fit` (`contain`, `cover`, `fill`), `gravity`, `enlarge`, `dpr`,
  `format` (`webp`, `avif`, `jpeg`, `png`, `auto` by `Accept`), `q` and `bg`.
- HMAC-SHA256 signed URLs (`s`) with optional expiry (`expires`), required whenever
  `IMGRESIZE_SECRET` is set; the service refuses to start without a secret unless
  `IMGRESIZE_ALLOW_UNSIGNED=true`.
- Source allowlist (hosts, `*.wildcards`, URL prefixes).
- SSRF guard: scheme and port checks, no credentials in URLs, every resolved address must
  be public (including IPv4-mapped, 6to4, Teredo and NAT64 forms), the checked IP is
  pinned for the connection, and every redirect is re-validated.
- Limits on source and upload size (streamed), pixel count (checked from the header before
  decoding) and output dimensions; decompression-bomb warnings are errors.
- Input format allowlist (JPEG, PNG, WebP, GIF, AVIF, BMP by default; TIFF opt-in).
- EXIF orientation fix, metadata stripping (EXIF, XMP, comments), optional ICC profile
  keeping, JPEG shrink-on-load.
- Disk cache with TTL, size cap and atomic writes; `ETag`, `If-None-Match` (304),
  `Cache-Control`, `Vary: Accept` and `X-Cache` headers.
- CLI: `serve`, `sign`, `check-config`, `prune-cache`.
- Container image on `ghcr.io/saizul/imgresize-api` (non-root, health check, `/cache`
  volume).

[Unreleased]: https://github.com/saizul/imgresize-api/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/saizul/imgresize-api/releases/tag/v0.1.0

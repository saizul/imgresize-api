# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | yes |

## What counts as a vulnerability

imgresize-api fetches URLs and decodes untrusted images, so we take these seriously:

- **SSRF:** making the service connect to a private, loopback, link-local, cloud metadata
  or otherwise non-public address, to a port that is not allowed, or to a host outside the
  allowlist. This includes doing it through redirects, DNS tricks or URL parsing differences.
- **Signature bypass:** a request without a valid signature being processed while
  `IMGRESIZE_SECRET` is set, or a signed URL being reusable with different parameters or
  after it expires.
- **Resource exhaustion** with a single request: decompression bombs, oversized bodies, very
  large dimensions, or anything that bypasses the documented limits.
- **Metadata leaks:** EXIF (including GPS), XMP or comments surviving in the output.
- **Cache poisoning:** a response cached under the key of a different source or parameters.
- Secrets or signed URLs with credentials appearing in logs or error messages.

Crashes inside Pillow or its codecs should also be reported to the
[Pillow project](https://github.com/python-pillow/Pillow/security). Please tell us too, so
we can restrict the affected formats.

## Reporting

Please **do not** open a public issue. Use
[GitHub private vulnerability reporting](https://github.com/saizul/imgresize-api/security/advisories/new)
with the version, the impact and steps to reproduce. Do not include real secrets.

You will get an answer within 5 working days. Fixes are released as soon as
possible and credited in the changelog unless you prefer otherwise.

## Hardening checklist for operators

- Always set a long random `IMGRESIZE_SECRET` (`openssl rand -hex 32`) and sign URLs on your
  backend. Never ship the secret to browsers.
- Set `IMGRESIZE_ALLOWED_SOURCES` to the hosts your images really come from.
- Keep `IMGRESIZE_ALLOW_PRIVATE_NETWORKS=false` in production.
- Run the container read-only with the `/cache` volume, as a non-root user (the default),
  with memory and CPU limits.
- Put a CDN or caching reverse proxy in front; responses carry `ETag` and `Cache-Control`.
- Keep Pillow up to date: rebuild the image when Pillow publishes a security release.

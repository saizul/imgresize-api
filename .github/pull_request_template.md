## What does this pull request do?

<!-- One or two sentences. Link the issue: "Closes #123". -->

## Type

- [ ] New transformation or output option
- [ ] Fetching, signing, cache or settings change
- [ ] Bug fix
- [ ] Documentation

## Checklist

- [ ] Tests added or updated (`pytest` passes; images are generated in the test, remote fetches use `httpx.MockTransport`, no network)
- [ ] `ruff check . && ruff format --check .` passes
- [ ] New query parameters are part of the cache key and listed in `KNOWN_PARAMS`
- [ ] Changes to fetching keep every SSRF check for the first URL **and** every redirect
- [ ] No secrets, signatures or source URLs with credentials in logs or error messages
- [ ] README reference updated for new parameters or settings
- [ ] CHANGELOG.md updated under "Unreleased"

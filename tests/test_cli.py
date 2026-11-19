from urllib.parse import parse_qsl, urlsplit

from imgresize_api.cli import main
from imgresize_api.signing import verify

SECRET = "test-secret-0123456789abcdef"  # gitleaks:allow (test fixture)


def test_sign(monkeypatch, capsys):
    monkeypatch.setenv("IMGRESIZE_SECRET", SECRET)
    assert main(["sign", "/resize?url=https://example.com/a.jpg&w=300", "--expires-in", "60"]) == 0
    url = capsys.readouterr().out.strip()
    params = parse_qsl(urlsplit(url).query)
    verify(SECRET.encode(), "/resize", params)
    assert any(k == "expires" for k, _ in params)


def test_sign_requires_secret(monkeypatch, capsys):
    monkeypatch.delenv("IMGRESIZE_SECRET", raising=False)
    assert main(["sign", "/resize?w=1"]) == 2


def test_check_config(monkeypatch, capsys):
    monkeypatch.setenv("IMGRESIZE_SECRET", SECRET)
    monkeypatch.setenv("IMGRESIZE_ALLOWED_SOURCES", "cdn.example.com")
    assert main(["check-config"]) == 0
    out = capsys.readouterr().out
    assert "cdn.example.com" in out and "config OK" in out
    assert SECRET not in out


def test_check_config_error(monkeypatch, capsys):
    monkeypatch.delenv("IMGRESIZE_SECRET", raising=False)
    monkeypatch.delenv("IMGRESIZE_ALLOW_UNSIGNED", raising=False)
    assert main(["check-config"]) == 2
    assert "IMGRESIZE_SECRET" in capsys.readouterr().err


def test_prune_cache(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("IMGRESIZE_ALLOW_UNSIGNED", "true")
    monkeypatch.setenv("IMGRESIZE_CACHE_DIR", str(tmp_path / "c"))
    assert main(["prune-cache"]) == 0
    assert '"removed": 0' in capsys.readouterr().out
    monkeypatch.setenv("IMGRESIZE_CACHE_DIR", "")
    assert main(["prune-cache"]) == 0
    assert "disabled" in capsys.readouterr().out

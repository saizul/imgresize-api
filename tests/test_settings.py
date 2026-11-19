import pytest

from imgresize_api.settings import Settings, SettingsError

BASE = {"IMGRESIZE_SECRET": "0123456789abcdef0123"}  # gitleaks:allow (test fixture)


def test_defaults_with_secret():
    settings = Settings.from_env(BASE)
    assert settings.signing_required
    assert settings.allowed_ports == (80, 443)
    assert settings.cache_enabled
    assert "avif" in settings.input_formats and "tiff" not in settings.input_formats


def test_requires_secret_or_explicit_unsigned():
    with pytest.raises(SettingsError, match="IMGRESIZE_SECRET"):
        Settings.from_env({})
    assert not Settings.from_env({"IMGRESIZE_ALLOW_UNSIGNED": "true"}).signing_required


def test_short_secret_rejected():
    with pytest.raises(SettingsError, match="16 characters"):
        Settings.from_env({"IMGRESIZE_SECRET": "short"})


def test_parsing():
    settings = Settings.from_env(
        {
            **BASE,
            "IMGRESIZE_ALLOWED_SOURCES": "cdn.example.com, *.assets.test\nhttps://media.example.org/uploads/",
            "IMGRESIZE_ALLOWED_PORTS": "*",
            "IMGRESIZE_MAX_PIXELS": "1_000_000",
            "IMGRESIZE_CACHE_DIR": "",
            "IMGRESIZE_INPUT_FORMATS": "jpg,png",
            "IMGRESIZE_KEEP_ICC": "no",
            "IMGRESIZE_FETCH_TIMEOUT": "2.5",
        }
    )
    assert settings.allowed_sources == ("cdn.example.com", "*.assets.test", "https://media.example.org/uploads/")
    assert settings.allowed_ports == ()
    assert settings.max_pixels == 1_000_000
    assert not settings.cache_enabled
    assert settings.input_formats == ("jpeg", "png")
    assert settings.keep_icc is False
    assert settings.fetch_timeout == 2.5


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("IMGRESIZE_MAX_PIXELS", "lots", "integer"),
        ("IMGRESIZE_MAX_DIMENSION", "0", "between"),
        ("IMGRESIZE_DEFAULT_QUALITY", "120", "between"),
        ("IMGRESIZE_ALLOW_PRIVATE_NETWORKS", "maybe", "true or false"),
        ("IMGRESIZE_ALLOWED_PORTS", "80,https", "ports"),
        ("IMGRESIZE_INPUT_FORMATS", "jpeg,svg", "svg"),
        ("IMGRESIZE_FETCH_TIMEOUT", "fast", "number"),
    ],
)
def test_invalid_values(name, value, message):
    with pytest.raises(SettingsError, match=message):
        Settings.from_env({**BASE, name: value})

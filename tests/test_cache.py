import pytest

from imgresize_api.cache import DiskCache, make_etag, make_key


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def cache(tmp_path, clock):
    return DiskCache(tmp_path / "c", ttl=100, max_bytes=0, clock=clock)


def test_put_and_get(cache):
    key = make_key("a", 1)
    entry = cache.put(key, b"data", "image/webp", 10, 20)
    assert entry.etag == make_etag(b"data")
    got = cache.get(key)
    assert got is not None
    assert (got.content, got.content_type, got.etag, got.width, got.height) == (
        b"data",
        "image/webp",
        entry.etag,
        10,
        20,
    )
    assert (cache.directory / key[:2] / key[2:4] / f"{key}.bin").is_file()


def test_keys_are_stable_and_distinct():
    assert make_key("x", {"b": 1, "a": 2}) == make_key("x", {"a": 2, "b": 1})
    assert make_key("x", 1) != make_key("x", 2)


def test_miss_and_ttl(cache, clock):
    key = make_key("ttl")
    assert cache.get(key) is None
    cache.put(key, b"x", "image/png")
    clock.now += 99
    assert cache.get(key) is not None
    clock.now += 2
    assert cache.get(key) is None
    assert not (cache.directory / key[:2] / key[2:4] / f"{key}.json").exists()


def test_rejects_non_hex_keys(cache):
    with pytest.raises(ValueError):
        cache.get("../../etc/passwd")


def test_corrupt_entries_are_misses(cache):
    key = make_key("corrupt")
    cache.put(key, b"12345", "image/png")
    data_path = cache.directory / key[:2] / key[2:4] / f"{key}.bin"
    data_path.write_bytes(b"12")
    assert cache.get(key) is None
    cache.put(key, b"12345", "image/png")
    (data_path.with_suffix(".json")).write_text("{not json")
    assert cache.get(key) is None


def test_prune_removes_expired_and_enforces_size(tmp_path, clock):
    cache = DiskCache(tmp_path / "c", ttl=100, max_bytes=25, clock=clock)
    old = make_key("old")
    cache.put(old, b"o" * 10, "image/png")
    clock.now += 150
    keys = []
    for i in range(4):
        clock.now += 1
        keys.append(make_key(i))
        cache.put(keys[-1], b"n" * 10, "image/png")
    stats = cache.prune()
    assert stats == {"removed": 3, "entries": 2, "bytes": 20}
    assert cache.get(old) is None
    assert cache.get(keys[0]) is None and cache.get(keys[1]) is None
    assert cache.get(keys[3]) is not None
    assert cache.writes_since_prune == 0


def test_prune_cleans_broken_meta(cache):
    key = make_key("broken")
    cache.put(key, b"x", "image/png")
    (cache.directory / key[:2] / key[2:4] / f"{key}.json").write_text("[]")
    assert cache.prune()["removed"] == 1

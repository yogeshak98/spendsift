from __future__ import annotations

from backend.app.persistence.price_cache import CachedDump, PriceCache


def test_read_returns_none_when_empty(tmp_path):
    assert PriceCache(tmp_path / "prices").read() is None


def test_write_then_read_roundtrips_body_and_meta(tmp_path):
    cache = PriceCache(tmp_path / "prices")
    cache.write(
        "122639;...;215.05;24-Jun-2026",
        etag='"abc"',
        last_modified="Tue, 24 Jun 2026 18:30:00 GMT",
    )
    got = cache.read()
    assert isinstance(got, CachedDump)
    assert got.body.startswith("122639;")
    assert got.etag == '"abc"'
    assert got.last_modified == "Tue, 24 Jun 2026 18:30:00 GMT"


def test_write_without_meta_reads_back_none_meta(tmp_path):
    cache = PriceCache(tmp_path / "prices")
    cache.write("body only", etag=None, last_modified=None)
    got = cache.read()
    assert got is not None
    assert got.etag is None
    assert got.last_modified is None


def test_rewrite_overwrites_previous(tmp_path):
    cache = PriceCache(tmp_path / "prices")
    cache.write("old", etag='"1"', last_modified=None)
    cache.write("new", etag='"2"', last_modified=None)
    got = cache.read()
    assert got.body == "new"
    assert got.etag == '"2"'


def test_read_outcome_defaults_to_none_pair(tmp_path):
    assert PriceCache(tmp_path / "prices").read_outcome() == (None, None)


def test_write_outcome_persists_source_and_as_of_without_body(tmp_path):
    cache = PriceCache(tmp_path / "prices")
    cache.write_outcome("offline", "2026-06-24")
    assert cache.read_outcome() == ("offline", "2026-06-24")
    # No body was written, so read() is still None.
    assert cache.read() is None


def test_write_outcome_preserves_existing_headers(tmp_path):
    cache = PriceCache(tmp_path / "prices")
    cache.write("body", etag='"v1"', last_modified="lm")
    cache.write_outcome("offline", "2026-06-24")
    got = cache.read()
    assert got.etag == '"v1"'  # headers preserved by write_outcome
    assert got.last_modified == "lm"
    assert cache.read_outcome() == ("offline", "2026-06-24")


def test_write_preserves_existing_outcome(tmp_path):
    cache = PriceCache(tmp_path / "prices")
    cache.write_outcome("offline", "2026-06-23")
    cache.write("body", etag='"v2"', last_modified=None)
    assert cache.read_outcome() == ("offline", "2026-06-23")  # not clobbered by write

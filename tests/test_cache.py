import json
import time

from routes_api.cache import ResponseCache


def test_cache_key_is_stable_across_mapping_order() -> None:
    assert ResponseCache.key_for({"a": 1, "b": 2}) == ResponseCache.key_for(
        {"b": 2, "a": 1}
    )


def test_expired_cache_entry_is_a_miss(tmp_path) -> None:
    cache = ResponseCache(tmp_path, ttl_seconds=10)
    request = {"url": "example"}
    cache.set(request, {"ok": True})
    cache_path = tmp_path / f"{cache.key_for(request)}.json"
    entry = json.loads(cache_path.read_text(encoding="utf-8"))
    entry["created_at_epoch"] = time.time() - 11
    cache_path.write_text(json.dumps(entry), encoding="utf-8")

    assert cache.get(request) is None

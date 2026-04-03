import time
import hashlib
import json

_cache = {}
CACHE_TTL = 900  # 15 minutes

def _make_key(preference: str, subreddits: list[str]) -> str:
    raw = preference.lower().strip() + "|" + ",".join(sorted(subreddits))
    return hashlib.md5(raw.encode()).hexdigest()

def get_cached(preference: str, subreddits: list[str]) -> list[dict] | None:
    key = _make_key(preference, subreddits)
    if key in _cache:
        entry = _cache[key]
        if time.time() - entry["time"] < CACHE_TTL:
            return entry["posts"]
        else:
            del _cache[key]
    return None

def set_cached(preference: str, subreddits: list[str], posts: list[dict]):
    key = _make_key(preference, subreddits)
    _cache[key] = {
        "time": time.time(),
        "posts": posts,
    }

def clear_cache():
    _cache.clear()

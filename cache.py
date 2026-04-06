#cache module — sqlite-backed feed cache w ttl + md5 keying
import time
import json
import hashlib
import sqlite3

DB_PATH = "clarity_users.db"
#30 min ttl — long enough for a demo session, short enough that content feels fresh
CACHE_TTL = 1800


def _init_cache_table() -> None:
    """Create the cache table if it doesn't exist. Safe to call on every startup."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feed_cache (
            key        TEXT PRIMARY KEY,
            posts_json TEXT NOT NULL,
            subs_json  TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def _make_key(search_term: str, persona_key: str = "") -> str:
    """
    Cache key derived from search term + persona.

    Search term determines the topic. Persona determines sort_method and
    time_filter, which change what Reddit returns — so it must be part of
    the key. Tones are excluded because they only affect scoring, not
    which posts are fetched.

    Args:
        search_term: Raw text from the search box, e.g. "mental health".
        persona_key: Active persona key, e.g. "learner", "tracker".

    Returns:
        MD5 hex digest of the normalised combined key.
    """
    #normalise before hashing -> "Mental Health" + "learner" == "mental health" + "learner"
    normalised = search_term.lower().strip() + "|" + persona_key.lower().strip()
    return hashlib.md5(normalised.encode()).hexdigest()


def get_cached(search_term: str, persona_key: str = "") -> tuple[list[dict], list[str]] | None:
    """
    Look up cached posts and subreddits for a search term + persona combination.

    Args:
        search_term: Raw user search input.
        persona_key: Active persona key.

    Returns:
        Tuple of (posts, subreddits) if a valid cache entry exists, else None.
    """
    key = _make_key(search_term, persona_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT posts_json, subs_json, created_at FROM feed_cache WHERE key = ?",
            (key,)
        ).fetchone()
        conn.close()

        if row is None:
            return None

        #expired entry -> delete + return none so pipeline refetches
        if time.time() - row[2] > CACHE_TTL:
            _delete_entry(key)
            return None

        posts = json.loads(row[0])
        subreddits = json.loads(row[1])
        print(f"[cache] HIT for '{search_term}'")
        return posts, subreddits

    except Exception as e:
        print(f"[cache] Read error: {e}")
        return None


def set_cached(search_term: str, persona_key: str, subreddits: list[str], posts: list[dict]) -> None:
    """
    Persist posts and subreddits for a search term + persona to SQLite.

    Args:
        search_term: Raw user search input used as part of the cache key.
        persona_key: Active persona key used as part of the cache key.
        subreddits: Subreddit names fetched — stored so the caller can reuse
                    them without re-running extract_subreddits().
        posts: Scored post dicts to cache.
    """
    key = _make_key(search_term, persona_key)
    try:
        conn = sqlite3.connect(DB_PATH)
        #upsert — updates existing entry if key exists, inserts otherwise
        conn.execute(
            """
            INSERT INTO feed_cache (key, posts_json, subs_json, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                posts_json = excluded.posts_json,
                subs_json  = excluded.subs_json,
                created_at = excluded.created_at
            """,
            (key, json.dumps(posts), json.dumps(subreddits), time.time())
        )
        conn.commit()
        conn.close()
        print(f"[cache] SET for '{search_term}' ({len(posts)} posts)")
    except Exception as e:
        print(f"[cache] Write error: {e}")


def _delete_entry(key: str) -> None:
    """Remove a single expired cache entry."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM feed_cache WHERE key = ?", (key,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[cache] Delete error: {e}")


def clear_cache() -> None:
    """Wipe all cache entries. Used by force_refresh."""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("DELETE FROM feed_cache")
        conn.commit()
        conn.close()
        print("[cache] Cleared all entries")
    except Exception as e:
        print(f"[cache] Clear error: {e}")


#runs once on import -> ensures table exists before any get/set calls
_init_cache_table()

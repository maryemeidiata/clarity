import sqlite3

DB_PATH = "clarity_users.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            post_id TEXT NOT NULL,
            post_text TEXT,
            signal TEXT NOT NULL,
            weight REAL NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            preference TEXT,
            mood TEXT,
            quality_score INTEGER,
            post_count INTEGER,
            filtered_count INTEGER,
            avg_tone REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()

def log_interaction(session_id: str, post_id: str,
                    post_text: str, signal: str, weight: float):
    conn = get_connection()
    conn.execute(
        "INSERT INTO interactions (session_id, post_id, post_text, signal, weight) "
        "VALUES (?, ?, ?, ?, ?)",
        (session_id, post_id, post_text, signal, weight)
    )
    conn.commit()
    conn.close()

def log_session(session_id: str, preference: str, mood: str,
                quality_score: int, post_count: int, filtered_count: int, avg_tone: float = 0.0):
    conn = get_connection()
    conn.execute(
        "INSERT INTO sessions (session_id, preference, mood, quality_score, post_count, filtered_count, avg_tone) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (session_id, preference, mood, quality_score, post_count, filtered_count, avg_tone)
    )
    conn.commit()
    conn.close()

def get_interaction_context(limit: int = 20) -> str:
    conn = get_connection()
    rows = conn.execute(
        "SELECT post_text, signal, weight FROM interactions "
        "ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    positives = [r["post_text"][:80] for r in rows if r["weight"] > 0]
    negatives = [r["post_text"][:80] for r in rows if r["weight"] < 0]

    context = ""
    if positives:
        context += f"User recently engaged positively with: {'; '.join(positives[:5])}. "
    if negatives:
        context += f"They dismissed: {'; '.join(negatives[:5])}."
    return context

def get_analytics() -> dict:
    conn = get_connection()

    total_sessions = conn.execute("SELECT COUNT(*) as c FROM sessions").fetchone()["c"]
    avg_quality = conn.execute("SELECT AVG(quality_score) as a FROM sessions WHERE quality_score > 0").fetchone()["a"]
    total_filtered = conn.execute("SELECT SUM(filtered_count) as s FROM sessions").fetchone()["s"]
    total_interactions = conn.execute("SELECT COUNT(*) as c FROM interactions").fetchone()["c"]
    thumbs_up = conn.execute("SELECT COUNT(*) as c FROM interactions WHERE weight > 0").fetchone()["c"]
    thumbs_down = conn.execute("SELECT COUNT(*) as c FROM interactions WHERE weight < 0").fetchone()["c"]

    recent_sessions = conn.execute(
        "SELECT preference, quality_score, post_count, filtered_count, avg_tone, created_at "
        "FROM sessions ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    # Per-topic tone averages for Mental Health Index
    topic_tone = conn.execute(
        "SELECT preference, AVG(avg_tone) as tone, SUM(filtered_count) as filtered "
        "FROM sessions WHERE preference IS NOT NULL AND preference != '' "
        "GROUP BY preference ORDER BY tone ASC LIMIT 10"
    ).fetchall()

    # Mood timeline — last 10 sessions with tone
    mood_timeline = conn.execute(
        "SELECT preference, avg_tone, created_at FROM sessions "
        "WHERE avg_tone IS NOT NULL ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

    # Healthiest topic
    healthiest = conn.execute(
        "SELECT preference, AVG(avg_tone) as tone FROM sessions "
        "WHERE preference IS NOT NULL AND preference != '' "
        "GROUP BY preference ORDER BY tone DESC LIMIT 1"
    ).fetchone()

    # Biggest blind spot (most filtered topic)
    blind_spot = conn.execute(
        "SELECT preference, SUM(filtered_count) as filtered FROM sessions "
        "WHERE preference IS NOT NULL AND preference != '' "
        "GROUP BY preference ORDER BY filtered DESC LIMIT 1"
    ).fetchone()

    conn.close()

    return {
        "total_sessions": total_sessions,
        "avg_quality": round(avg_quality, 1) if avg_quality else 0,
        "total_filtered": total_filtered or 0,
        "total_interactions": total_interactions,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
        "recent_sessions": [dict(r) for r in recent_sessions],
        "topic_tone": [dict(r) for r in topic_tone],
        "mood_timeline": [dict(r) for r in mood_timeline],
        "healthiest_topic": dict(healthiest) if healthiest else None,
        "blind_spot": dict(blind_spot) if blind_spot else None,
    }
import sqlite3
import json

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
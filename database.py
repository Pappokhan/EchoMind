import sqlite3
import os
import re
from datetime import datetime, timezone
import config

os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)


def get_connection():
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp TEXT NOT NULL,
            entry_text TEXT NOT NULL,
            mode TEXT DEFAULT 'journal',
            mood TEXT,
            sentiment_score REAL,
            themes TEXT,
            reflection TEXT,
            suggested_action TEXT,
            model_name TEXT,
            prompt_version TEXT,
            latency_ms REAL,
            demo_mode INTEGER DEFAULT 0
        )
    """)

    cur.execute("PRAGMA table_info(entries)")
    existing_cols = {row[1] for row in cur.fetchall()}
    if "mode" not in existing_cols:
        cur.execute("ALTER TABLE entries ADD COLUMN mode TEXT DEFAULT 'journal'")

    if "user_id" not in existing_cols:
        cur.execute("ALTER TABLE entries ADD COLUMN user_id INTEGER")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS request_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            model_name TEXT,
            status TEXT NOT NULL,
            latency_ms REAL,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            error_message TEXT,
            tool_calls_count INTEGER,
            rag_hits INTEGER
        )
    """)

    cur.execute("PRAGMA table_info(request_logs)")
    log_cols = {row[1] for row in cur.fetchall()}
    if "tool_calls_count" not in log_cols:
        cur.execute("ALTER TABLE request_logs ADD COLUMN tool_calls_count INTEGER")
    if "rag_hits" not in log_cols:
        cur.execute("ALTER TABLE request_logs ADD COLUMN rag_hits INTEGER")

    cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_mode ON entries(mode)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_timestamp ON entries(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_entries_user_id ON entries(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_timestamp ON request_logs(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_request_logs_status ON request_logs(status)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS digests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp TEXT NOT NULL,
            period_days INTEGER NOT NULL,
            mode TEXT,
            entry_count INTEGER,
            avg_sentiment REAL,
            headline TEXT,
            mood_trend TEXT,
            narrative TEXT,
            top_themes TEXT,
            suggested_focus TEXT,
            model_name TEXT,
            prompt_version TEXT,
            latency_ms REAL,
            demo_mode INTEGER DEFAULT 0
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_digests_timestamp ON digests(timestamp)")

    cur.execute("PRAGMA table_info(digests)")
    digest_cols = {row[1] for row in cur.fetchall()}
    if "user_id" not in digest_cols:
        cur.execute("ALTER TABLE digests ADD COLUMN user_id INTEGER")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_digests_user_id ON digests(user_id)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, key)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_user_memory_user_id ON user_memory(user_id)")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            attachment_name TEXT,
            attachment_mime TEXT,
            attachment_kind TEXT,
            attachment_path TEXT
        )
    """)

    cur.execute("PRAGMA table_info(chat_messages)")
    chat_cols = {row[1] for row in cur.fetchall()}
    for col in ("attachment_name", "attachment_mime", "attachment_kind", "attachment_path"):
        if col not in chat_cols:
            cur.execute(f"ALTER TABLE chat_messages ADD COLUMN {col} TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_user_id ON chat_messages(user_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON chat_messages(created_at)")

    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
            entry_text, themes, reflection,
            content='entries', content_rowid='id'
        )
    """)

    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
            INSERT INTO entries_fts(rowid, entry_text, themes, reflection)
            VALUES (new.id, new.entry_text, new.themes, new.reflection);
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, entry_text, themes, reflection)
            VALUES ('delete', old.id, old.entry_text, old.themes, old.reflection);
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
            INSERT INTO entries_fts(entries_fts, rowid, entry_text, themes, reflection)
            VALUES ('delete', old.id, old.entry_text, old.themes, old.reflection);
            INSERT INTO entries_fts(rowid, entry_text, themes, reflection)
            VALUES (new.id, new.entry_text, new.themes, new.reflection);
        END
    """)

    cur.execute("""
        INSERT INTO entries_fts(rowid, entry_text, themes, reflection)
        SELECT e.id, e.entry_text, e.themes, e.reflection
        FROM entries e
        WHERE e.id NOT IN (SELECT rowid FROM entries_fts)
    """)

    conn.commit()
    conn.close()

def create_user(username, email, display_name, password_hash):
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO users (username, email, display_name, password_hash, created_at)
           VALUES (?,?,?,?,?)""",
        (username, email, display_name, password_hash, datetime.now(timezone.utc).isoformat()),
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()
    return user_id


def get_user_by_id(user_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_username(username):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_email(email):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_user_profile(user_id, display_name, email):
    conn = get_connection()
    conn.execute(
        "UPDATE users SET display_name = ?, email = ? WHERE id = ?",
        (display_name, email, user_id),
    )
    conn.commit()
    conn.close()


def update_user_password(user_id, password_hash):
    conn = get_connection()
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    conn.commit()
    conn.close()


def get_user_stats(user_id):
    conn = get_connection()
    entry_count = conn.execute(
        "SELECT COUNT(*) AS c FROM entries WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    digest_count = conn.execute(
        "SELECT COUNT(*) AS c FROM digests WHERE user_id = ?", (user_id,)
    ).fetchone()["c"]
    last_entry = conn.execute(
        "SELECT timestamp FROM entries WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    conn.close()
    return {
        "entry_count": entry_count,
        "digest_count": digest_count,
        "last_entry_at": last_entry["timestamp"] if last_entry else None,
    }


def save_entry(entry_text, analysis, latency_ms, demo_mode, mode="journal", user_id=None):
    conn = get_connection()
    conn.execute(
        """INSERT INTO entries
           (user_id, timestamp, entry_text, mode, mood, sentiment_score, themes, reflection,
            suggested_action, model_name, prompt_version, latency_ms, demo_mode)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            datetime.now(timezone.utc).isoformat(),
            entry_text,
            mode,
            analysis.get("mood"),
            analysis.get("sentiment_score"),
            ",".join(analysis.get("themes", [])),
            analysis.get("reflection"),
            analysis.get("suggested_action"),
            config.MODEL_NAME,
            config.PROMPT_VERSION,
            latency_ms,
            1 if demo_mode else 0,
        ),
    )
    conn.commit()
    conn.close()


def get_active_dates(user_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT DISTINCT substr(timestamp, 1, 10) AS d FROM entries WHERE user_id = ? ORDER BY d ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [r["d"] for r in rows]


def get_recent_entries(user_id, limit=50, mode=None):
    conn = get_connection()
    if mode:
        rows = conn.execute(
            "SELECT * FROM entries WHERE user_id = ? AND mode = ? ORDER BY id DESC LIMIT ?",
            (user_id, mode, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM entries WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_entries_since(user_id, cutoff_iso, mode=None):
    conn = get_connection()
    if mode:
        rows = conn.execute(
            """SELECT id, timestamp, mode, mood, sentiment_score, themes, reflection, entry_text
               FROM entries WHERE user_id = ? AND timestamp >= ? AND mode = ? ORDER BY id ASC""",
            (user_id, cutoff_iso, mode),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, timestamp, mode, mood, sentiment_score, themes, reflection, entry_text
               FROM entries WHERE user_id = ? AND timestamp >= ? ORDER BY id ASC""",
            (user_id, cutoff_iso),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_digest(period_days, mode, entry_count, avg_sentiment, headline, mood_trend,
                 narrative, top_themes, suggested_focus, latency_ms, demo_mode, user_id=None):
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO digests
           (user_id, timestamp, period_days, mode, entry_count, avg_sentiment, headline, mood_trend,
            narrative, top_themes, suggested_focus, model_name, prompt_version, latency_ms, demo_mode)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            user_id,
            datetime.now(timezone.utc).isoformat(),
            period_days,
            mode,
            entry_count,
            avg_sentiment,
            headline,
            mood_trend,
            narrative,
            ",".join(top_themes or []),
            suggested_focus,
            config.MODEL_NAME,
            config.PROMPT_VERSION,
            latency_ms,
            1 if demo_mode else 0,
        ),
    )
    digest_id = cur.lastrowid
    conn.commit()
    conn.close()
    return digest_id


def get_recent_digests(user_id, limit=10):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM digests WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def log_request(endpoint, status, latency_ms, model_name=None,
                 prompt_tokens=None, completion_tokens=None, error_message=None,
                 tool_calls_count=None, rag_hits=None):
    conn = get_connection()
    conn.execute(
        """INSERT INTO request_logs
           (timestamp, endpoint, model_name, status, latency_ms,
            prompt_tokens, completion_tokens, error_message,
            tool_calls_count, rag_hits)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now(timezone.utc).isoformat(),
            endpoint,
            model_name,
            status,
            latency_ms,
            prompt_tokens,
            completion_tokens,
            error_message,
            tool_calls_count,
            rag_hits,
        ),
    )
    conn.commit()
    conn.close()


def get_request_logs(limit=200):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM request_logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def save_chat_message(user_id, role, content, attachment=None):
    conn = get_connection()
    conn.execute(
        """INSERT INTO chat_messages
           (user_id, role, content, created_at, attachment_name, attachment_mime,
            attachment_kind, attachment_path)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            user_id, role, content, datetime.now(timezone.utc).isoformat(),
            (attachment or {}).get("name"),
            (attachment or {}).get("mime"),
            (attachment or {}).get("kind"),
            (attachment or {}).get("path"),
        ),
    )
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.close()
    return row_id


def _row_to_chat_message(row):
    d = {"id": row["id"], "role": row["role"], "content": row["content"], "created_at": row["created_at"]}
    if row["attachment_path"]:
        d["attachment"] = {
            "name": row["attachment_name"],
            "mime": row["attachment_mime"],
            "kind": row["attachment_kind"],
        }
    return d


def get_chat_messages(user_id, limit=50):
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, role, content, created_at, attachment_name, attachment_mime,
                  attachment_kind, attachment_path
           FROM chat_messages
           WHERE user_id = ? ORDER BY id DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [_row_to_chat_message(r) for r in reversed(rows)]


def get_chat_attachment(user_id, message_id):
    conn = get_connection()
    row = conn.execute(
        """SELECT attachment_name, attachment_mime, attachment_kind, attachment_path
           FROM chat_messages WHERE id = ? AND user_id = ?""",
        (message_id, user_id),
    ).fetchone()
    conn.close()
    if not row or not row["attachment_path"]:
        return None
    return {
        "name": row["attachment_name"],
        "mime": row["attachment_mime"],
        "kind": row["attachment_kind"],
        "path": row["attachment_path"],
    }


def clear_chat_messages(user_id):
    conn = get_connection()
    paths = [
        r["attachment_path"] for r in conn.execute(
            "SELECT attachment_path FROM chat_messages WHERE user_id = ? AND attachment_path IS NOT NULL",
            (user_id,),
        ).fetchall()
    ]
    conn.execute("DELETE FROM chat_messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    for rel_path in paths:
        try:
            os.remove(os.path.join(config.CHAT_UPLOADS_DIR, rel_path))
        except OSError:
            pass


def get_chat_day_count(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(DISTINCT substr(created_at, 1, 10)) AS c FROM chat_messages WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["c"] if row else 0


def get_chat_message_count(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM chat_messages WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["c"] if row else 0


def get_chat_first_message_at(user_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT created_at FROM chat_messages WHERE user_id = ? ORDER BY id ASC LIMIT 1",
        (user_id,),
    ).fetchone()
    conn.close()
    return row["created_at"] if row else None

def save_memory_fact(user_id, key, value):
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO user_memory (user_id, key, value, created_at, updated_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (user_id, key.strip()[:80], value.strip()[:400], now, now),
    )
    conn.commit()
    conn.close()


def get_memory_facts(user_id, limit=25):
    conn = get_connection()
    rows = conn.execute(
        "SELECT key, value, updated_at FROM user_memory WHERE user_id = ? ORDER BY updated_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_memory_fact(user_id, key):
    conn = get_connection()
    cur = conn.execute("DELETE FROM user_memory WHERE user_id = ? AND key = ?", (user_id, key))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def clear_memory(user_id):
    conn = get_connection()
    conn.execute("DELETE FROM user_memory WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

_FTS_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "have", "has",
    "was", "were", "are", "been", "being", "but", "not", "you", "your",
    "about", "just", "into", "than", "then", "them", "they", "their",
    "what", "when", "where", "which", "while", "would", "could", "should",
    "there", "here", "very", "really", "still", "also", "like", "some",
    "will", "can", "did", "does", "doing", "had", "its",
}


def _fts_query_from_text(text, max_terms=10):
    words = re.findall(r"[a-zA-Z']{3,}", text.lower())
    seen = []
    for w in words:
        if w in _FTS_STOPWORDS or w in seen:
            continue
        seen.append(w)
    seen.sort(key=len, reverse=True)
    terms = seen[:max_terms]
    if not terms:
        return None
    return " OR ".join(f'"{t}"' for t in terms)


def search_similar_entries(user_id, query_text, exclude_id=None, limit=3):
    match_query = _fts_query_from_text(query_text)
    if not match_query:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT e.id, e.timestamp, e.mode, e.mood, e.sentiment_score,
                      e.themes, e.reflection, e.entry_text
               FROM entries_fts f
               JOIN entries e ON e.id = f.rowid
               WHERE entries_fts MATCH ? AND e.user_id = ?
               ORDER BY bm25(entries_fts) LIMIT ?""",
            (match_query, user_id, limit + (1 if exclude_id else 0)),
        ).fetchall()
    except sqlite3.OperationalError:
        conn.close()
        return []
    conn.close()
    results = [dict(r) for r in rows if r["id"] != exclude_id]
    return results[:limit]
from datetime import datetime, timedelta, timezone

import database
from streaks import compute_streak

MAX_TOOL_ROUNDS = 3

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_writing_streak",
            "description": "Get the user's current writing streak, longest streak, and "
                            "total active days. Use this before referencing their streak "
                            "or consistency instead of guessing.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_mood_trend",
            "description": "Get the user's average sentiment score and mood breakdown over "
                            "a recent window, to check whether today's entry fits or breaks "
                            "a recent pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "How many days back to look. Use 7 or 30.",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_past_entries",
            "description": "Search the user's own past entries for ones related to a topic "
                            "or theme, to check for real continuity before claiming they "
                            "wrote about something before.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Topic or keywords to search for."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_about_me",
            "description": "Save a short, durable fact about the user that will still be true "
                            "weeks from now, so future entries can be read with that context "
                            "in mind. Only for stable facts (their job, a recurring goal, a "
                            "named ongoing project or relationship the user themselves "
                            "described) — never transient moods, single-day events, or "
                            "sensitive details like health/medical information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short label, e.g. 'job' or 'big_goal'."},
                    "value": {"type": "string", "description": "The fact itself, in one short sentence."},
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "forget_about_me",
            "description": "Remove a previously saved fact about the user — use only if the "
                            "user's own text explicitly says it's no longer true or asks to "
                            "have it forgotten.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "The key of the fact to remove."},
                },
                "required": ["key"],
            },
        },
    },
]


def _get_writing_streak(user_id, args):
    return compute_streak(database.get_active_dates(user_id))


def _get_mood_trend(user_id, args):
    days = args.get("days") or 7
    try:
        days = max(1, min(int(days), 90))
    except (TypeError, ValueError):
        days = 7
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    entries = database.get_entries_since(user_id, cutoff)
    scored = [e["sentiment_score"] for e in entries if e.get("sentiment_score") is not None]
    avg = round(sum(scored) / len(scored), 3) if scored else None
    moods = {}
    for e in entries:
        m = e.get("mood")
        if m:
            moods[m] = moods.get(m, 0) + 1
    return {
        "days": days,
        "entry_count": len(entries),
        "avg_sentiment_score": avg,
        "mood_counts": moods,
    }


def _search_past_entries(user_id, args):
    query = str(args.get("query") or "").strip()
    if not query:
        return {"results": []}
    hits = database.search_similar_entries(user_id, query, limit=3)
    return {
        "results": [
            {
                "date": (h.get("timestamp") or "")[:10],
                "mode": h.get("mode"),
                "mood": h.get("mood"),
                "themes": h.get("themes"),
                "reflection": h.get("reflection"),
            }
            for h in hits
        ]
    }


def _remember_about_me(user_id, args):
    key = str(args.get("key") or "").strip()
    value = str(args.get("value") or "").strip()
    if not key or not value:
        return {"saved": False, "error": "key and value are required"}
    database.save_memory_fact(user_id, key, value)
    return {"saved": True, "key": key}


def _forget_about_me(user_id, args):
    key = str(args.get("key") or "").strip()
    if not key:
        return {"deleted": False, "error": "key is required"}
    deleted = database.delete_memory_fact(user_id, key)
    return {"deleted": deleted, "key": key}


_IMPLEMENTATIONS = {
    "get_writing_streak": _get_writing_streak,
    "get_mood_trend": _get_mood_trend,
    "search_past_entries": _search_past_entries,
    "remember_about_me": _remember_about_me,
    "forget_about_me": _forget_about_me,
}


def execute_tool_call(name, arguments, user_id):
    impl = _IMPLEMENTATIONS.get(name)
    if impl is None:
        return {"error": f"Unknown tool '{name}'"}
    try:
        return impl(user_id, arguments or {})
    except Exception as e:
        return {"error": f"Tool '{name}' failed: {e}"}
from collections import defaultdict, Counter
from datetime import datetime

import database
import config


def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def get_summary(user_id):
    logs = database.get_request_logs(limit=1000)
    entries = database.get_recent_entries(user_id, limit=1000)

    total_requests = len(logs)
    successes = [l for l in logs if l["status"] == "success"]
    errors = [l for l in logs if l["status"] == "error"]
    fallbacks = [l for l in logs if l["status"] == "fallback"]

    latencies = [l["latency_ms"] for l in logs if l["latency_ms"] is not None]
    avg_latency = round(sum(latencies) / len(latencies), 1) if latencies else 0
    p95_latency = 0
    if latencies:
        s = sorted(latencies)
        idx = max(0, int(len(s) * 0.95) - 1)
        p95_latency = round(s[idx], 1)

    success_rate = round((len(successes) / total_requests) * 100, 1) if total_requests else 100.0

    per_day = Counter()
    for l in logs:
        dt = _parse_ts(l["timestamp"])
        if dt:
            per_day[dt.strftime("%Y-%m-%d")] += 1
    requests_over_time = [{"date": d, "count": c} for d, c in sorted(per_day.items())][-14:]

    entries_chrono = list(reversed(entries))
    sentiment_trend = [
        {
            "timestamp": e["timestamp"],
            "sentiment_score": e["sentiment_score"],
            "mood": e["mood"],
        }
        for e in entries_chrono if e["sentiment_score"] is not None
    ]

    mood_counts = Counter(e["mood"] for e in entries if e["mood"])
    mood_distribution = [{"mood": m, "count": c} for m, c in mood_counts.most_common(8)]

    avg_sentiment = None
    scored = [e["sentiment_score"] for e in entries if e["sentiment_score"] is not None]
    if scored:
        avg_sentiment = round(sum(scored) / len(scored), 3)

    theme_counter = Counter()
    for e in entries:
        if e.get("themes"):
            for t in e["themes"].split(","):
                t = t.strip()
                if t:
                    theme_counter[t] += 1
    top_themes = [{"theme": t, "count": c} for t, c in theme_counter.most_common(10)]

    mode_counts = Counter(e.get("mode") or "journal" for e in entries)
    mode_distribution = [{"mode": m, "count": c} for m, c in mode_counts.most_common(10)]

    return {
        "total_requests": total_requests,
        "success_count": len(successes),
        "error_count": len(errors),
        "fallback_count": len(fallbacks),
        "success_rate": success_rate,
        "avg_latency_ms": avg_latency,
        "p95_latency_ms": p95_latency,
        "requests_over_time": requests_over_time,
        "sentiment_trend": sentiment_trend,
        "mood_distribution": mood_distribution,
        "avg_sentiment": avg_sentiment,
        "top_themes": top_themes,
        "mode_distribution": mode_distribution,
        "total_entries": len(entries),
        "model_name": config.MODEL_NAME,
        "prompt_version": config.PROMPT_VERSION,
        "demo_mode": config.DEMO_MODE,
    }

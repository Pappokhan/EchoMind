import time

import config
import database
from llm_service import _chat_completion, _extract_json
from mlops.logger import log_llm_call
from prompts import DIGEST_SYSTEM_PROMPT, build_digest_user_prompt

DIGEST_ENDPOINT = "/api/digest"
ALLOWED_PERIODS = {7: "Last 7 days", 30: "Last 30 days"}


def _period_label(days: int) -> str:
    return ALLOWED_PERIODS.get(days, f"Last {days} days")


def _rule_based_digest(entries: list, days: int) -> dict:
    scores = [e["sentiment_score"] for e in entries if e.get("sentiment_score") is not None]
    avg = round(sum(scores) / len(scores), 2) if scores else 0.0

    half = max(1, len(scores) // 2)
    first_avg = sum(scores[:half]) / half if scores[:half] else avg
    second_avg = sum(scores[half:]) / max(1, len(scores) - half) if scores[half:] else avg
    delta = second_avg - first_avg
    if delta > 0.15:
        trend = "Improving"
    elif delta < -0.15:
        trend = "Dipping"
    else:
        trend = "Steady"

    theme_counts = {}
    mood_counts = {}
    for e in entries:
        for t in (e.get("themes") or "").split(","):
            t = t.strip()
            if t and t.lower() != "demo mode":
                theme_counts[t] = theme_counts.get(t, 0) + 1
        mood = e.get("mood")
        if mood:
            mood_counts[mood] = mood_counts.get(mood, 0) + 1

    top_themes = [t for t, _ in sorted(theme_counts.items(), key=lambda kv: -kv[1])[:5]]
    top_mood = max(mood_counts, key=mood_counts.get) if mood_counts else "Mixed"

    return {
        "headline": f"{len(entries)} entries, {_period_label(days).lower()}",
        "mood_trend": trend,
        "narrative": (
            f"This is a demo-mode digest (no API key configured): across {len(entries)} "
            f"entries the average score was {avg:+.2f} and the most common read was "
            f"'{top_mood}'. Add a free GEMINI_API_KEY in your .env file to unlock a real "
            "narrative synthesis instead of this aggregate."
        ),
        "top_themes": top_themes,
        "suggested_focus": "Add GEMINI_API_KEY to .env for a full AI-written digest.",
    }


def build_digest(user_id, days: int = 7, mode_id: str = None) -> dict:
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    entries = database.get_entries_since(user_id, cutoff, mode=mode_id)

    if not entries:
        return {
            "empty": True,
            "entry_count": 0,
            "period_days": days,
            "mode": mode_id,
            "period_label": _period_label(days),
        }

    scores = [e["sentiment_score"] for e in entries if e.get("sentiment_score") is not None]
    avg_sentiment = round(sum(scores) / len(scores), 2) if scores else None

    start = time.perf_counter()
    if config.DEMO_MODE:
        result = _rule_based_digest(entries, days)
        latency_ms = (time.perf_counter() - start) * 1000
        log_llm_call(endpoint=DIGEST_ENDPOINT, status="fallback", latency_ms=latency_ms,
                     model_name="rule-based-fallback")
        demo_mode = True
    else:
        try:
            content, usage = _chat_completion(
                DIGEST_SYSTEM_PROMPT,
                build_digest_user_prompt(entries, _period_label(days)),
                max_tokens=500,
            )
            latency_ms = (time.perf_counter() - start) * 1000
            result = _extract_json(content)
            if not isinstance(result.get("top_themes"), list):
                result["top_themes"] = []
            else:
                result["top_themes"] = [str(t) for t in result["top_themes"][:5] if t]
            log_llm_call(endpoint=DIGEST_ENDPOINT, status="success", latency_ms=latency_ms,
                         model_name=config.MODEL_NAME,
                         prompt_tokens=usage.get("prompt_tokens"),
                         completion_tokens=usage.get("completion_tokens"))
            demo_mode = False
        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            log_llm_call(endpoint=DIGEST_ENDPOINT, status="error", latency_ms=latency_ms,
                         model_name=config.MODEL_NAME, error_message=str(e))
            result = _rule_based_digest(entries, days)
            demo_mode = True

    database.save_digest(
        period_days=days,
        mode=mode_id,
        entry_count=len(entries),
        avg_sentiment=avg_sentiment,
        headline=result.get("headline"),
        mood_trend=result.get("mood_trend"),
        narrative=result.get("narrative"),
        top_themes=result.get("top_themes", []),
        suggested_focus=result.get("suggested_focus"),
        latency_ms=latency_ms,
        demo_mode=demo_mode,
        user_id=user_id,
    )

    return {
        "empty": False,
        "period_days": days,
        "period_label": _period_label(days),
        "mode": mode_id,
        "entry_count": len(entries),
        "avg_sentiment": avg_sentiment,
        "headline": result.get("headline"),
        "mood_trend": result.get("mood_trend"),
        "narrative": result.get("narrative"),
        "top_themes": result.get("top_themes", []),
        "suggested_focus": result.get("suggested_focus"),
        "latency_ms": round(latency_ms, 1),
        "demo_mode": demo_mode,
        "model_name": config.MODEL_NAME if not demo_mode else "rule-based-fallback",
    }

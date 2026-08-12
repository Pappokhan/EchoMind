from datetime import date, datetime, timedelta, timezone


def _parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


def compute_streak(active_dates: list) -> dict:
    if not active_dates:
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "total_active_days": 0,
            "wrote_today": False,
        }

    dates = sorted({_parse_date(d) for d in active_dates})
    today = datetime.now(timezone.utc).date()

    longest = 1
    run = 1
    for prev, cur in zip(dates, dates[1:]):
        if (cur - prev).days == 1:
            run += 1
        else:
            longest = max(longest, run)
            run = 1
    longest = max(longest, run)

    date_set = set(dates)
    wrote_today = today in date_set
    anchor = today if wrote_today else today - timedelta(days=1)

    current = 0
    cursor = anchor
    while cursor in date_set:
        current += 1
        cursor -= timedelta(days=1)

    return {
        "current_streak": current,
        "longest_streak": longest,
        "total_active_days": len(dates),
        "wrote_today": wrote_today,
    }

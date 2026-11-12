"""Turn upcoming half-hourly prices into plain advice and a notification message."""
from __future__ import annotations

import numpy as np
import pandas as pd

from agileflex.planner import best_hours, blocks

from .tips import tip_for

TZ = "Europe/London"
PEAK = (16.0, 19.0)


def _fmt(t: pd.Timestamp) -> str:
    return f"{t:%H:%M}"


def _block(a: pd.Timestamp, b: pd.Timestamp, today, **extra) -> dict:
    """A time range for display, flagged when it starts tomorrow so '16:30' isn't read as already past."""
    return {"start": _fmt(a), "end": _fmt(b), "day": "tomorrow" if a.date() > today else "today", **extra}


def when(block: dict) -> str:
    """'tomorrow 16:30–17:30' or '16:30–17:30'."""
    span = f"{block['start']}–{block['end']}"
    return f"tomorrow {span}" if block.get("day") == "tomorrow" else span


def build_advice(prices: pd.Series, now: pd.Timestamp, shift_kwh: float = 5.0, horizon_h: int = 24) -> dict:
    """prices: p/kWh indexed by half-hour start (UTC). Uses slots from the current half-hour onwards."""
    start = now.floor("30min")
    upcoming = prices[(prices.index >= start) & (prices.index < start + pd.Timedelta(hours=horizon_h))].dropna()
    if len(upcoming) < 4:
        raise ValueError("Not enough upcoming prices to give advice.")
    t = upcoming.index.tz_convert(TZ)
    p = upcoming.values.astype(float)
    hod = t.hour + t.minute / 60

    # best_hours returns blocks in time order; the advice lists the cheapest first.
    cheapest = sorted(best_hours(t, p, n=3, cheapest=True, earliest=0, latest=24), key=lambda b: b[2])
    avoid = best_hours(t, p, n=1, cheapest=False, earliest=0, latest=24)
    cheapest_2h = float(np.sort(p)[:4].mean())  # the four cheapest half-hours ahead
    average = float(p.mean())
    in_peak = (hod >= PEAK[0]) & (hod < PEAK[1])
    peak_avg = float(p[in_peak].mean()) if in_peak.any() else None
    negative = blocks(t, np.where(p < 0, 1.0, 0.0), min_kwh=0.5)

    saving_avg = max(0.0, shift_kwh * (average - cheapest_2h) / 100)
    saving_peak = max(0.0, shift_kwh * (peak_avg - cheapest_2h) / 100) if peak_avg is not None else None
    day = now.tz_convert(TZ)

    return {
        "generated_at": now.isoformat(),
        "horizon_end": (upcoming.index[-1] + pd.Timedelta(minutes=30)).tz_convert(TZ).isoformat(),
        "slots": [{"start": ts.isoformat(), "price": round(float(v), 2),
                   "best": any(a <= ts < b for a, b, _ in cheapest)} for ts, v in zip(t, p, strict=True)],
        "cheapest": [_block(a, b, day.date(), avg_p=round(avg, 1)) for a, b, avg in cheapest],
        "avoid": [_block(a, b, day.date(), avg_p=round(avg, 1)) for a, b, avg in avoid],
        "negative": [_block(a, b, day.date()) for a, b, _ in negative],
        "average_p": round(average, 1),
        "peak_average_p": round(peak_avg, 1) if peak_avg is not None else None,
        "cheapest_2h_p": round(cheapest_2h, 1),
        "shift_kwh": shift_kwh,
        "saving_vs_average_gbp": round(saving_avg, 2),
        "saving_vs_peak_gbp": round(saving_peak, 2) if saving_peak is not None else None,
        "monthly_vs_average_gbp": round(saving_avg * 30, 2),
        "tip": tip_for(day.toordinal(), bool(negative), float(p.max() - p.min())),
    }


def notification(advice: dict) -> dict:
    """Short title/body for a push notification (phones show about 2–4 lines)."""
    first = advice["cheapest"][0]
    title = f"Cheapest: {when(first)} ({first['avg_p']:.1f}p)"
    parts = []
    if advice["negative"]:
        parts.append(f"Negative prices {when(advice['negative'][0])}: you're paid to use power.")
    others = advice["cheapest"][1:]
    if others:
        parts.append("Also " + ", ".join(when(c) for c in others) + ".")
    if advice["avoid"]:
        a = advice["avoid"][0]
        parts.append(f"Avoid {when(a)} ({a['avg_p']:.0f}p).")
    saving = advice["saving_vs_peak_gbp"] if advice["saving_vs_peak_gbp"] is not None else advice["saving_vs_average_gbp"]
    basis = "the evening peak" if advice["saving_vs_peak_gbp"] is not None else "the average price"
    parts.append(f"Shifting {advice['shift_kwh']:g} kWh saves about £{saving:.2f} vs {basis}.")
    return {"title": title, "body": " ".join(parts), "url": "/"}

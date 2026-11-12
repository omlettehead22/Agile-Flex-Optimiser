"""Daily planner: once Octopus publishes tomorrow's Agile prices (around 16:00 UK time),
build tonight's charging plan for the car and home battery with the same optimiser as the back-test.

The plan covers one window: today's EV plug-in time (e.g. 18:00) to the same time tomorrow.
Outputs in plans/: latest.md, latest.png, archive/<date>.md, history.csv, state.json.

    python -m agileflex plan           # skips quietly if prices aren't out or today's plan exists
    python -m agileflex plan --force   # rebuild today's plan
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .common import ROOT, _hhmm, frame, make_window
from .fetch import fetch_agile_prices, fetch_carbon_series, find_agile_product, http_get, tariff_code_for
from .report import plot_schedules
from .strategies import baseline, optimised

SLOT = pd.Timedelta(minutes=30)


def plans_dir(cfg: dict) -> Path:
    p = Path(cfg.get("planner", {}).get("plans_dir", "plans"))
    return p if p.is_absolute() else ROOT / p


def target_window(cfg: dict, now: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Tonight's window: today's plug-in time to the same time tomorrow (local, DST-safe)."""
    tz = cfg["data"]["timezone"]
    day = now.tz_convert(tz).tz_localize(None).normalize()
    plug = pd.Timedelta(minutes=_hhmm(cfg["ev"]["plug_in"]))
    return (day + plug).tz_localize(tz), (day + pd.Timedelta(days=1) + plug).tz_localize(tz)


def fetch_window(cfg: dict, start: pd.Timestamp, end: pd.Timestamp, get=http_get):
    """Prices (+ carbon forecast, best effort) for the window. Returns (frame or None, tariff)."""
    d = cfg["data"]
    product = find_agile_product(get) if d["agile_product"] == "auto" else d["agile_product"]
    tariff = tariff_code_for(product, d["region"], get)
    idx = pd.date_range(start.tz_convert("UTC"), end.tz_convert("UTC"), freq="30min", inclusive="left")
    prices = fetch_agile_prices(product, tariff, start, end, get)["price_p_kwh"].reindex(idx)
    if prices.isna().any():
        return None, tariff  # tomorrow's prices not published yet
    try:
        carbon = fetch_carbon_series(d["carbon_region_id"], start, end, get).reindex(idx)
    except Exception as exc:  # the plan doesn't depend on carbon unless a carbon weight is set
        print(f"Note: carbon forecast unavailable ({exc}).")
        carbon = pd.Series(np.nan, index=idx)
    return frame(idx, prices.values, carbon.values, cfg), tariff


def blocks(times: pd.DatetimeIndex, kwh: np.ndarray, min_kwh: float = 0.05) -> list[tuple]:
    """Merge consecutive active half-hours into (start, end, kWh) blocks."""
    out: list[list] = []
    for t, e in zip(times, kwh, strict=True):
        if e > min_kwh:
            if out and out[-1][1] == t:
                out[-1][1] = t + SLOT
                out[-1][2] += e
            else:
                out.append([t, t + SLOT, float(e)])
    return [tuple(b) for b in out]


def best_hours(times: pd.DatetimeIndex, price: np.ndarray, n: int = 3, cheapest: bool = True,
               earliest: float = 7, latest: float = 23) -> list[tuple]:
    """Non-overlapping one-hour slots (two half-hours) within waking hours, by average price."""
    hod = times.hour + times.minute / 60
    candidates = []
    for k in range(len(times) - 1):
        if earliest <= hod[k] and hod[k] + 1 <= latest and times[k + 1] - times[k] == SLOT:
            candidates.append((float(price[k:k + 2].mean()), k))
    candidates.sort(reverse=not cheapest)
    picked, used = [], set()
    for avg, k in candidates:
        if k in used or k + 1 in used:
            continue
        picked.append((times[k], times[k] + 2 * SLOT, avg))
        used.update({k, k + 1})
        if len(picked) == n:
            break
    merged: list[list] = []
    for b_start, b_end, avg in sorted(picked):  # join touching hours into one range
        if merged and merged[-1][1] == b_start:
            hours = (merged[-1][1] - merged[-1][0]) / (2 * SLOT)
            merged[-1][2] = (merged[-1][2] * hours + avg) / (hours + 1)
            merged[-1][1] = b_end
        else:
            merged.append([b_start, b_end, avg])
    return [tuple(b) for b in merged]


def _hm(t) -> str:
    return f"{t:%H:%M}"


def _span(b) -> str:
    return f"{_hm(b[0])}–{_hm(b[1])}"


def render(cfg, start, end, tariff, opt, base, history, now) -> str:
    tz = cfg["data"]["timezone"]
    t = opt.index.tz_convert(tz)
    price = opt["price"].to_numpy()
    cost = float(opt["cost_p"].sum() / 100)
    base_cost = float(base["cost_p"].sum() / 100)
    saving = base_cost - cost
    pct = saving / base_cost * 100 if base_cost > 0 else 0.0
    need, depart = cfg["ev"]["daily_need_kwh"], cfg["ev"]["plug_out"]

    lines = [
        f"# Charging plan: {start:%a %d %b %H:%M} → {end:%a %d %b %H:%M}",
        "",
        f"**Estimated cost for the day (home + car): £{cost:.2f}**, against £{base_cost:.2f} if the car "
        f"charged as soon as it was plugged in. **Saving: £{saving:.2f} ({pct:.0f}%).**",
        "",
        f"Tariff {tariff} · generated {now.tz_convert(tz):%d %b %H:%M} UK time",
        "",
        f"## Car: {need:g} kWh needed by {depart}",
        "",
    ]
    # The battery serves the home first; anything beyond the home's load went into the car.
    ev = opt["ev_kwh"].to_numpy()
    from_batt = np.clip(opt["bat_discharge_kwh"].to_numpy() - opt["house_kwh"].to_numpy(), 0, ev)
    for b in blocks(t, ev):
        sel = (t >= b[0]) & (t < b[1])
        batt, grid = float(from_batt[sel].sum()), float((ev - from_batt)[sel].sum())
        if grid < 0.05:
            how = "from the home battery"
        else:
            avg = float((price[sel] * (ev - from_batt)[sel]).sum() / grid)
            how = f"avg {avg:.1f}p/kWh" + (f"; {batt:.1f} kWh of it from the home battery" if batt >= 0.05 else "")
        lines.append(f"- Charge **{_span(b)}**, {b[2]:.1f} kWh ({how})")

    if cfg["battery"]["capacity_kwh"] > 0:
        lines += ["", "## Home battery", ""]
        charge = blocks(t, opt["bat_charge_kwh"].to_numpy())
        supply = blocks(t, opt["bat_discharge_kwh"].to_numpy())
        big = lambda bs: [b for b in bs if b[2] >= 0.25]  # hide trickles nobody would act on
        lines += [f"- Charge **{_span(b)}**, {b[2]:.1f} kWh" for b in big(charge)] or ["- No charging needed"]
        lines += [f"- Power the home (and car) **{_span(b)}**, {b[2]:.1f} kWh" for b in big(supply)]

    lines += ["", "## Flexible appliances (washing machine, dishwasher, tumble dryer)", ""]
    lines += [f"- Good: **{_span(b)}** (avg {b[2]:.1f}p)" for b in best_hours(t, price)]
    lines += [f"- Avoid: {_span(b)} (avg {b[2]:.1f}p)" for b in best_hours(t, price, n=1, cheapest=False)]

    lo, hi = int(np.argmin(price)), int(np.argmax(price))
    lines += ["", "## Prices", "",
              f"Cheapest {price[lo]:.1f}p at {_hm(t[lo])} · most expensive {price[hi]:.1f}p at {_hm(t[hi])} · "
              f"average {price.mean():.1f}p/kWh"]
    negative = blocks(t, np.where(price < 0, 1.0, 0.0), min_kwh=0.5)
    if negative:
        lines += ["", "**Negative prices: you are paid to use electricity "
                  + ", ".join(_span(b) for b in negative) + ".**"]

    total = history["saving_gbp"].sum()
    since = pd.Timestamp(history["window_start"].min()).tz_convert(tz)
    lines += ["", "## Running total", "",
              f"Saved **£{total:.2f}** over {len(history)} planned day(s) since {since:%d %b %Y}, "
              "compared with charging on plug-in.", "", "![Plan chart](latest.png)", ""]
    return "\n".join(lines)


def make_plan(cfg: dict, now: pd.Timestamp | None = None, get=http_get, force: bool = False) -> Path | None:
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    start, end = target_window(cfg, now)
    out = plans_dir(cfg)
    (out / "archive").mkdir(parents=True, exist_ok=True)
    hist_path, state_path = out / "history.csv", out / "state.json"
    history = pd.read_csv(hist_path) if hist_path.exists() else pd.DataFrame()
    key = start.tz_convert("UTC").isoformat()

    if not force and len(history) and key in set(history["window_start"]):
        print(f"Plan for {start:%d %b} already exists; use --force to rebuild.")
        return None

    # Run late (GitHub sometimes delays scheduled jobs)? Plan from the next half-hour, not 18:00.
    plan_start = max(start, now.tz_convert(start.tz).ceil("30min"))
    if plan_start >= end:
        print("Today's window has already ended; nothing to plan.")
        return None
    df, tariff = fetch_window(cfg, plan_start, end, get)
    if df is None:
        print(f"Prices up to {end:%d %b %H:%M} are not published yet; try again later.")
        return None

    # Battery level: carried over from yesterday's plan (assumes it was followed).
    cap = cfg["battery"]["capacity_kwh"]
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    default = cfg["battery"]["start_soc_frac"] * cap
    soc0 = (state.get("battery_soc_start_kwh") if state.get("window_start") == key
            else state.get("battery_soc_end_kwh", default))
    soc0 = float(np.clip(soc0, cfg["battery"]["min_soc_frac"] * cap, cap))

    w = make_window(df, cfg)
    opt = optimised(w, cfg, soc_start=soc0)
    base = baseline(w, cfg)

    cost, base_cost = float(opt["cost_p"].sum() / 100), float(base["cost_p"].sum() / 100)
    row = {"window_start": key, "plan_cost_gbp": round(cost, 4), "plugin_cost_gbp": round(base_cost, 4),
           "saving_gbp": round(base_cost - cost, 4),
           "saving_pct": round((base_cost - cost) / base_cost * 100, 2) if base_cost > 0 else 0.0,
           "peak_kwh_avoided": round(float(base.loc[base["peak"], "grid_kwh"].sum()
                                           - opt.loc[opt["peak"], "grid_kwh"].sum()), 3),
           "generated_at": now.tz_convert("UTC").isoformat()}
    if len(history):
        history = history[history["window_start"] != key]
    history = pd.concat([history, pd.DataFrame([row])], ignore_index=True).sort_values("window_start")
    history.to_csv(hist_path, index=False)

    soc_end = float(opt["soc_kwh"].iloc[-1]) if cap > 0 else 0.0
    state_path.write_text(json.dumps({"window_start": key, "battery_soc_start_kwh": soc0,
                                      "battery_soc_end_kwh": round(soc_end, 4)}, indent=2))

    text = render(cfg, plan_start, end, tariff, opt, base, history, now)
    if plan_start > start:
        note = (f"\n> Planned late, at {now.tz_convert(start.tz):%H:%M}, so this plan starts at "
                f"{plan_start:%H:%M} instead of {start:%H:%M}.\n")
        text = text.replace("\n\n## Car:", f"\n{note}\n## Car:", 1)
    (out / "latest.md").write_text(text)
    (out / "archive" / f"{start:%Y-%m-%d}.md").write_text(text.replace("\n![Plan chart](latest.png)\n", "\n"))
    plot_schedules(w, cfg, out / "latest.png", {"Baseline": base, "Optimised": opt},
                   title=f"Plan for {plan_start:%a %d %b %H:%M} → {end:%a %d %b %H:%M}",
                   subtitle=f"Estimated £{cost:.2f} vs £{base_cost:.2f} charging on plug-in "
                            f"(saving £{base_cost - cost:.2f})", soc_start=soc0)
    print(f"Plan written to {out / 'latest.md'} (saving £{row['saving_gbp']:.2f}, {row['saving_pct']:.0f}%)")
    return out / "latest.md"

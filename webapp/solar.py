"""Solar + battery savings calculator.

Simulates a typical year in half-hours: solar output from a regional annual yield and a seasonal
daylight shape, a typical home load, and a battery that stores surplus solar for the evening.
Yields are approximate regional averages; replacing them with PVGIS figures per postcode is a
planned upgrade (see TASKS.md).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from agileflex.common import house_profile

# Approximate annual output per kWp of a south-facing, unshaded system (kWh/kWp/year).
YIELD_KWH_PER_KWP = {"A": 950, "B": 920, "C": 940, "D": 880, "E": 910, "F": 850, "G": 860, "H": 1000,
                     "J": 980, "K": 930, "L": 1020, "M": 870, "N": 820, "P": 780}
MONTH_SHARE = np.array([2.5, 4.5, 8.0, 11.0, 13.5, 14.0, 13.5, 11.5, 9.0, 6.5, 3.5, 2.5])
MONTH_SHARE = MONTH_SHARE / MONTH_SHARE.sum()
DAYLIGHT_HOURS = np.array([8.0, 9.8, 11.8, 13.8, 15.6, 16.6, 16.2, 14.6, 12.6, 10.6, 8.8, 7.8])
PANEL_DEGRADATION = 0.005
BATTERY_ROUND_TRIP = 0.90
YEARS = 20


@dataclass
class SolarInput:
    region: str = "C"
    system_kwp: float = 4.0
    system_cost_gbp: float = 6500.0
    battery_kwh: float = 5.0
    battery_cost_gbp: float = 3000.0
    annual_usage_kwh: float = 3100.0
    import_p: float = 25.0
    export_p: float = 15.0
    annual_price_change_pct: float = 0.0
    yield_override: float | None = None


def _year_index() -> pd.DatetimeIndex:
    return pd.date_range("2025-01-01", "2026-01-01", freq="30min", inclusive="left", tz="Europe/London").tz_convert("UTC")


def solar_profile(index_utc: pd.DatetimeIndex, kwp: float, annual_yield: float) -> np.ndarray:
    """Half-hourly kWh. UK solar noon is close to 12:00 UTC, so the bell curve is centred there."""
    mid = index_utc + pd.Timedelta(minutes=15)
    h = mid.hour.values + mid.minute.values / 60
    month = index_utc.tz_convert("Europe/London").month.values - 1
    day_len = DAYLIGHT_HOURS[month]
    x = (h - (12 - day_len / 2)) / day_len
    shape = np.where((x > 0) & (x < 1), np.sin(np.pi * np.clip(x, 0, 1)), 0.0)
    # Weather: each day gets a random brightness factor (same seed every time, so results are repeatable).
    local_day = index_utc.tz_convert("Europe/London").normalize()
    day_pos, days = pd.factorize(local_day)
    factor = np.random.default_rng(2025).gamma(shape=3.0, scale=1 / 3.0, size=len(days))
    shape = shape * factor[day_pos]
    out = np.zeros(len(index_utc))
    total = kwp * annual_yield
    for m in range(12):
        sel = month == m
        if shape[sel].sum() > 0:
            out[sel] = shape[sel] / shape[sel].sum() * total * MONTH_SHARE[m]
    return out


def simulate(load: np.ndarray, gen: np.ndarray, battery_kwh: float) -> dict:
    cap = battery_kwh
    step = min(5.0, battery_kwh / 2) * 0.5  # kWh per half-hour
    eta = np.sqrt(BATTERY_ROUND_TRIP)
    soc = 0.0
    imp = np.zeros(len(load))
    exp = np.zeros(len(load))
    for i in range(len(load)):
        net = gen[i] - load[i]
        if net >= 0:
            charge = min(net, step, (cap - soc) / eta) if cap > 0 else 0.0
            soc += charge * eta
            exp[i] = net - charge
        else:
            need = -net
            give = min(need, step, soc * eta) if cap > 0 else 0.0
            soc -= give / eta
            imp[i] = need - give
    return {"import": imp, "export": exp}


def compute(inp: SolarInput) -> dict:
    if inp.region not in YIELD_KWH_PER_KWP:
        raise ValueError(f"Unknown region {inp.region!r}.")
    annual_yield = inp.yield_override or YIELD_KWH_PER_KWP[inp.region]
    idx = _year_index()
    cfg = {"data": {"timezone": "Europe/London"},
           "house": {"annual_kwh": inp.annual_usage_kwh, "seasonal_swing": 0.20}}
    profile = house_profile(idx, cfg).to_numpy()
    load = profile * inp.annual_usage_kwh / profile.sum()
    gen = solar_profile(idx, inp.system_kwp, annual_yield)
    flows = simulate(load, gen, inp.battery_kwh)

    bill_before = load.sum() * inp.import_p / 100
    bill_after = (flows["import"].sum() * inp.import_p - flows["export"].sum() * inp.export_p) / 100
    saving = bill_before - bill_after
    cost = inp.system_cost_gbp + (inp.battery_cost_gbp if inp.battery_kwh > 0 else 0)

    growth = 1 + inp.annual_price_change_pct / 100
    yearly = [saving * (1 - PANEL_DEGRADATION) ** y * growth ** y for y in range(YEARS)]
    cumulative = np.cumsum(yearly)
    payback = None
    if saving > 0 and cumulative[-1] >= cost:
        k = int(np.argmax(cumulative >= cost))
        before = cumulative[k - 1] if k > 0 else 0.0
        payback = float(round(k + (cost - before) / yearly[k], 1))

    months = idx.tz_convert("Europe/London").month.values
    monthly = []
    for m in range(1, 13):
        sel = months == m
        m_saving = (load[sel].sum() * inp.import_p - flows["import"][sel].sum() * inp.import_p
                    + flows["export"][sel].sum() * inp.export_p) / 100
        monthly.append({"month": pd.Timestamp(2025, m, 1).strftime("%b"), "generation_kwh": round(float(gen[sel].sum()), 0),
                        "saving_gbp": round(float(m_saving), 2)})

    self_used = gen.sum() - flows["export"].sum()
    return {
        "inputs": inp.__dict__ | {"annual_yield_kwh_per_kwp": annual_yield},
        "generation_kwh": round(float(gen.sum()), 0),
        "self_used_kwh": round(float(self_used), 0),
        "exported_kwh": round(float(flows["export"].sum()), 0),
        "self_use_pct": round(float(self_used / gen.sum() * 100), 1) if gen.sum() > 0 else 0.0,
        "grid_import_before_kwh": round(float(load.sum()), 0),
        "grid_import_after_kwh": round(float(flows["import"].sum()), 0),
        "bill_before_gbp": round(float(bill_before), 2),
        "bill_after_gbp": round(float(bill_after), 2),
        "annual_saving_gbp": round(float(saving), 2),
        "upfront_cost_gbp": round(float(cost), 2),
        "payback_years": payback,
        "net_saving_20y_gbp": round(float(cumulative[-1] - cost), 2),
        "monthly": monthly,
        "assumptions": [
            f"Regional yield {annual_yield:.0f} kWh per kWp a year (south-facing, unshaded). Your roof may differ.",
            f"Panels lose {PANEL_DEGRADATION * 100:.1f}% output a year; battery round-trip efficiency {BATTERY_ROUND_TRIP:.0%}.",
            "Sunny and cloudy days vary randomly around the monthly average; the home uses a typical daily pattern.",
            "Battery stores spare solar for later; it is not charged from the grid. Battery replacement is not included.",
            "Prices stay as entered unless you set a yearly price change. Costs are your figures, not quotes.",
        ],
    }

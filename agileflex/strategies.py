"""The three charging strategies. Each takes one daily Window and returns a half-hourly schedule.

All strategies plan with `price` (defaults to the actual Agile prices); metrics are always
scored on the actual prices, so passing a forecast here measures the cost of forecast error.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .common import DT_H, Window, empty_schedule, finish_schedule


def _ev_params(cfg):
    return cfg["ev"]["charger_kw"] * DT_H, cfg["ev"]["daily_need_kwh"]


def _bat_params(cfg):
    b = cfg["battery"]
    cap = b["capacity_kwh"]
    return dict(
        cap=cap,
        step=b["power_kw"] * DT_H,
        eta=float(np.sqrt(b["round_trip_efficiency"])),
        smin=b["min_soc_frac"] * cap,
        s0=b["start_soc_frac"] * cap,
    )


def baseline(w: Window, cfg: dict, price: np.ndarray | None = None) -> pd.DataFrame:
    """EV charges flat out from the moment it is plugged in. No home battery."""
    s = empty_schedule(w)
    step, need = _ev_params(cfg)
    ev = np.zeros(len(s))
    for i in np.flatnonzero(w.plugged):  # chronological
        if need <= 1e-9:
            break
        ev[i] = min(step, need)
        need -= ev[i]
    s["ev_kwh"] = ev
    return finish_schedule(s, w)


def _fill_cheapest(candidates: np.ndarray, price: np.ndarray, step: float, energy: float) -> np.ndarray:
    """Spread `energy` over the cheapest candidate slots, `step` kWh max per slot."""
    out = np.zeros(len(price))
    order = candidates[np.lexsort((candidates, price[candidates]))]  # by price, then time
    for i in order:
        if energy <= 1e-9:
            break
        out[i] = min(step, energy)
        energy -= out[i]
    return out


def rule_based(w: Window, cfg: dict, price: np.ndarray | None = None) -> pd.DataFrame:
    """EV charges in the cheapest plugged-in half-hours. Battery discharges into the
    16:00-19:00 peak, then refills in the cheapest remaining half-hours."""
    p = w.data["price"].to_numpy() if price is None else np.asarray(price)
    s = empty_schedule(w)
    step, need = _ev_params(cfg)
    ev = _fill_cheapest(np.flatnonzero(w.plugged), p, step, need)

    b = _bat_params(cfg)
    house = w.data["house_kwh"].to_numpy()
    dis = np.zeros(len(s))
    soc = b["s0"]
    peak_idx = np.flatnonzero(w.peak)
    for i in peak_idx:  # discharge to cover on-site load, no export
        dis[i] = max(0.0, min(b["step"], house[i] + ev[i], (soc - b["smin"]) * b["eta"]))
        soc -= dis[i] / b["eta"]

    after = np.arange(len(s))
    after = after[(~w.peak) & (after > (peak_idx.max() if len(peak_idx) else -1))]
    grid_needed = max(0.0, (b["s0"] - soc) / b["eta"])
    chg = _fill_cheapest(after, p, b["step"], grid_needed)

    s["ev_kwh"] = ev
    s["bat_charge_kwh"] = chg
    s["bat_discharge_kwh"] = dis
    s["soc_kwh"] = b["s0"] + np.cumsum(chg * b["eta"] - dis / b["eta"])
    return finish_schedule(s, w)


def optimised(w: Window, cfg: dict, price: np.ndarray | None = None, carbon_weight: float | None = None,
              soc_start: float | None = None):
    from .optimise import solve_window  # local import keeps strategies importable without PuLP
    return solve_window(w, cfg, price=price, carbon_weight=carbon_weight, soc_start=soc_start)


STRATEGIES = {
    "Baseline": baseline,
    "Rule-based": rule_based,
    "Optimised": optimised,
}


def run(strategy, wins, cfg, **kw) -> pd.DataFrame:
    """Run a strategy over every window and stack the schedules."""
    parts = []
    for w in wins:
        s = strategy(w, cfg, **kw)
        s["day"] = w.day
        parts.append(s)
    return pd.concat(parts)

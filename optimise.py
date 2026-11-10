"""Daily mixed-integer linear program for EV + home battery scheduling (PuLP + CBC).

For each half-hour t in the 16:00 -> 16:00 window:

    minimise   sum_t (price_t + w * carbon_t / 1000) * grid_t
    grid_t   = house_t + ev_t + charge_t - discharge_t          (kWh at the meter)
    grid_t   >= 0                                               (no export)
    0 <= ev_t <= charger_kW * 0.5 * plugged_t                   (charger limit, plug-in window)
    sum_t ev_t = daily_need                                     (EV full by 07:30)
    0 <= charge_t    <= battery_kW * 0.5 * mode_t               (battery power limit,
    0 <= discharge_t <= battery_kW * 0.5 * (1 - mode_t)          never both at once; mode_t binary)
    soc_t = soc_{t-1} + eta * charge_t - discharge_t / eta      (eta = sqrt(round-trip))
    min_soc <= soc_t <= capacity                                (state-of-charge bounds)
    soc_T >= soc_0                                              (no free energy from the battery)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pulp

from common import DT_H, Window, empty_schedule, finish_schedule


def _var(name, lo, hi, cat="Continuous"):
    return pulp.LpVariable(name, lo, hi, cat)


def _solver(cfg):
    """CBC ships inside PuLP 2.x, so nothing else needs installing."""
    return pulp.PULP_CBC_CMD(msg=False, timeLimit=cfg["optimiser"].get("solver_time_limit_s", 30))


def solve_window(w: Window, cfg: dict, price: np.ndarray | None = None,
                 carbon_weight: float | None = None) -> pd.DataFrame:
    p = w.data["price"].to_numpy() if price is None else np.asarray(price, dtype=float)
    ci = w.data["gco2"].to_numpy()
    house = w.data["house_kwh"].to_numpy()
    wgt = cfg["optimiser"]["carbon_weight_p_per_kg"] if carbon_weight is None else carbon_weight
    coef = p + wgt * ci / 1000.0

    ev_step = cfg["ev"]["charger_kw"] * DT_H
    need = cfg["ev"]["daily_need_kwh"]
    b = cfg["battery"]
    cap, step = b["capacity_kwh"], b["power_kw"] * DT_H
    eta = float(np.sqrt(b["round_trip_efficiency"]))
    smin, s0 = b["min_soc_frac"] * cap, b["start_soc_frac"] * cap

    T = range(len(p))
    m = pulp.LpProblem("flex", pulp.LpMinimize)
    ev = [_var(f"ev{t}", 0, ev_step if w.plugged[t] else 0) for t in T]
    ch = [_var(f"ch{t}", 0, step) for t in T]
    dis = [_var(f"dis{t}", 0, step) for t in T]
    soc = [_var(f"soc{t}", smin, cap) for t in T]
    mode = [_var(f"mode{t}", 0, 1, "Binary") for t in T]

    m += pulp.lpSum(coef[t] * (house[t] + ev[t] + ch[t] - dis[t]) for t in T)
    m += pulp.lpSum(ev) == need
    for t in T:
        m += ch[t] <= step * mode[t]
        m += dis[t] <= step * (1 - mode[t])
        m += dis[t] <= house[t] + ev[t]                        # no export
        prev = s0 if t == 0 else soc[t - 1]
        m += soc[t] == prev + eta * ch[t] - (1.0 / eta) * dis[t]
    m += soc[-1] >= s0

    status = m.solve(_solver(cfg))
    if pulp.LpStatus[status] != "Optimal":
        raise RuntimeError(f"LP not optimal on {w.day.date()}: {pulp.LpStatus[status]}")

    s = empty_schedule(w)
    val = lambda xs: np.array([max(0.0, x.value() or 0.0) for x in xs])
    s["ev_kwh"] = val(ev)
    s["bat_charge_kwh"] = val(ch)
    s["bat_discharge_kwh"] = val(dis)
    s["soc_kwh"] = np.array([x.value() for x in soc])
    return finish_schedule(s, w)

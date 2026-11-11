"""Physical constraints every strategy must respect, on real days and on adversarial price shapes."""
import copy

import numpy as np
import pandas as pd
import pytest

from agileflex.common import DT_H, Window, load_config, load_data, windows
from agileflex.strategies import STRATEGIES, baseline, optimised, rule_based

TOL = 1e-5


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def wins(cfg):
    return windows(load_data(cfg), cfg)


def _sample(wins):
    """Every 10th day, both DST days, and the day with the most negative prices."""
    pick = wins[::10]
    pick += [w for w in wins if len(w.data) != 48]
    pick.append(min(wins, key=lambda w: w.data["price"].clip(upper=0).sum()))
    return pick


def _with_prices(w: Window, prices) -> Window:
    d = w.data.copy()
    d["price"] = np.asarray(prices, dtype=float)
    return Window(day=w.day, data=d, plugged=w.plugged, peak=w.peak)


def check(s: pd.DataFrame, w: Window, cfg: dict, battery=True):
    ev, ch, dis, soc = (s[c].to_numpy() for c in ("ev_kwh", "bat_charge_kwh", "bat_discharge_kwh", "soc_kwh"))
    b = cfg["battery"]
    cap, step = b["capacity_kwh"], b["power_kw"] * DT_H
    eta = np.sqrt(b["round_trip_efficiency"])
    s0 = b["start_soc_frac"] * cap

    # EV: reaches its daily need, within charger power, only while plugged in (18:00-07:30)
    assert ev.sum() == pytest.approx(cfg["ev"]["daily_need_kwh"], abs=1e-4)
    assert (ev >= -TOL).all()
    assert (ev <= cfg["ev"]["charger_kw"] * DT_H + TOL).all()
    assert (ev[~w.plugged] <= TOL).all()

    # No export
    assert (s["grid_kwh"].to_numpy() >= -TOL).all()

    if not battery:
        assert np.abs(ch).max() <= TOL and np.abs(dis).max() <= TOL
        return
    # Battery: power limits, never charge and discharge in the same half-hour
    assert (ch >= -TOL).all() and (ch <= step + TOL).all()
    assert (dis >= -TOL).all() and (dis <= step + TOL).all()
    assert not ((ch > TOL) & (dis > TOL)).any()
    # State of charge: inside bounds, follows the loss model, restored by the end of the day
    assert (soc >= b["min_soc_frac"] * cap - TOL).all()
    assert (soc <= cap + TOL).all()
    expected = s0 + np.cumsum(eta * ch - dis / eta)
    assert np.allclose(soc, expected, atol=1e-4)
    assert soc[-1] >= s0 - 1e-4


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_real_days(name, wins, cfg):
    for w in _sample(wins):
        check(STRATEGIES[name](w, cfg), w, cfg, battery=(name != "Baseline"))


@pytest.mark.parametrize("shape", ["all_negative", "flat", "spike_at_7am", "rising", "random"])
@pytest.mark.parametrize("name", list(STRATEGIES))
def test_adversarial_prices(name, shape, wins, cfg):
    w = wins[40]
    n = len(w.data)
    rng = np.random.default_rng(0)
    prices = {
        "all_negative": np.full(n, -5.0),
        "flat": np.full(n, 20.0),
        "spike_at_7am": np.where(np.arange(n) == 30, 500.0, 10.0),
        "rising": np.linspace(-10, 60, n),
        "random": rng.normal(20, 15, n),
    }[shape]
    w2 = _with_prices(w, prices)
    check(STRATEGIES[name](w2, cfg), w2, cfg, battery=(name != "Baseline"))


def test_plug_window_is_18_to_0730(wins, cfg):
    for w in wins[:5] + wins[200:205] + [x for x in wins if len(x.data) != 48]:
        local = w.data["local"][w.plugged]
        assert local.iloc[0].strftime("%H:%M") == "18:00"
        assert local.iloc[-1].strftime("%H:%M") == "07:00"  # last slot ends 07:30
        # 13.5 h normally; 12.5 h / 14.5 h on the nights the clocks go forward / back
        elapsed_h = (local.iloc[-1] - local.iloc[0]).total_seconds() / 3600 + 0.5
        assert w.plugged.sum() == elapsed_h * 2
        assert {len(x.data) for x in wins} == {46, 48, 50}
        peak = w.data["local"][w.peak]
        assert peak.iloc[0].strftime("%H:%M") == "16:00" and w.peak.sum() == 6


def test_optimiser_never_worse_than_heuristics(wins, cfg):
    for w in _sample(wins):
        opt = optimised(w, cfg)["cost_p"].sum()
        assert opt <= rule_based(w, cfg)["cost_p"].sum() + 1e-3
        assert opt <= baseline(w, cfg)["cost_p"].sum() + 1e-3


def test_carbon_weight_reduces_emissions(wins, cfg):
    for w in wins[::30]:
        lo = optimised(w, cfg, carbon_weight=0)
        hi = optimised(w, cfg, carbon_weight=50)
        assert hi["kgco2"].sum() <= lo["kgco2"].sum() + 1e-6
        assert hi["cost_p"].sum() >= lo["cost_p"].sum() - 1e-6


def test_no_battery_config(wins, cfg):
    c = copy.deepcopy(cfg)
    c["battery"]["capacity_kwh"] = 0
    for w in wins[::60]:
        check(optimised(w, c), w, c, battery=False)


def test_infeasible_need_is_reported(wins, cfg):
    c = copy.deepcopy(cfg)
    c["ev"]["daily_need_kwh"] = 200  # more than 7 kW x 13.5 h can deliver
    with pytest.raises(RuntimeError):
        optimised(wins[0], c)

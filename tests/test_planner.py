import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from agileflex.common import load_config
from agileflex.planner import best_hours, blocks, make_plan, target_window

from fakes import synthetic_market

PRODUCT, TARIFF = "AGILE-24-10-01", "E-1R-AGILE-24-10-01-C"


def fake_octopus(published_until: str, carbon_ok: bool = True):
    """Fake Octopus + carbon APIs serving synthetic prices up to `published_until` (UTC)."""
    market = synthetic_market("2025-10-01", "2026-04-30", seed=11)
    cutoff = pd.Timestamp(published_until, tz="UTC")

    def get(url, params=None):
        if url.endswith("/v1/products/"):
            return {"results": [{"code": PRODUCT, "direction": "IMPORT", "available_from": "2024-10-01"}], "next": None}
        if url.endswith(f"/products/{PRODUCT}/"):
            return {"single_register_electricity_tariffs": {"_C": {"direct_debit_monthly": {"code": TARIFF}}}}
        if "standard-unit-rates" in url:
            lo, hi = pd.Timestamp(params["period_from"]), pd.Timestamp(params["period_to"])
            sel = market[(market.index >= lo) & (market.index < hi) & (market.index < cutoff)]
            rows = [{"valid_from": t.isoformat(), "value_inc_vat": v, "value_exc_vat": v / 1.05}
                    for t, v in sel["price_p_kwh"].items()][::-1]  # API returns newest first
            return {"results": rows, "next": None}
        if "carbonintensity" in url:
            if not carbon_ok:
                raise ConnectionError("down")
            parts = url.split("/")
            lo, hi = pd.Timestamp(parts[-4]), pd.Timestamp(parts[-3])
            sel = market[(market.index >= lo) & (market.index < hi)]
            return {"data": {"data": [{"from": t.strftime("%Y-%m-%dT%H:%MZ"), "intensity": {"forecast": v}}
                                      for t, v in sel["carbon_g_kwh"].items()]}}
        raise AssertionError(url)
    return get


@pytest.fixture
def plan_cfg(tmp_path):
    cfg = load_config()
    cfg["planner"] = {"plans_dir": str(tmp_path / "plans")}
    return cfg


def test_target_window_is_tonight(plan_cfg):
    start, end = target_window(plan_cfg, pd.Timestamp("2025-12-03 16:45", tz="UTC"))
    assert (start.hour, start.day, end.day) == (18, 3, 4)


def test_plan_written_with_savings(plan_cfg):
    now = pd.Timestamp("2025-12-03 16:45", tz="UTC")
    path = make_plan(plan_cfg, now=now, get=fake_octopus("2025-12-04 23:00"))
    text = path.read_text()
    out = path.parent
    need = plan_cfg["ev"]["daily_need_kwh"]
    assert f"## Car: {need:g} kWh needed by 07:30" in text and "Charge **" in text and "Saving: £" in text
    assert (out / "latest.png").stat().st_size > 0
    assert (out / "archive" / "2025-12-03.md").exists()
    hist = pd.read_csv(out / "history.csv")
    assert len(hist) == 1 and hist["saving_gbp"].iloc[0] > 0
    state = json.loads((out / "state.json").read_text())
    assert 0 <= state["battery_soc_end_kwh"] <= plan_cfg["battery"]["capacity_kwh"]


def test_waits_when_prices_not_published(plan_cfg, tmp_path):
    now = pd.Timestamp("2025-12-03 15:00", tz="UTC")
    assert make_plan(plan_cfg, now=now, get=fake_octopus("2025-12-03 23:00")) is None
    assert not (tmp_path / "plans" / "latest.md").exists()


def test_daily_runs_build_history_and_carry_battery(plan_cfg):
    get = fake_octopus("2026-01-01")
    for day in ("2025-12-03", "2025-12-04", "2025-12-05"):
        make_plan(plan_cfg, now=pd.Timestamp(f"{day} 16:45", tz="UTC"), get=get)
    out = Path(plan_cfg["planner"]["plans_dir"])
    hist = pd.read_csv(out / "history.csv")
    assert len(hist) == 3
    assert "over 3 planned day(s)" in (out / "latest.md").read_text()
    # Re-running the same day is skipped; --force rebuilds without duplicating history.
    assert make_plan(plan_cfg, now=pd.Timestamp("2025-12-05 17:45", tz="UTC"), get=get) is None
    make_plan(plan_cfg, now=pd.Timestamp("2025-12-05 17:45", tz="UTC"), get=get, force=True)
    assert len(pd.read_csv(out / "history.csv")) == 3


def test_plan_survives_carbon_outage(plan_cfg):
    path = make_plan(plan_cfg, now=pd.Timestamp("2025-12-03 16:45", tz="UTC"),
                     get=fake_octopus("2025-12-04 23:00", carbon_ok=False))
    assert path is not None


def test_plan_on_clock_change_night(plan_cfg):
    # 25 Oct 2025 18:00 -> 26 Oct 18:00 local has 50 half-hours (clocks go back).
    path = make_plan(plan_cfg, now=pd.Timestamp("2025-10-25 15:45", tz="UTC"), get=fake_octopus("2025-10-26 23:00"))
    assert path is not None


def test_blocks_and_best_hours():
    t = pd.date_range("2025-12-03 07:00", periods=8, freq="30min", tz="Europe/London")
    assert blocks(t, np.array([0, 1, 1, 0, 2, 0, 0, 0])) == [
        (t[1], t[3], 2.0), (t[4], t[5], 2.0)]
    price = np.array([30, 5, 5, 30, 30, 10, 10, 30.0])
    best = best_hours(t, price, n=2)
    assert [b[0] for b in best] == [t[1], t[5]]
    flat = best_hours(t, np.array([5, 5, 5, 5, 30, 30, 30, 30.0]), n=2)  # touching hours merge
    assert flat == [(t[0], t[4], 5.0)]
    assert blocks(t, np.array([0.01, 0, 0, 0, 0, 0, 0, 0])) == []  # trickles below 0.05 kWh are hidden


def test_plan_starts_from_yesterdays_battery_level(plan_cfg):
    get = fake_octopus("2026-01-01")
    out = Path(plan_cfg["planner"]["plans_dir"])
    make_plan(plan_cfg, now=pd.Timestamp("2025-12-03 16:45", tz="UTC"), get=get)
    end_day1 = json.loads((out / "state.json").read_text())["battery_soc_end_kwh"]
    make_plan(plan_cfg, now=pd.Timestamp("2025-12-04 16:45", tz="UTC"), get=get)
    assert json.loads((out / "state.json").read_text())["battery_soc_start_kwh"] == pytest.approx(end_day1)


def test_late_run_plans_from_next_half_hour(plan_cfg):
    out = Path(plan_cfg["planner"]["plans_dir"])
    make_plan(plan_cfg, now=pd.Timestamp("2025-12-03 21:47", tz="UTC"), get=fake_octopus("2026-01-01"))
    text = (out / "latest.md").read_text()
    assert text.startswith("# Charging plan: Wed 03 Dec 22:00 →")
    assert "Planned late, at 21:47, so this plan starts at 22:00 instead of 18:00." in text
    need = plan_cfg["ev"]["daily_need_kwh"]
    assert f"## Car: {need:g} kWh needed by 07:30" in text  # still fully charged by morning


import pandas as pd
import pytest
import yaml

from agileflex.common import ROOT, _hhmm, house_profile, load_config


def test_unquoted_yaml_times_are_accepted():
    assert _hhmm("07:30") == 450
    assert _hhmm(yaml.safe_load("t: 18:00")["t"]) == 18 * 60  # YAML 1.1 sexagesimal


def test_impossible_ev_need_is_rejected(tmp_path):
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    cfg["ev"]["daily_need_kwh"] = 200
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError, match="cannot be delivered"):
        load_config(p)


def test_house_profile_keeps_season_on_a_single_day():
    cfg = load_config()
    day = lambda d: pd.date_range(d, periods=48, freq="30min", tz="UTC")
    winter, summer = house_profile(day("2026-01-15"), cfg).sum(), house_profile(day("2026-07-15"), cfg).sum()
    assert winter > 1.4 * summer  # +-20% seasonal swing
    year = pd.date_range("2025-01-01", "2026-01-01", freq="30min", tz="UTC", inclusive="left")
    assert house_profile(year, cfg).sum() == pytest.approx(cfg["house"]["annual_kwh"], rel=0.01)

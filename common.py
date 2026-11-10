"""Shared plumbing: config, cached data, the household load profile and daily planning windows."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "outputs"
DT_H = 0.5  # half-hour settlement period, in hours

# Typical UK domestic shape (hour-of-day weights, before scaling): low overnight,
# a morning bump and an evening peak around 18:00-19:00.
_HOURLY_SHAPE = np.array([
    0.30, 0.25, 0.22, 0.21, 0.21, 0.23, 0.32, 0.45, 0.50, 0.45, 0.42, 0.42,
    0.45, 0.43, 0.40, 0.42, 0.52, 0.68, 0.78, 0.75, 0.65, 0.55, 0.45, 0.36,
])


def load_config(path: str | Path | None = None) -> dict:
    with open(path or ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def _hhmm(s: str) -> int:
    """'07:30' -> minutes after midnight."""
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def house_profile(index_utc: pd.DatetimeIndex, cfg: dict) -> pd.Series:
    """Synthetic household load (kWh per half-hour), excluding the EV."""
    hc = cfg["house"]
    if hc.get("profile_csv"):
        p = pd.read_csv(hc["profile_csv"], parse_dates=["valid_from"]).set_index("valid_from")["house_kwh"]
        return p.reindex(index_utc).interpolate(limit_direction="both")
    local = index_utc.tz_convert(cfg["data"]["timezone"])
    shape = _HOURLY_SHAPE[local.hour]
    doy = local.day_of_year.to_numpy()
    season = 1 + hc["seasonal_swing"] * np.cos(2 * np.pi * (doy - 15) / 365.25)
    raw = shape * season
    scaled = raw / raw.sum() * hc["annual_kwh"] * (len(index_utc) / (365 * 48))
    return pd.Series(scaled, index=index_utc, name="house_kwh")


def load_data(cfg: dict | None = None) -> pd.DataFrame:
    """Cached prices + carbon + house load on one half-hourly UTC index."""
    cfg = cfg or load_config()
    region = cfg["data"]["region"]
    price = pd.read_csv(DATA / f"agile_{region}.csv", parse_dates=["valid_from"]).set_index("valid_from")
    carbon = pd.read_csv(DATA / f"carbon_region{cfg['data']['carbon_region_id']}.csv", parse_dates=["valid_from"]).set_index("valid_from")
    start = pd.Timestamp(cfg["data"]["start"], tz="UTC")
    end = pd.Timestamp(cfg["data"]["end"], tz="UTC")
    idx = pd.date_range(start, end, freq="30min", inclusive="left")
    df = pd.DataFrame(index=idx)
    df["price"] = price["price_p_per_kwh"].reindex(idx)
    df["gco2"] = carbon["gco2_per_kwh"].reindex(idx)
    if df.isna().any().any():
        raise ValueError("Cached data has gaps for the configured period; run `python fetch.py --refresh`.")
    df["house_kwh"] = house_profile(idx, cfg)
    df["local"] = idx.tz_convert(cfg["data"]["timezone"])
    return df


@dataclass
class Window:
    """One daily planning window (decision time -> decision time next day, local)."""
    day: pd.Timestamp          # local date the window starts
    data: pd.DataFrame         # slice of load_data() for the window
    plugged: np.ndarray        # bool: EV is plugged in during this slot
    peak: np.ndarray           # bool: slot is in the 16:00-19:00 peak


def windows(df: pd.DataFrame, cfg: dict) -> list[Window]:
    """Split the year into daily windows. DST days give 46 or 50 slots, handled naturally."""
    tz = cfg["data"]["timezone"]
    dec = _hhmm(cfg["schedule"]["decision_time"])
    plug_in, plug_out = _hhmm(cfg["ev"]["plug_in"]), _hhmm(cfg["ev"]["plug_out"])
    p0, p1 = _hhmm(cfg["schedule"]["peak_start"]), _hhmm(cfg["schedule"]["peak_end"])

    # Iterate over naive local calendar dates: adding 24h to a tz-aware timestamp drifts by an
    # hour across a clock change, which would silently move the plug-in and peak windows.
    first = df["local"].iloc[0].tz_localize(None).normalize()
    last = df["local"].iloc[-1].tz_localize(None).normalize()
    out = []
    for day in pd.date_range(first, last, freq="D"):
        t0 = (day + pd.Timedelta(minutes=dec)).tz_localize(tz)
        t1 = (day + pd.Timedelta(days=1, minutes=dec)).tz_localize(tz)
        day = day.tz_localize(tz)
        if t0 < df.index[0] or t1 > df.index[-1] + pd.Timedelta(minutes=30):
            continue
        w = df.loc[(df.index >= t0) & (df.index < t1)]
        mins = (w["local"].dt.hour * 60 + w["local"].dt.minute).to_numpy()
        same_day = (w["local"].dt.normalize() == w["local"].iloc[0].normalize()).to_numpy()
        plugged = (same_day & (mins >= plug_in)) | (~same_day & (mins < plug_out))
        peak = (mins >= p0) & (mins < p1)
        out.append(Window(day=day, data=w, plugged=plugged, peak=peak))
    return out


def empty_schedule(w: Window) -> pd.DataFrame:
    s = pd.DataFrame(index=w.data.index)
    for c in ("ev_kwh", "bat_charge_kwh", "bat_discharge_kwh", "soc_kwh"):
        s[c] = 0.0
    return s


def finish_schedule(s: pd.DataFrame, w: Window) -> pd.DataFrame:
    """Add grid import and copy across price/carbon/house for metrics."""
    s = s.copy()
    s["house_kwh"] = w.data["house_kwh"]
    s["price"] = w.data["price"]
    s["gco2"] = w.data["gco2"]
    s["peak"] = w.peak
    s["plugged"] = w.plugged
    s["grid_kwh"] = s["house_kwh"] + s["ev_kwh"] + s["bat_charge_kwh"] - s["bat_discharge_kwh"]
    s["cost_p"] = s["grid_kwh"] * s["price"]
    s["kgco2"] = s["grid_kwh"] * s["gco2"] / 1000
    return s

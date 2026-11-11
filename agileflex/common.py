"""Shared plumbing: config, cached data, the household load profile and daily planning windows."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent.parent  # repo root
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
        cfg = yaml.safe_load(f)
    ev = cfg["ev"]
    plug_h = ((_hhmm(ev["plug_out"]) - _hhmm(ev["plug_in"])) % (24 * 60)) / 60
    if ev["daily_need_kwh"] > plug_h * ev["charger_kw"]:
        raise ValueError(f"ev.daily_need_kwh={ev['daily_need_kwh']} cannot be delivered: a {ev['charger_kw']} kW "
                         f"charger adds at most {plug_h * ev['charger_kw']:.1f} kWh between "
                         f"{ev['plug_in']} and {ev['plug_out']}.")
    return cfg


def _hhmm(s) -> int:
    """'07:30' -> minutes after midnight. Also accepts an int: YAML 1.1 reads unquoted 18:00 as 1080."""
    if isinstance(s, int):
        return s
    h, m = str(s).split(":")
    return int(h) * 60 + int(m)


def house_profile(index_utc: pd.DatetimeIndex, cfg: dict) -> pd.Series:
    """Synthetic household load (kWh per half-hour), excluding the EV."""
    hc = cfg["house"]
    if hc.get("profile_csv"):
        p = pd.read_csv(hc["profile_csv"], parse_dates=["valid_from"]).set_index("valid_from")["house_kwh"]
        return p.reindex(index_utc).interpolate(limit_direction="both")
    local = index_utc.tz_convert(cfg["data"]["timezone"])
    shape = _HOURLY_SHAPE[local.hour] / _HOURLY_SHAPE.mean()
    doy = local.day_of_year.to_numpy()
    season = 1 + hc["seasonal_swing"] * np.cos(2 * np.pi * (doy - 15) / 365.25)  # averages 1 over a year
    # Scale against the annual mean, not this index's own sum, so a one-day window keeps its season.
    kwh = shape * season * hc["annual_kwh"] / (365.25 * 48)
    return pd.Series(kwh, index=index_utc, name="house_kwh")


def load_data(cfg: dict | None = None) -> pd.DataFrame:
    """Cached prices + carbon + house load on one half-hourly UTC index."""
    cfg = cfg or load_config()
    region = cfg["data"]["region"]
    price = pd.read_csv(DATA / f"agile_{region}.csv", parse_dates=["valid_from"]).set_index("valid_from")
    carbon = pd.read_csv(DATA / f"carbon_region{cfg['data']['carbon_region_id']}.csv", parse_dates=["valid_from"]).set_index("valid_from")
    start = pd.Timestamp(cfg["data"]["start"], tz="UTC")
    end = pd.Timestamp(cfg["data"]["end"], tz="UTC")
    idx = pd.date_range(start, end, freq="30min", inclusive="left")
    df = frame(idx, price["price_p_per_kwh"].reindex(idx), carbon["gco2_per_kwh"].reindex(idx), cfg)
    if df[["price", "gco2"]].isna().any().any():
        raise ValueError("Cached data has gaps for the configured period; run `python -m agileflex fetch --refresh`.")
    return df


@dataclass
class Window:
    """One daily planning window (decision time -> decision time next day, local)."""
    day: pd.Timestamp          # local date the window starts
    data: pd.DataFrame         # slice of load_data() for the window
    plugged: np.ndarray        # bool: EV is plugged in during this slot
    peak: np.ndarray           # bool: slot is in the 16:00-19:00 peak


def make_window(data: pd.DataFrame, cfg: dict) -> Window:
    """Wrap any contiguous slice of half-hours as a planning Window. The EV is plugged in from
    plug_in on the window's first local date until plug_out the next morning; the peak mask is
    by local wall-clock time. Works for the back-test (16:00 start) and the planner (18:00)."""
    plug_in, plug_out = _hhmm(cfg["ev"]["plug_in"]), _hhmm(cfg["ev"]["plug_out"])
    p0, p1 = _hhmm(cfg["schedule"]["peak_start"]), _hhmm(cfg["schedule"]["peak_end"])
    local = data["local"]
    mins = (local.dt.hour * 60 + local.dt.minute).to_numpy()
    first_date = local.iloc[0].normalize()
    same_day = (local.dt.normalize() == first_date).to_numpy()
    next_day = (local.dt.normalize() == first_date + pd.Timedelta(days=1)).to_numpy()
    plugged = (same_day & (mins >= plug_in)) | (next_day & (mins < plug_out))
    peak = (mins >= p0) & (mins < p1)
    return Window(day=first_date, data=data, plugged=plugged, peak=peak)


def windows(df: pd.DataFrame, cfg: dict) -> list[Window]:
    """Split the year into daily decision-time windows. DST days give 46 or 50 slots."""
    tz = cfg["data"]["timezone"]
    dec = _hhmm(cfg["schedule"]["decision_time"])
    # Iterate over naive local calendar dates: adding 24h to a tz-aware timestamp drifts by an
    # hour across a clock change, which would silently move the plug-in and peak windows.
    first = df["local"].iloc[0].tz_localize(None).normalize()
    last = df["local"].iloc[-1].tz_localize(None).normalize()
    out = []
    for day in pd.date_range(first, last, freq="D"):
        t0 = (day + pd.Timedelta(minutes=dec)).tz_localize(tz)
        t1 = (day + pd.Timedelta(days=1, minutes=dec)).tz_localize(tz)
        if t0 < df.index[0] or t1 > df.index[-1] + pd.Timedelta(minutes=30):
            continue
        out.append(make_window(df.loc[(df.index >= t0) & (df.index < t1)], cfg))
    return out


def frame(index_utc: pd.DatetimeIndex, price, gco2, cfg: dict) -> pd.DataFrame:
    """Build the standard half-hour frame (price, gco2, house_kwh, local) for any index."""
    df = pd.DataFrame({"price": np.asarray(price, dtype=float), "gco2": np.asarray(gco2, dtype=float)},
                      index=index_utc)
    df["house_kwh"] = house_profile(index_utc, cfg)
    df["local"] = index_utc.tz_convert(cfg["data"]["timezone"])
    return df


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

"""Test-only synthetic market: Agile-like prices and carbon for offline tests of the planner and web
app (fake API responses). Never used for results; the back-test runs on real cached data."""
import numpy as np
import pandas as pd


def _season(local: pd.DatetimeIndex) -> np.ndarray:
    """1 in mid-January (winter), 0 in mid-July (summer)."""
    return (np.cos(2 * np.pi * (local.day_of_year.values - 15) / 365) + 1) / 2


def synthetic_market(start: str, end: str, seed: int = 42, tz: str = "Europe/London") -> pd.DataFrame:
    """Cheap overnight, midday solar dip, 16:00-19:00 peak, windy spells cheaper, occasional negatives."""
    idx = pd.date_range(pd.Timestamp(start, tz=tz), pd.Timestamp(end, tz=tz), freq="30min", inclusive="left")
    rng = np.random.default_rng(seed)
    hod = idx.hour.values + idx.minute.values / 60
    winter = _season(idx)
    days = idx.normalize()
    w, wind_by_day = 0.0, {}
    for d in days.unique():  # AR(1) weather: windy spells last a few days
        w = 0.65 * w + rng.normal(0, 1)
        wind_by_day[d] = 1 / (1 + np.exp(-1.6 * w))
    wind = np.array([wind_by_day[d] for d in days])
    shape = np.select([hod < 5, hod < 7, hod < 9, hod < 11, hod < 15, hod < 16, hod < 19, hod < 23],
                      [-5, -2, 4, 1, -3, 2, 14, 3], default=-3).astype(float)
    shape -= np.where((hod >= 11) & (hod < 15), 3 * (1 - winter), 0)
    gust = np.clip(wind - 0.85, 0, None) / 0.15
    price = 20 + 6 * winter + shape - 10 * wind - 8 * gust + rng.normal(0, 1.5, len(idx))
    carbon = (150 + 120 * winter + np.where((hod >= 16) & (hod < 19), 40, 0)
              - np.where((hod >= 10) & (hod < 16), 40 * (1 - winter), 0) - 160 * wind + rng.normal(0, 15, len(idx)))
    return pd.DataFrame({"price_p_kwh": np.clip(price, -15, 100).round(2),
                         "carbon_g_kwh": np.clip(carbon, 25, 450).round(0)}, index=idx.tz_convert("UTC"))

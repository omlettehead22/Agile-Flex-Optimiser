"""Stretch: what does the optimiser lose if it must plan on a price *forecast*?

Scenario: the plan for 16:00 -> 16:00 is committed before Octopus publishes tomorrow's
Agile prices, so it only knows prices up to the decision time. The forecaster is a ridge
regression refit every day on a rolling window, using only information available at 16:00:

  * the same half-hour yesterday and one week ago (lags of 48 and 336 slots)
  * the Carbon Intensity API's own regional *forecast* for that half-hour (it is published
    ~48h ahead, and it tracks wind and gas on the margin, which drive Agile)
  * time-of-day dummies and a weekend flag

The plan is then scored on the actual prices. Regret = cost(plan on forecast) - cost(plan on actuals).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import Window

LAGS = (48, 336)


def features(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    for lag in LAGS:
        f[f"lag{lag}"] = df["price"].shift(lag)
    f["gco2"] = df["gco2"]
    slot = df["local"].dt.hour * 2 + df["local"].dt.minute // 30
    for k in range(1, 48):  # slot 0 is the baseline category
        f[f"s{k}"] = (slot == k).astype(float)
    f["weekend"] = (df["local"].dt.dayofweek >= 5).astype(float)
    return f


def _ridge(X: np.ndarray, y: np.ndarray, alpha: float):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    Z = (X - mu) / sd
    Z1 = np.c_[np.ones(len(Z)), Z]
    reg = alpha * np.eye(Z1.shape[1])
    reg[0, 0] = 0
    beta = np.linalg.solve(Z1.T @ Z1 + reg, Z1.T @ (y - 0))
    return lambda Xn: np.c_[np.ones(len(Xn)), (Xn - mu) / sd] @ beta


def forecast_windows(df: pd.DataFrame, wins: list[Window], cfg: dict) -> dict[pd.Timestamp, dict]:
    """Return {window day: {'ridge': forecast array, 'naive': yesterday's prices}} for every
    window with enough history. Training data always ends strictly before the window opens."""
    fc = cfg["forecast"]
    F = features(df)
    hist = pd.Timedelta(days=fc["train_days"])
    out = {}
    for w in wins:
        t0 = w.data.index[0]
        train = F[(F.index < t0) & (F.index >= t0 - hist)].dropna()
        if len(train) < 0.9 * fc["train_days"] * 48 or train.index[0] - df.index[0] < pd.Timedelta(days=7):
            continue
        model = _ridge(train.to_numpy(), df.loc[train.index, "price"].to_numpy(), fc["ridge_alpha"])
        Xw = F.loc[w.data.index]
        out[w.day] = {"ridge": model(Xw.to_numpy()), "naive": Xw["lag48"].to_numpy()}
    return out

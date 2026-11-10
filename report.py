"""Run every strategy over the year, compute metrics and draw the charts.

    python report.py            # full run, writes outputs/
    python report.py --no-forecast
"""
from __future__ import annotations

import argparse
import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import OUT, load_config, load_data, windows
from strategies import STRATEGIES, optimised, rule_based, run

# Colours: categorical slots from the reference palette, baseline in neutral grey.
COL = {"Baseline": "#8f8e88", "Rule-based": "#eb6834", "Optimised": "#2a78d6",
       "Optimised + carbon": "#1baf7a", "Optimised, EV only": "#6250d6"}
INK, INK2, GRID, PEAK = "#0b0b0b", "#52514e", "#e7e6e2", "#f1efe9"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": INK2, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "figure.dpi": 150, "savefig.bbox": "tight", "legend.frameon": False,
})


# --------------------------------------------------------------------------- metrics
def metrics(s: pd.DataFrame, base: pd.DataFrame) -> dict:
    flex = s["ev_kwh"] + s["bat_charge_kwh"]
    neg = s["price"] < 0
    used = s["house_kwh"] + s["ev_kwh"]
    cost = s["cost_p"].sum() / 100
    base_cost = base["cost_p"].sum() / 100
    return {
        "Annual cost (GBP)": round(cost, 2),
        "Saving vs baseline (GBP)": round(base_cost - cost, 2),
        "Saving vs baseline (%)": round(100 * (base_cost - cost) / base_cost, 1),
        "Cost per kWh used (p)": round(100 * cost / used.sum(), 2),
        "Emissions (kgCO2)": round(s["kgco2"].sum(), 1),
        "CO2 saved vs baseline (kg)": round(base["kgco2"].sum() - s["kgco2"].sum(), 1),
        "Peak 16-19 import (kWh)": round(s.loc[s["peak"], "grid_kwh"].sum(), 1),
        "kWh moved out of 16-19 peak": round(base.loc[base["peak"], "grid_kwh"].sum()
                                             - s.loc[s["peak"], "grid_kwh"].sum(), 1),
        "Negative-price half-hours used": int((neg & (flex > 1e-6)).sum()),
        "kWh bought at negative prices": round(flex[neg].sum(), 1),
        "Battery throughput (kWh)": round(s["bat_discharge_kwh"].sum(), 1),
    }


def run_all(cfg, df, wins, verbose=True):
    sched = {}
    for name, fn in STRATEGIES.items():
        if verbose:
            print(f"  running {name} ...", flush=True)
        sched[name] = run(fn, wins, cfg)
    # Attribution: the same optimiser with the home battery removed.
    import copy
    ev_only = copy.deepcopy(cfg)
    ev_only["battery"]["capacity_kwh"] = 0
    if verbose:
        print("  running Optimised, EV only (no battery) ...", flush=True)
    sched["Optimised, EV only"] = run(optimised, wins, ev_only)
    w = cfg["optimiser"]["carbon_variant_weight_p_per_kg"]
    if w:
        if verbose:
            print(f"  running Optimised + carbon ({w} p/kgCO2) ...", flush=True)
        sched["Optimised + carbon"] = run(optimised, wins, cfg, carbon_weight=w)
    return sched


# --------------------------------------------------------------------------- charts
def typical_winter_window(wins, cfg):
    """The Dec-Feb day whose optimised saving is the median winter saving (ties -> earliest)."""
    cand = [w for w in wins if w.day.month in (12, 1, 2) and len(w.data) == 48]
    sav = np.array([STRATEGIES["Baseline"](w, cfg)["cost_p"].sum() - optimised(w, cfg)["cost_p"].sum()
                    for w in cand])
    return cand[int(np.argmin(np.abs(sav - np.median(sav))))]


def chart_day(w, cfg, path):
    tz = cfg["data"]["timezone"]
    sb, sr, so = (f(w, cfg) for f in (STRATEGIES["Baseline"], rule_based, optimised))
    t = w.data["local"].dt.tz_localize(None)
    edges = list(t) + [t.iloc[-1] + pd.Timedelta(minutes=30)]
    kw = 2.0  # kWh per half-hour -> kW

    fig, ax = plt.subplots(3, 1, figsize=(10, 7.2), sharex=True,
                           gridspec_kw={"height_ratios": [1.1, 1, 1], "hspace": 0.18})
    for a in ax:
        for i, pk in enumerate(w.peak):
            if pk:
                a.axvspan(edges[i], edges[i + 1], color=PEAK, lw=0, zorder=0)

    ax[0].stairs(w.data["price"], edges, color=INK, lw=2, baseline=None)
    ax[0].set_ylabel("Agile price\n(p/kWh)")
    lo, hi = min(0, w.data["price"].min()) - 2, w.data["price"].max() * 1.25
    ax[0].set_ylim(lo, hi)
    if lo < 0:
        ax[0].axhline(0, color=INK2, lw=0.8)
    ax[0].text(edges[0] + pd.Timedelta(minutes=10), hi * 0.97, "16:00-19:00 peak", color=INK2, fontsize=9, va="top")

    ax[1].stairs(sb["ev_kwh"] * kw, edges, color=COL["Baseline"], lw=2, label="Baseline: charge on arrival")
    ax[1].stairs(so["ev_kwh"] * kw, edges, color=COL["Optimised"], lw=2, label="Optimised")
    ax[1].stairs(sr["ev_kwh"] * kw, edges, color=COL["Rule-based"], lw=2, ls=(0, (3, 3)), label="Rule-based")
    ax[1].set_ylabel("EV charging\n(kW)")
    ax[1].set_ylim(0, cfg["ev"]["charger_kw"] * 1.25)
    ax[1].legend(loc="upper right", ncol=3, fontsize=9)

    cap = cfg["battery"]["capacity_kwh"]
    soc_t = list(t) + [edges[-1]]
    s0 = cfg["battery"]["start_soc_frac"] * cap
    for s, name, ls in ((sr, "Rule-based", "--"), (so, "Optimised", "-")):
        ax[2].plot(soc_t, 100 * np.r_[s0, s["soc_kwh"]] / cap, color=COL[name], lw=2, ls=ls, label=name)
    ax[2].set_ylabel("Home battery\ncharge (%)")
    ax[2].set_ylim(-5, 110)
    ax[2].legend(loc="lower right", ncol=2, fontsize=9)
    ax[2].xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax[2].xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 2)))

    cb, cr, co = (x["cost_p"].sum() / 100 for x in (sb, sr, so))
    fig.suptitle(f"A typical winter day, {w.day:%a %d %b %Y} (16:00 to 16:00, London)", x=0.07, ha="left",
                 fontsize=13, color=INK, y=0.985)
    fig.text(0.07, 0.935, f"Same house, same car, same prices. Day's bill: baseline £{cb:.2f}, "
             f"rule-based £{cr:.2f}, optimised £{co:.2f}", ha="left", color=INK2, fontsize=10)
    fig.savefig(path)
    plt.close(fig)
    return {"day": str(w.day.date()), "baseline_gbp": round(cb, 2), "rule_based_gbp": round(cr, 2),
            "optimised_gbp": round(co, 2)}


def chart_monthly(sched, path):
    base = sched["Baseline"].groupby(sched["Baseline"]["day"].dt.strftime("%Y-%m"))["cost_p"].sum() / 100
    fig, ax = plt.subplots(figsize=(10, 3.6))
    x = np.arange(len(base))
    width = 0.27
    for k, name in enumerate(["Baseline", "Rule-based", "Optimised"]):
        m = sched[name].groupby(sched[name]["day"].dt.strftime("%Y-%m"))["cost_p"].sum() / 100
        ax.bar(x + (k - 1) * width, m.values, width - 0.03, color=COL[name], label=name)
    ax.set_xticks(x, [pd.Timestamp(m + "-01").strftime("%b\n%y") for m in base.index])
    ax.set_ylabel("Monthly bill (GBP)\nexcl. standing charge")
    ax.grid(axis="x", visible=False)
    ax.legend(ncol=3, loc="lower left", bbox_to_anchor=(0, 1.0))
    ax.set_title("Household electricity cost by month", loc="left", color=INK, pad=28)
    fig.savefig(path)
    plt.close(fig)


# --------------------------------------------------------------------------- forecast stretch
def forecast_study(cfg, df, wins):
    from forecast import forecast_windows
    print("  forecasting prices (walk-forward ridge) ...", flush=True)
    fc = forecast_windows(df, wins, cfg)
    days = [w for w in wins if w.day in fc]
    rows, err = {}, {"ridge": [], "naive": []}
    plans = {"Optimised, actual prices": lambda w: optimised(w, cfg),
             "Optimised, ridge forecast": lambda w: optimised(w, cfg, price=fc[w.day]["ridge"]),
             "Optimised, naive forecast (yesterday)": lambda w: optimised(w, cfg, price=fc[w.day]["naive"]),
             "Rule-based, actual prices": lambda w: rule_based(w, cfg),
             "Baseline": lambda w: STRATEGIES["Baseline"](w, cfg)}
    for name, f in plans.items():
        rows[name] = sum(f(w)["cost_p"].sum() for w in days) / 100
    for w in days:
        a = w.data["price"].to_numpy()
        for k in err:
            err[k].append(np.abs(fc[w.day][k] - a))
    perfect = rows["Optimised, actual prices"]
    base = rows["Baseline"]
    out = {
        "days": len(days), "from": str(days[0].day.date()), "to": str(days[-1].day.date()),
        "mae_ridge_p": round(float(np.concatenate(err["ridge"]).mean()), 2),
        "mae_naive_p": round(float(np.concatenate(err["naive"]).mean()), 2),
        "cost_gbp": {k: round(v, 2) for k, v in rows.items()},
    }
    out["regret_ridge_gbp"] = round(rows["Optimised, ridge forecast"] - perfect, 2)
    out["regret_naive_gbp"] = round(rows["Optimised, naive forecast (yesterday)"] - perfect, 2)
    out["share_of_saving_kept_ridge_pct"] = round(100 * (base - rows["Optimised, ridge forecast"]) / (base - perfect), 1)
    out["share_of_saving_kept_naive_pct"] = round(100 * (base - rows["Optimised, naive forecast (yesterday)"]) / (base - perfect), 1)
    return out


# --------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--no-forecast", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    df = load_data(cfg)
    wins = windows(df, cfg)
    OUT.mkdir(exist_ok=True)
    print(f"{len(wins)} daily windows, {wins[0].day.date()} to {wins[-1].day.date()}")

    sched = run_all(cfg, df, wins)
    base = sched["Baseline"]
    table = pd.DataFrame({k: metrics(v, base) for k, v in sched.items()})
    table.to_csv(OUT / "metrics.csv")
    (OUT / "metrics.md").write_text(table.to_markdown())
    allsched = pd.concat({k: v for k, v in sched.items()}, names=["strategy", "valid_from"])
    num = allsched.select_dtypes("number").columns
    allsched[num] = allsched[num].round(4)
    allsched.drop(columns=["peak", "plugged"]).to_csv(OUT / "schedules.csv.gz")

    analysed = pd.concat([w.data for w in wins])
    results = {
        "period": f"{wins[0].day.date()} to {wins[-1].day.date()}",
        "days": len(wins),
        "negative_price_half_hours": int((analysed["price"] < 0).sum()),
        "metrics": table.to_dict(),
        "typical_winter_day": chart_day(typical_winter_window(wins, cfg), cfg, OUT / "headline_winter_day.png"),
    }
    chart_monthly(sched, OUT / "monthly_cost.png")
    if cfg["forecast"]["enabled"] and not args.no_forecast:
        results["forecast"] = forecast_study(cfg, df, wins)
    (OUT / "results.json").write_text(json.dumps(results, indent=2, default=str))

    print("\n" + table.to_string())
    if "forecast" in results:
        print("\nForecast study:", json.dumps(results["forecast"], indent=2))
    return results


if __name__ == "__main__":
    main()

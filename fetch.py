"""Download Agile prices and regional carbon intensity to cached CSVs in data/.

    python fetch.py            # uses the cache if it already covers the configured period
    python fetch.py --refresh  # re-download

Both APIs are public and need no key. The repo ships with the cache, so you only need this
to change region or period.
"""
from __future__ import annotations

import argparse
import sys
import time

import pandas as pd
import requests

from common import DATA, load_config

OCTOPUS = "https://api.octopus.energy/v1"
CARBON = "https://api.carbonintensity.org.uk"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "agile-flex (github.com) research script"


def _get(url: str, params: dict | None = None, tries: int = 4) -> dict:
    for k in range(tries):
        r = SESSION.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        time.sleep(2 ** k)
    r.raise_for_status()
    return {}


# ----------------------------------------------------------------------------- Octopus
def pick_agile_product(products: list[dict]) -> str:
    """Newest AGILE import product that is still on sale. Product codes change (e.g.
    AGILE-FLEX-22-11-25 -> AGILE-23-12-06 -> AGILE-24-10-01), so never hard-code one."""
    agile = [p for p in products
             if p["code"].startswith("AGILE") and "OUTGOING" not in p["code"]
             and p.get("direction", "IMPORT") == "IMPORT" and p.get("available_to") is None]
    if not agile:
        raise RuntimeError("No current AGILE import product found in /v1/products/")
    return max(agile, key=lambda p: p["available_from"])["code"]


def tariff_code(product: dict, region: str) -> str:
    return product["single_register_electricity_tariffs"][f"_{region}"]["direct_debit_monthly"]["code"]


def fetch_agile(cfg: dict) -> pd.DataFrame:
    d = cfg["data"]
    code = d["agile_product"]
    if code == "auto":
        products, url = [], f"{OCTOPUS}/products/"
        params = {"is_variable": "true", "brand": "OCTOPUS_ENERGY", "page_size": 1500}
        while url:
            j = _get(url, params)
            products += j["results"]
            url, params = j.get("next"), None
        code = pick_agile_product(products)
    tc = tariff_code(_get(f"{OCTOPUS}/products/{code}/"), d["region"])
    print(f"Agile product {code}, tariff {tc}")

    url = f"{OCTOPUS}/products/{code}/electricity-tariffs/{tc}/standard-unit-rates/"
    params = {"period_from": f"{d['start']}T00:00:00Z", "period_to": f"{d['end']}T00:00:00Z", "page_size": 1500}
    rows = []
    while url:
        j = _get(url, params)
        rows += j["results"]
        url, params = j.get("next"), None
    return parse_agile(rows, d["start"], d["end"])


def parse_agile(rows: list[dict], start: str, end: str) -> pd.DataFrame:
    s = pd.Series({pd.Timestamp(r["valid_from"]).tz_convert("UTC"): r["value_inc_vat"] for r in rows}).sort_index()
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="30min", inclusive="left")
    s = s.reindex(idx)
    if s.isna().any():
        raise RuntimeError(f"{int(s.isna().sum())} half-hours missing from Agile data")
    return pd.DataFrame({"valid_from": idx, "price_p_per_kwh": s.values})


# ----------------------------------------------------------------------------- Carbon
def fetch_carbon(cfg: dict) -> pd.DataFrame:
    d = cfg["data"]
    start, end = pd.Timestamp(d["start"], tz="UTC"), pd.Timestamp(d["end"], tz="UTC")
    rows, t = [], start
    while t < end:  # the regional endpoint serves at most 14 days per call
        e = min(t + pd.Timedelta(days=13), end)
        f = (t + pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%MZ")
        j = _get(f"{CARBON}/regional/intensity/{f}/{e:%Y-%m-%dT%H:%MZ}/regionid/{d['carbon_region_id']}")
        rows += j.get("data", {}).get("data", [])
        t = e
    return parse_carbon(rows, d["start"], d["end"])


def parse_carbon(rows: list[dict], start: str, end: str) -> pd.DataFrame:
    """The API has occasional multi-day holes (e.g. 1-2 Jan 2026 for London). Fill them with
    the average for that half-hour over the surrounding +-9 days, and flag them."""
    s = pd.Series({pd.Timestamp(r["from"]).tz_convert("UTC"): r["intensity"]["forecast"] for r in rows},
                  dtype=float).sort_index()
    s = s[~s.index.duplicated()]
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="30min", inclusive="left")
    s = s.reindex(idx)
    miss = s.isna()
    if miss.any():
        slot = idx.hour * 2 + idx.minute // 30
        for ts in idx[miss]:
            near = s[(idx >= ts - pd.Timedelta(days=9)) & (idx <= ts + pd.Timedelta(days=9))
                     & (slot == ts.hour * 2 + ts.minute // 30)]
            s[ts] = round(near.mean(), 1) if near.notna().any() else float("nan")
        s = s.interpolate(limit_direction="both")
    return pd.DataFrame({"valid_from": idx, "gco2_per_kwh": s.values, "interpolated": miss.values})


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--config", default=None)
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    DATA.mkdir(exist_ok=True)

    targets = {DATA / f"agile_{cfg['data']['region']}.csv": fetch_agile,
               DATA / f"carbon_region{cfg['data']['carbon_region_id']}.csv": fetch_carbon}
    for path, fn in targets.items():
        if path.exists() and not args.refresh:
            have = pd.read_csv(path, parse_dates=["valid_from"])["valid_from"]
            if have.min() <= pd.Timestamp(cfg["data"]["start"], tz="UTC") and \
               have.max() >= pd.Timestamp(cfg["data"]["end"], tz="UTC") - pd.Timedelta(minutes=30):
                print(f"cached: {path.name} ({len(have)} rows)")
                continue
        df = fn(cfg)
        df.to_csv(path, index=False)
        print(f"wrote {path.name} ({len(df)} rows)")


if __name__ == "__main__":
    sys.exit(main())

"""Octopus Agile and Carbon Intensity API access.

    python -m agileflex fetch            # cache a year of data in data/ (skips if already cached)
    python -m agileflex fetch --refresh  # re-download

Both APIs are public and need no key. The repo ships with the cache, so you only need this to
change region or period. Every function that touches the network takes a `get(url, params)`
callable, so the planner, the web app and the tests can swap in a fake.
"""
from __future__ import annotations

import time

import pandas as pd
import requests

from .common import DATA, load_config

OCTOPUS_API = "https://api.octopus.energy/v1"
CARBON_API = "https://api.carbonintensity.org.uk"
_SESSION = requests.Session()
_SESSION.headers["User-Agent"] = "agile-flex-optimiser (github.com/omlettehead22/Agile-Flex-Optimiser)"


def http_get(url: str, params: dict | None = None, tries: int = 4) -> dict:
    for k in range(tries):
        r = _SESSION.get(url, params=params, timeout=30)
        if r.status_code == 200:
            return r.json()
        if r.status_code < 500 and r.status_code != 429:
            break
        time.sleep(2 ** k)
    r.raise_for_status()
    return {}


def _paginate(url: str, params: dict | None, get=http_get) -> list[dict]:
    rows, data = [], get(url, params)
    while True:
        rows.extend(data.get("results", []))
        nxt = data.get("next")
        if not nxt:
            return rows
        data = get(nxt, None)


def _iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%MZ")


# ----------------------------------------------------------------------------- Octopus
def pick_agile_product(products: list[dict]) -> str:
    """Newest AGILE import product, preferring ones still on sale. Product codes change
    (AGILE-FLEX-22-11-25 -> AGILE-23-12-06 -> AGILE-24-10-01), so never hard-code one."""
    agile = [p for p in products
             if p.get("code", "").startswith("AGILE") and "OUTGOING" not in p["code"]
             and p.get("direction", "IMPORT") == "IMPORT"]
    if not agile:
        raise RuntimeError("No AGILE import product found in /v1/products/")
    on_sale = [p for p in agile if p.get("available_to") is None] or agile
    return max(on_sale, key=lambda p: p.get("available_from") or "")["code"]


def find_agile_product(get=http_get) -> str:
    return pick_agile_product(_paginate(f"{OCTOPUS_API}/products/", None, get))


def tariff_code(product: dict, region: str) -> str:
    """Tariff code from a product's detail payload (raises KeyError if the region is missing)."""
    return product["single_register_electricity_tariffs"][f"_{region}"]["direct_debit_monthly"]["code"]


def tariff_code_for(product_code: str, region: str, get=http_get) -> str:
    """Look the tariff code up; fall back to Octopus's E-1R-<product>-<region> pattern."""
    detail = get(f"{OCTOPUS_API}/products/{product_code}/", None)
    entry = detail.get("single_register_electricity_tariffs", {}).get(f"_{region}", {})
    for payment in ("direct_debit_monthly", "varying"):
        if entry.get(payment, {}).get("code"):
            return entry[payment]["code"]
    return f"E-1R-{product_code}-{region}"


def parse_rates(rows: list[dict]) -> pd.DataFrame:
    """API rows (newest first) -> DataFrame indexed by UTC slot start, column price_p_kwh."""
    if not rows:
        return pd.DataFrame(columns=["price_p_kwh"], index=pd.DatetimeIndex([], tz="UTC"))
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["valid_from"], utc=True)
    df = df.drop_duplicates("timestamp").set_index("timestamp").sort_index()
    return df[["value_inc_vat"]].rename(columns={"value_inc_vat": "price_p_kwh"}).astype(float)


def fetch_agile_prices(product_code: str, tariff: str, start, end, get=http_get) -> pd.DataFrame:
    url = f"{OCTOPUS_API}/products/{product_code}/electricity-tariffs/{tariff}/standard-unit-rates/"
    params = {"period_from": _iso(start), "period_to": _iso(end), "page_size": 1500}
    return parse_rates(_paginate(url, params, get))


def fetch_agile_year(cfg: dict, get=http_get) -> pd.DataFrame:
    d = cfg["data"]
    code = find_agile_product(get) if d["agile_product"] == "auto" else d["agile_product"]
    tc = tariff_code_for(code, d["region"], get)
    print(f"Agile product {code}, tariff {tc}")
    rows = fetch_agile_prices(code, tc, pd.Timestamp(d["start"], tz="UTC"), pd.Timestamp(d["end"], tz="UTC"), get)
    return to_cache_frame(rows, d["start"], d["end"])


def to_cache_frame(rates: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="30min", inclusive="left")
    s = rates["price_p_kwh"].reindex(idx)
    if s.isna().any():
        raise RuntimeError(f"{int(s.isna().sum())} half-hours missing from Agile data. The product may not cover "
                           "the whole period: set data.agile_product to an older code (e.g. AGILE-23-12-06).")
    return pd.DataFrame({"valid_from": idx, "price_p_per_kwh": s.values})


def parse_agile(rows: list[dict], start: str, end: str) -> pd.DataFrame:
    """Raw API rows -> gap-checked cache frame."""
    return to_cache_frame(parse_rates(rows), start, end)


# ----------------------------------------------------------------------------- Carbon
def carbon_rows(region_id: int, start, end, get=http_get) -> list[dict]:
    """Raw regional intensity rows. The regional endpoint serves at most 14 days per call."""
    start, end = pd.Timestamp(start).tz_convert("UTC"), pd.Timestamp(end).tz_convert("UTC")
    rows, t = [], start
    while t < end:
        e = min(t + pd.Timedelta(days=13), end)
        f = (t + pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%MZ")
        payload = get(f"{CARBON_API}/regional/intensity/{f}/{e:%Y-%m-%dT%H:%MZ}/regionid/{region_id}", None)
        data = payload.get("data", {})
        if isinstance(data, list):  # the API has returned both shapes
            data = data[0] if data else {}
        rows += data.get("data", [])
        t = e
    return rows


def fetch_carbon_series(region_id: int, start, end, get=http_get) -> pd.Series:
    """Half-hourly gCO2/kWh forecast for a short window (used by the daily planner)."""
    s = pd.Series({pd.Timestamp(r["from"]).tz_convert("UTC"): (r.get("intensity") or {}).get("forecast")
                   for r in carbon_rows(region_id, start, end, get)}, dtype=float)
    return s[~s.index.duplicated()].sort_index()


def fetch_carbon_year(cfg: dict, get=http_get) -> pd.DataFrame:
    d = cfg["data"]
    return parse_carbon(carbon_rows(d["carbon_region_id"], pd.Timestamp(d["start"], tz="UTC"),
                                    pd.Timestamp(d["end"], tz="UTC"), get), d["start"], d["end"])


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


def cache_year(cfg: dict | None = None, refresh: bool = False, get=http_get) -> None:
    cfg = cfg or load_config()
    DATA.mkdir(exist_ok=True)
    targets = {DATA / f"agile_{cfg['data']['region']}.csv": fetch_agile_year,
               DATA / f"carbon_region{cfg['data']['carbon_region_id']}.csv": fetch_carbon_year}
    for path, fn in targets.items():
        if path.exists() and not refresh:
            have = pd.read_csv(path, parse_dates=["valid_from"])["valid_from"]
            if have.min() <= pd.Timestamp(cfg["data"]["start"], tz="UTC") and \
               have.max() >= pd.Timestamp(cfg["data"]["end"], tz="UTC") - pd.Timedelta(minutes=30):
                print(f"cached: {path.name} ({len(have)} rows)")
                continue
        df = fn(cfg, get)
        df.to_csv(path, index=False)
        print(f"wrote {path.name} ({len(df)} rows)")

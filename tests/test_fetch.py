"""Offline tests for the API parsing (no network needed)."""
import pandas as pd
import pytest

from agileflex.fetch import parse_agile, parse_carbon, pick_agile_product, tariff_code


def test_pick_current_agile_import_product():
    products = [
        {"code": "AGILE-23-12-06", "direction": "IMPORT", "available_from": "2023-12-06", "available_to": "2024-10-01"},
        {"code": "AGILE-24-10-01", "direction": "IMPORT", "available_from": "2024-10-01", "available_to": None},
        {"code": "AGILE-OUTGOING-19-05-13", "direction": "EXPORT", "available_from": "2018-01-01", "available_to": None},
        {"code": "VAR-22-11-01", "direction": "IMPORT", "available_from": "2022-11-01", "available_to": None},
    ]
    assert pick_agile_product(products) == "AGILE-24-10-01"


def test_tariff_code_for_region():
    prod = {"single_register_electricity_tariffs": {"_C": {"direct_debit_monthly": {"code": "E-1R-AGILE-24-10-01-C"}}}}
    assert tariff_code(prod, "C") == "E-1R-AGILE-24-10-01-C"


def test_parse_agile_sorts_and_checks_gaps():
    rows = [{"valid_from": "2026-01-01T00:30:00Z", "value_inc_vat": 2.0},
            {"valid_from": "2026-01-01T00:00:00Z", "value_inc_vat": 1.0}]
    df = parse_agile(rows, "2026-01-01T00:00", "2026-01-01T01:00")
    assert df["price_p_per_kwh"].tolist() == [1.0, 2.0]
    with pytest.raises(RuntimeError):
        parse_agile(rows[:1], "2026-01-01T00:00", "2026-01-01T01:00")


def test_parse_carbon_fills_and_flags_gaps():
    idx = pd.date_range("2026-01-01", periods=48 * 5, freq="30min", tz="UTC")
    rows = [{"from": t.strftime("%Y-%m-%dT%H:%MZ"), "intensity": {"forecast": 100 + (t.hour)}}
            for t in idx if t.day != 3]  # drop a whole day
    df = parse_carbon(rows, "2026-01-01", "2026-01-06")
    assert df["gco2_per_kwh"].notna().all()
    assert df["interpolated"].sum() == 48
    gap = df[df["interpolated"]]
    # same half-hour on neighbouring days -> same value
    assert (gap["gco2_per_kwh"].to_numpy() == 100 + gap["valid_from"].dt.hour.to_numpy()).all()


# --------------------------------------------------------------- helpers shared with the planner and web app
from agileflex.fetch import carbon_rows, fetch_agile_prices, find_agile_product, tariff_code_for  # noqa: E402


def test_rates_pagination_and_parsing():
    pages = {
        "first": {"results": [{"valid_from": "2026-01-01T00:30:00Z", "value_inc_vat": 2.0}], "next": "second"},
        "second": {"results": [{"valid_from": "2026-01-01T00:00:00Z", "value_inc_vat": 1.0}], "next": None},
    }
    calls = []

    def get(url, params=None):
        calls.append(url)
        return pages["second"] if url == "second" else pages["first"]

    df = fetch_agile_prices("P", "T", pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-01-02", tz="UTC"), get)
    assert df["price_p_kwh"].tolist() == [1.0, 2.0] and len(calls) == 2


def test_product_and_tariff_lookup_with_fallback():
    def get(url, params=None):
        if url.endswith("/products/"):
            return {"results": [{"code": "AGILE-24-10-01", "available_from": "2024-10-01"}], "next": None}
        return {"single_register_electricity_tariffs": {}}  # region missing -> documented pattern

    assert find_agile_product(get) == "AGILE-24-10-01"
    assert tariff_code_for("AGILE-24-10-01", "C", get) == "E-1R-AGILE-24-10-01-C"


@pytest.mark.parametrize("wrap", [dict, lambda d: [d]])
def test_carbon_both_payload_shapes_and_chunking(wrap):
    urls = []

    def get(url, params=None):
        urls.append(url)
        return {"data": wrap({"data": [{"from": "2026-01-01T00:00Z", "intensity": {"forecast": 100}}]})}

    rows = carbon_rows(13, pd.Timestamp("2026-01-01", tz="UTC"), pd.Timestamp("2026-02-01", tz="UTC"), get)
    assert len(urls) == 3 and all(u.endswith("/regionid/13") for u in urls)  # 13-day chunks
    assert rows[0]["intensity"]["forecast"] == 100

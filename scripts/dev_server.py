"""Run Off-peak locally on sample prices, with no network access: for UI work and screenshots.

    python scripts/dev_server.py                 # http://127.0.0.1:8001, clock fixed at 3 Dec 2025 14:10
    python scripts/dev_server.py --at "2025-12-03 21:40" --port 8002

Prices come from the synthetic market used by the tests, so every run looks the same.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
os.environ["OFFPEAK_NO_AUTOAPP"] = "1"

import pandas as pd  # noqa: E402
import uvicorn  # noqa: E402

from fakes import synthetic_market  # noqa: E402
from webapp.main import create_app  # noqa: E402

MARKET = synthetic_market("2025-11-01", "2026-02-01", seed=5)["price_p_kwh"]


def fake_get(url, params=None):
    if url.endswith("/v1/products/"):
        return {"results": [{"code": "AGILE-24-10-01", "direction": "IMPORT", "available_from": "2024-10-01"}],
                "next": None}
    if "/products/AGILE-24-10-01/" in url and "standard-unit-rates" not in url:
        return {"single_register_electricity_tariffs": {}}
    if "standard-unit-rates" in url:
        lo, hi = pd.Timestamp(params["period_from"]), pd.Timestamp(params["period_to"])
        sel = MARKET[(MARKET.index >= lo) & (MARKET.index < hi)]
        return {"results": [{"valid_from": t.isoformat(), "value_inc_vat": v} for t, v in sel.items()], "next": None}
    if "grid-supply-points" in url:
        return {"results": [{"group_id": "_C"}]}
    raise RuntimeError(f"dev server has no fake for {url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="2025-12-03 14:10", help="fixed UTC time the app thinks it is")
    ap.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    now = pd.Timestamp(args.at, tz="UTC")
    app = create_app("sqlite:///:memory:", get=fake_get, clock=lambda: now,
                     vapid_public_key="dev-key", cron_token="dev")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

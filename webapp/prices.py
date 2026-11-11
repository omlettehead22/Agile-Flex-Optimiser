"""Fetch and cache Agile prices per region, and map postcodes to regions."""
from __future__ import annotations

import re
import time

import pandas as pd

from agileflex.fetch import OCTOPUS_API, fetch_agile_prices, find_agile_product, tariff_code_for

REGIONS = {
    "A": "Eastern England", "B": "East Midlands", "C": "London", "D": "Merseyside and North Wales",
    "E": "West Midlands", "F": "North East England", "G": "North West England", "H": "Southern England",
    "J": "South East England", "K": "South Wales", "L": "South West England", "M": "Yorkshire",
    "N": "South Scotland", "P": "North Scotland",
}
POSTCODE = re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2}$")


def normalise_postcode(postcode: str) -> str:
    pc = re.sub(r"\s+", "", postcode or "").upper()
    if not POSTCODE.match(pc):
        raise ValueError("Enter a full UK postcode, for example SW1A 1AA.")
    return pc


class PriceService:
    def __init__(self, get, clock, ttl_seconds: int = 1800):
        self.get, self.clock, self.ttl = get, clock, ttl_seconds
        self._product: tuple[float, str] | None = None
        self._cache: dict[str, tuple[float, pd.Series]] = {}

    def region_for_postcode(self, postcode: str) -> str:
        pc = normalise_postcode(postcode)
        data = self.get(f"{OCTOPUS_API}/industry/grid-supply-points/", {"postcode": pc})
        results = data.get("results", [])
        if not results:
            raise ValueError("That postcode wasn't recognised. Choose your region from the list instead.")
        region = results[0]["group_id"].lstrip("_")
        if region not in REGIONS:
            raise ValueError("That postcode maps to an unknown region. Choose your region from the list instead.")
        return region

    def _product_code(self) -> str:
        if self._product is None or time.time() - self._product[0] > 6 * 3600:
            self._product = (time.time(), find_agile_product(self.get))
        return self._product[1]

    def upcoming(self, region: str) -> pd.Series:
        if region not in REGIONS:
            raise ValueError(f"Unknown region {region!r}.")
        hit = self._cache.get(region)
        if hit and time.time() - hit[0] < self.ttl:
            return hit[1]
        product = self._product_code()
        tariff = tariff_code_for(product, region, self.get)
        start = self.clock().floor("30min")
        series = fetch_agile_prices(product, tariff, start, start + pd.Timedelta(hours=48), self.get)["price_p_kwh"]
        self._cache[region] = (time.time(), series)
        return series

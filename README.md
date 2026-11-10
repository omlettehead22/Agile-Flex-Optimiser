# Agile flex: scheduling an EV and a home battery on Octopus Agile prices

An optimiser that plans a London household's EV charging and home battery half-hour by half-hour against **12 months of real Octopus Agile prices** (Nov 2025 to Oct 2026, region C), then measures cost, carbon and peak load against two simpler strategies.

![A typical winter day: Agile price, EV charging and battery state of charge](outputs/headline_winter_day.png)

| | Baseline<br><sub>charge on arrival, no battery</sub> | Optimised<br><sub>daily MILP, EV + 10 kWh battery</sub> |
|---|---:|---:|
| **Annual electricity bill** | £3,592 | **£2,141 (−40%, £1,451 saved)** |
| **Imported in the 16:00–19:00 peak** | 3,060 kWh | **4 kWh (3,055 kWh shifted)** |
| **Emissions** | 2,234 kgCO2 | **1,744 kgCO2 (−22%, 490 kg saved)** |

Even when it has to plan on a **price forecast** instead of the real prices, it keeps **92%** of that saving.

---

## Results, all strategies (1 Nov 2025 – 31 Oct 2026, 364 daily plans)

| | Baseline | Rule-based | **Optimised** | Optimised, EV only | Optimised + carbon |
|:--|--:|--:|--:|--:|--:|
| Annual cost (£) | 3,592 | 2,293 | **2,141** | 2,407 | 2,144 |
| Saving vs baseline | – | 36.2% | **40.4%** | 33.0% | 40.3% |
| Cost per kWh used (p) | 26.4 | 16.8 | **15.7** | 17.7 | 15.8 |
| Emissions (kgCO2) | 2,234 | 1,826 | **1,744** | 1,850 | 1,699 |
| kWh moved out of 16:00–19:00 | – | 3,060 | **3,055** | 2,548 | 3,055 |
| Negative-price half-hours used (of 499) | 1 | 106 | **272** | 75 | 270 |
| kWh bought at negative prices | 4 | 304 | **661** | 252 | 656 |
| Battery throughput (kWh) | – | 512 | **2,840** | – | 2,844 |

Full table: [`outputs/metrics.csv`](outputs/metrics.csv). Monthly view: [`outputs/monthly_cost.png`](outputs/monthly_cost.png).

**What the numbers say**

- **Most of the value is not charging the car at 18:00.** Smart EV charging alone saves £1,185 a year (33%). A one-line "cheapest slots" rule gets 90% of what the optimiser finds.
- **The optimiser earns its extra £152 a year over the rule by using the battery all day**, not just at the peak: ~280 cycles a year against intraday spreads, and 2.5× as many negative-price half-hours used. In total the battery adds £266 a year on top of smart EV charging.
- **Cheap and clean mostly coincide.** Cost-only optimisation already cuts emissions 22%. A carbon weight of 15p/kgCO2 saves a further 44 kg for £2.81 a year (about £64/tonne).
- **Forecasting is worth real money, and a simple model goes far.** Committing each plan before tomorrow's prices are published, on a ridge-regression forecast (MAE 4.1p/kWh), loses £100 over 307 days vs perfect foresight and keeps 92% of the saving. Naively repeating yesterday's prices keeps 87.5%. The forecast-driven optimiser still beats the rule-based strategy running on *real* prices (£1,970 vs £2,016 on the same days).
- Prices moved a lot during the year: Agile averaged ~18p/kWh last winter and 25–27p/kWh in Aug–Oct 2026, which is why autumn bills are the highest in the monthly chart.

## The three strategies

1. **Baseline.** The EV charges at 7 kW from 18:00 until it has 30 kWh. No battery.
2. **Rule-based.** The EV charges in the cheapest half-hours between 18:00 and 07:30. The battery discharges into the home during 16:00–19:00, then refills in the cheapest remaining half-hours.
3. **Optimised.** A mixed-integer linear program per day ([`optimise.py`](optimise.py)), minimising cost plus an optional carbon weight:

```
minimise   Σ_t (price_t + w · carbon_t) · grid_t
subject to grid_t = house_t + ev_t + charge_t − discharge_t ≥ 0      (no export)
           0 ≤ ev_t ≤ 3.5 kWh while plugged in, else 0               (7 kW charger, 18:00–07:30)
           Σ_t ev_t = 30 kWh                                         (car ready by 07:30)
           0 ≤ charge_t ≤ 2.5·m_t,  0 ≤ discharge_t ≤ 2.5·(1−m_t)    (5 kW, never both, m_t binary)
           soc_t = soc_{t−1} + η·charge_t − discharge_t / η          (η = √0.9, 90% round trip)
           0 ≤ soc_t ≤ 10 kWh,  soc_end ≥ soc_start                  (no free energy)
```

Each plan runs **16:00 to 16:00**: Octopus publishes the next day's Agile prices around 16:00, so this is the earliest moment a real controller knows every price in the window. Each day solves in a few hundredths of a second with CBC (bundled with PuLP).

## Assumptions

All live in [`config.yaml`](config.yaml); change a number and re-run.

| | Value |
|---|---|
| EV | 60 kWh battery, 7 kW charger, needs 30 kWh/day, plugged in 18:00–07:30 every day |
| Home battery | 10 kWh, 5 kW charge/discharge, 90% round-trip efficiency, full at 16:00 and restored by 16:00 next day |
| Household load (excl. EV) | 2,700 kWh/yr (Ofgem typical), synthetic half-hourly shape with evening peak, ±20% seasonal swing. Swap in a real profile with `house.profile_csv` |
| Tariff | Agile Octopus `AGILE-24-10-01`, region C (London), unit rates incl. VAT. Standing charge excluded (identical across strategies) |
| Export | None. The battery can only supply the home and the car |
| Carbon | National Grid ESO Carbon Intensity API, London region (id 13), half-hourly forecast intensity |
| Not modelled | Battery degradation (so battery value is an upper bound), EV charging losses, days the car is away |

## Run it

```bash
git clone https://github.com/omlettehead22/Agile-Flex-Optimiser && cd Agile-Flex-Optimiser
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python report.py        # ~1 min: all strategies, metrics, charts, forecast study -> outputs/
pytest                  # ~35 s: 27 constraint and parsing tests
```

The data is cached in `data/`, so no network is needed. To re-download or change region/period, edit `config.yaml` and run `python fetch.py --refresh` (no API key required).

## Project layout

| File | Purpose |
|---|---|
| `fetch.py` | Octopus and Carbon Intensity APIs → cached CSVs. Finds the current Agile product automatically, since product codes change |
| `common.py` | Config, data loading, household profile, daily 16:00→16:00 windows (DST-safe) |
| `strategies.py` | Baseline and rule-based strategies, plus a runner |
| `optimise.py` | The daily MILP |
| `forecast.py` | Walk-forward ridge price forecaster for the stretch study |
| `report.py` | Metrics, charts, `outputs/results.json` |
| `tests/` | Every strategy, on real days, DST days and adversarial prices (all-negative, flat, spikes): EV always reaches its target only while plugged in; battery stays within power and SOC limits, never charges and discharges at once, follows the loss model and ends the day where it started; no export; optimiser is never beaten by the heuristics; a carbon weight never increases emissions |
| `notebooks/walkthrough.ipynb` | The interview walkthrough |

## Data notes

- Agile: 17,520 half-hours, no gaps. 499 negative-price half-hours, minimum −11.3p, maximum 86.7p.
- Carbon intensity: the API has no London data for 1–2 Jan 2026 (95 half-hours). These are filled with the average for the same half-hour over the surrounding ±9 days and flagged in the `interpolated` column.
- The tests caught a real bug during development: adding 24 hours to a timezone-aware timestamp drifts by an hour after the clocks change, which silently moved every summer plan to 17:00. Windows are now built from local calendar dates.

## Data sources

- Octopus Energy public API: `https://api.octopus.energy/v1/products/` → `…/electricity-tariffs/E-1R-AGILE-24-10-01-C/standard-unit-rates/`
- Carbon Intensity API (National Grid ESO): `https://api.carbonintensity.org.uk/regional/intensity/{from}/{to}/regionid/13`

Not affiliated with Octopus Energy. MIT licence.

I built an optimiser on Octopus's own Agile prices that cuts a household's electricity bill by 40% and shifts 3,055 kWh out of the 4–7pm peak.

The setup: a London home with an EV (7 kW charger, 30 kWh a day, plugged in 18:00–07:30) and a 10 kWh home battery, run against every half-hourly Agile price from November 2025 to October 2026 (17,520 of them), with National Grid ESO carbon intensity for the same half-hours.

I compared three strategies on the same year:
→ Plug in and charge at 18:00, no battery: £3,591
→ Simple rule (cheapest slots for the car, battery covers the peak): £2,295
→ A daily mixed-integer program over charger, battery power, state of charge and losses: £2,142

What I learned:

1️⃣ Most of the value is simply not charging at 6pm. A one-line rule gets 90% of the optimiser's saving.

2️⃣ The optimiser's edge comes from using the battery all day, not just at the peak: about 280 cycles a year against intraday spreads, and 2.5× as many negative-price half-hours used (269 of the year's 499).

3️⃣ Cheap and clean mostly line up. Cost-only optimisation already cut emissions 22% (489 kgCO2). Adding a carbon price saved a further 44 kg for under £3 a year.

4️⃣ Forecasts matter, and simple ones go a long way. When the plan had to be made before tomorrow's prices were published, a ridge-regression forecast kept 92% of the saving, and still beat the simple rule running on the real prices.

Every constraint is tested (the car always reaches its target, the battery never leaves its limits), and the tests caught a daylight-saving bug that had quietly moved every summer plan by an hour.

The same optimiser now runs a daily charging plan on GitHub Actions and powers Off-peak, a small web app that tells anyone on Agile when to use electricity.

Code, data, notebook and assumptions are all open: https://github.com/omlettehead22/Agile-Flex-Optimiser

#EnergyFlexibility #OctopusEnergy #Python #Optimisation #NetZero

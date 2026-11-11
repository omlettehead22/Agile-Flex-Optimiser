"""Energy tips shown once a day (one per day, rotating), plus situation-specific tips."""

DAILY = [
    "Most dishwashers and washing machines have a delay timer. Set it tonight to start in the cheapest slot.",
    "Washing clothes at 30°C instead of 40°C uses about a third less electricity for most loads.",
    "A full dishwasher uses less energy than washing the same items by hand under a running hot tap.",
    "Tumble dryers are among the most expensive appliances to run. Dry on a line when you can, or run the dryer in a cheap slot.",
    "Boil only the water you need. Overfilling the kettle is one of the easiest wastes to cut.",
    "Turn appliances off at the wall rather than leaving them on standby. TVs, consoles and chargers add up.",
    "An immersion heater on a timer can heat your water in the cheapest overnight hours instead of the evening.",
    "Batch cooking in the oven once uses less energy than heating it on three separate evenings.",
    "Air fryers and microwaves use far less energy than a full-size oven for small portions.",
    "Defrost your freezer when ice builds up; a thick layer makes it work harder.",
    "Keep the fridge at 3–5°C and the freezer at −18°C. Colder than that costs more without keeping food fresher.",
    "LED bulbs use around 80% less electricity than old halogen bulbs.",
    "Charging laptops, e-bikes and power banks in a cheap slot moves load out of the evening peak.",
    "Draught-proofing doors and windows is one of the cheapest ways to cut heating bills.",
    "Bleeding radiators so they heat fully can make the room warm faster.",
    "Turning your thermostat down by 1°C can cut heating bills noticeably, often by around 10%.",
    "Close curtains at dusk to keep heat in on cold evenings.",
    "If you have an EV, set the car or charger schedule once; you only need to change it when prices look unusual.",
    "Heat pumps run most efficiently at lower flow temperatures. Ask your installer whether yours can run cooler.",
    "Running the dishwasher on eco mode takes longer but uses noticeably less energy.",
    "A smart plug with a schedule can move small loads, like a dehumidifier, into cheap hours automatically.",
    "Cooking with lids on pans boils food faster and uses less energy.",
    "Fill gaps in the freezer with bottles of water; a full freezer holds its temperature better.",
    "Shorter showers save both water and the energy that heats it.",
    "Check your smart meter's half-hourly data once a month to spot appliances using power overnight.",
    "If prices go negative, that's the moment to run the dryer, charge everything and heat water.",
    "Insulating your hot water cylinder keeps water hot for longer with less reheating.",
    "Avoid running several big appliances in the 16:00–19:00 peak; even shifting one helps.",
    "Pre-heat or pre-cool your home slightly before the peak, then let it coast through the expensive hours.",
    "Unplug the second fridge or freezer if it's mostly empty; it can cost more than you'd expect.",
]

NEGATIVE = "Prices go negative soon: you're paid to use electricity. Run the washing machine, dryer and dishwasher, and charge everything."
BIG_SPREAD = "Prices vary a lot today, so timing matters more than usual. A delay timer on one appliance makes a real difference."


def tip_for(day_ordinal: int, has_negative: bool, spread_p: float) -> str:
    if has_negative:
        return NEGATIVE
    if spread_p >= 25:
        return BIG_SPREAD
    return DAILY[day_ordinal % len(DAILY)]

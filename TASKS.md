# Tasks for Karthik

The core is built and tested: the daily planner, the off-peak web app with browser notifications, and the solar calculator. These tasks are deliberately left for you. Each one makes the product more useful or more yours. Do them roughly in this order.

## 1. Deploy it (start here)
Get the web app live so real people can use it.
- Create a free Postgres database (Neon or Supabase) and copy its connection URL.
- Run `python scripts/generate_vapid_keys.py` once. Keep the output private.
- On Render: New → Blueprint → this repo. Fill in `DATABASE_URL`, the three `VAPID_*` values and a long random `CRON_TOKEN`.
- In GitHub: Settings → Secrets and variables → Actions → add `APP_URL` (your Render URL) and the same `CRON_TOKEN`.
- **Done when:** you turn on notifications on your phone, press "Send a test notification", and it arrives; the next day it arrives at your chosen time.

## 2. Postcode-level solar output (PVGIS)
`webapp/solar.py` uses one approximate yield per region. The EU's free PVGIS service gives yield for an exact location, roof angle and direction.
- Add server-side calls to PVGIS (it can't be called from the browser), using postcode → latitude/longitude (postcodes.io is free).
- Add roof direction and pitch inputs.
- **Done when:** a north-facing roof in Glasgow gives clearly less output than a south-facing roof in Cornwall, and the tests cover it.

## 3. Make the solar calculator Agile-aware (your differentiator)
Today the calculator assumes a flat import price and the battery only stores solar. On Agile, a battery can also charge from the grid overnight when it's cheap.
- Use real Agile prices for a past year and the LP optimiser in `agileflex/optimise.py` to schedule the battery with solar.
- Add Agile Outgoing (export) prices.
- **Done when:** the calculator shows "battery on Agile" vs "battery on a flat tariff", and you can explain the difference in an interview.

## 4. Smarter notifications
- "Tell me when the price drops below X p/kWh" alerts.
- Skip a message when nothing is interesting (prices flat all day), with a setting to always send.
- **Done when:** users can choose, and tests cover both.

## 5. Use people's real consumption
Octopus customers can create an API key and get their own half-hourly usage. Let users paste it (store it encrypted, or don't store it at all) to personalise savings and the solar calculator.

## 6. Installer leads, done properly (business)
Before you share anyone's details with an installer:
- Add a clear consent checkbox and a privacy policy page (UK GDPR). Never pre-tick consent.
- Store consent with a timestamp; make it easy to withdraw.
- Agree referral terms in writing with each installer.

## 7. Protect the public endpoints
Add rate limiting (for example per IP) to `/api/test-push`, `/api/subscribe` and `/api/region`, so nobody can spam them.

## 8. Know your numbers
A simple admin page (password protected) showing subscribers, notifications sent per day, and unsubscribes. You'll need these for installers, investors or a visa endorsement.

## 9. Grow the guides
Six guides are in `webapp/articles/`. Good next topics: reading your smart meter data, EV charging on Agile, Agile vs fixed tariffs, Intelligent Octopus Go, insulation before heat pumps.
- Write in your own words and check facts against gov.uk, Ofgem or the Energy Saving Trust; set `updated:` to the date you checked.
- Re-check grant and VAT figures every few months. **The 0% VAT on solar and batteries is due to end on 31 March 2027**; update `is-solar-worth-it.md` then.
- If you add an energy news section, show headlines with links and your own one-line summary. Don't copy articles.
- **Done when:** each new guide passes the tests (header fields, working links) and you'd be happy for an installer to read it.

## 10. Name, domain and polish
Pick a product name, buy a domain, point it at Render, and test the "Add to Home Screen" flow on a real iPhone and Android phone.

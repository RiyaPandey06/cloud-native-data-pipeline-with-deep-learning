# Data Source Terms (Open-Meteo)

Facts below were taken from Open-Meteo's terms and Air Quality API docs as summarised in the PRD (verified 29 Oct 2026); re-check before deploying, since provider terms change.

- No API key for non-commercial use. Limits: under 10,000 calls/day, 5,000/hour, 600/minute, 300,000/month. This pipeline makes 2 calls per run: about 48/day hourly.
- Data is CC BY 4.0. Attribution to the CAMS ENSEMBLE data provider and Open-Meteo is mandatory (dashboard footer and README).
- Rate limits are applied per IP; shared cloud hosts may be throttled earlier. The client retries with backoff and honours `Retry-After`; incremental fetches and stored raw payloads keep call volume low.
- The target is CAMS model output, not sensor data. Native resolution depends on location (0.1° hourly in Europe, 0.4° 3-hourly globally); confirm it for Bengaluru before quoting model results.

## Open decision: is a job-search portfolio "non-commercial"?

Open-Meteo lists private or non-profit sites without subscriptions or advertising as non-commercial and commercial products or promotional activities as commercial. A portfolio sits in a grey area. Options: ask the provider, self-host their open-source server, or switch source. Suggested message:

> Hello, I run a free, ad-free, open-source portfolio project that forecasts PM2.5 for one city using the Open-Meteo Air Quality and Weather APIs (about 48 requests/day, attribution shown). It is used to demonstrate data-engineering skills to prospective employers and earns no revenue. Does this count as non-commercial use under your terms? Thank you.

Send to info@open-meteo.com and record the reply here: _(pending)_.

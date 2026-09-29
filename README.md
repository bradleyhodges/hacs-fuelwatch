# FuelWatch WA for Home Assistant

Western Australian fuel prices, nearby station searches, vehicle costs and comparisons, using the [FuelWatch JSON:API service](https://fuelwatch.oss.bhodges.me/v1). The original price publisher is [FuelWatch](https://fuelwatch.wa.gov.au). Home Assistant installations require no FuelWatch or Google API key.

## Installation

Requires Home Assistant **2026.9.4 or newer**. Copy `custom_components/fuelwatch_wa` into your Home Assistant `custom_components` directory, restart Home Assistant, then add **FuelWatch WA** under **Settings → Devices & services**. This repository also contains HACS integration metadata.

Configure searches, tracked stations, vehicles, comparisons and discounts through the integration's subentries. Select the fuel products needed by each search or station. Existing saved station selections continue using their original identities; this update does not require recreating entries.

The integration registers its sidebar dashboard and bundles a `custom:fuelwatch-wa-card` dashboard card. To use the card in another dashboard, add `/fuelwatch_wa_static/fuelwatch-wa-card.js` as a JavaScript module resource. Station rows expose postcode, phone, facilities, restrictions and opening hours when available. Google-enriched fields retain their attribution links; FuelWatch-owned values take precedence. All price periods and displayed opening times use **AWST (+08:00)**.

## Data flow and reliability

The shared coordinator polls `https://fuelwatch.oss.bhodges.me/v1` hourly for the required products and current/next published periods. Explicit product and absolute-date filters prevent an API default change or a Perth midnight rollover from selecting the wrong fuel or day. Prices remain exact decimals in cents per litre until presentation.

The API returns one `serviceStation` resource per station with `price.products`, for example `{"1":195.1,"2":200.2}`. A missing product means that station has no published quote for that fuel. **Update the Home Assistant integration before deploying the new Worker schema.** The updated adapter also accepts the previously deployed `fuelPrices` representation, allowing it to run against the old Worker during rollout. Older integration versions cannot read the grouped schema. Both formats require JSON:API 1.1, source metadata and matching 06:00–06:00 Perth price periods.

Requests use Home Assistant's shared HTTP session, bounded concurrency, timeouts and bounded retries for temporary failures. Identical in-flight requests share work. ETags allow conditional requests without replacing the original fetch timestamp on a 304. Invalid responses and unexpectedly empty replacements preserve the last good snapshot. Saved quotes, station details and source metadata survive restarts; old saved records without these new details remain readable.

The **Data status** diagnostic sensor exposes source, API URL, per-product snapshot provenance and errors. Price entity attributes and dashboard rows expose station details as snake_case fields (`site_features`, `open_hours`, `is_24_hours`, `source_notes`, `enrichment`) alongside the existing price, validity and age fields. Enrichment affects descriptive fields, never station identity or price calculations.

The Worker maintains complete per-product/date extracts in D1, refreshes them hourly and retains published snapshots for up to six hours. Brand and exact-suburb selections reuse these extracts; region and surrounding-suburb searches use FuelWatch's source filtering and their own shared cache. See [the Worker documentation](api-worker/README.md) for its complete schema, cache behavior, migrations, scheduled refreshes, logs and deployment commands. A local source change does not update the deployed Worker: run its migration/deploy commands to activate the new schema and hourly cron.

## Development

The Python integration requires Python 3.14.2 or newer. Run the full Home Assistant suite on Linux (including WSL); Home Assistant imports platform-specific modules unavailable on native Windows.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
ruff check custom_components tests tools
```

For focused adapter validation on Windows:

```sh
python tools/test_api_adapter.py
```

For the dashboard and bundled assets:

```sh
cd dashboard
npm ci
npm test
npm run build
```

Commit rebuilt assets from `custom_components/fuelwatch_wa/www` with dashboard source changes. The Worker fixture `api-worker/tests/fixtures/fuelwatch-v1.json` is checked against the actual serializer and consumed by Python adapter tests, keeping both sides of the API contract aligned. Tests use deterministic fixtures; live API checks should be small and deliberate.

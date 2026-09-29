# FuelWatch API worker

Validates the WA FuelWatch RSS feed and serves JSON for the Home Assistant integration at `https://fuelwatch.oss.bhodges.me/v1`. The domain is declared in `wrangler.jsonc`; it must be deployed before the integration can fetch prices.

## Develop and verify

Use Node.js 24 or newer and pnpm 11.6.0. Run these commands from `api-worker`:

```sh
pnpm install --frozen-lockfile
pnpm run db:migrate:local
pnpm run dev
```

```sh
pnpm run lint
pnpm run type-check
pnpm test
pnpm run build
```

`type-check` regenerates Cloudflare binding/runtime types before checking the source. `test` bundles the actual worker and runs it in Miniflare, alongside deterministic unit tests for dates, deadlines, validation and cache failures. `build` performs a Wrangler deployment dry run without publishing anything. `pnpm run format` applies Biome formatting and safe lint fixes. CI runs all checks with the committed lockfile.

The test fixture in `tests/fixtures/fuelwatch-v1.json` is checked against worker output and consumed by the Python adapter tests in the repository's `tests/test_api.py`.

From the repository root, `python tools/test_api_adapter.py` runs the real Python adapter tests without importing Home Assistant's platform bootstrap (useful on Windows). Install the repository's development requirements first. This focused check supplements the full `python -m pytest -q` integration suite, which runs on Linux in CI.

## HTTP contract

- `GET /v1`: versioned JSON described below; used by Home Assistant.
- `GET /`: legacy `{ "feed": ... }` representation retaining RSS descriptions and parser fields.
- `HEAD`: the same status and headers as GET, without a response body; shares its cache entry.
- `OPTIONS`: public CORS preflight. GET, HEAD and OPTIONS are the only allowed methods.

Both endpoints accept the same case-sensitive query names:

| Parameter | Accepted values | Default |
| --- | --- | --- |
| `Product` | `1`, `2`, `4`, `5`, `6`, `10`, `11` | `1` |
| `Day` | `yesterday`, `today`, `tomorrow`, or `DD/MM/YYYY` within those three Perth calendar dates | `today` |
| `Suburb` | Nonempty text up to 100 characters, without control characters | All suburbs |
| `Region` | Region codes exported in `src/fuelwatch.ts` | All regions |
| `Brand` | Brand codes exported in `src/fuelwatch.ts` | All brands |
| `Surrounding` | `yes` or `no` | Origin default |

Unknown or repeated parameters, invalid codes and unsupported dates return HTTP 400 before origin access. Suburb case/whitespace, query ordering and date aliases are normalized into cache keys. Absolute dates are translated to upstream relative days.

```sh
curl 'https://fuelwatch.oss.bhodges.me/v1?Product=1&Day=today'
```

The envelope contains `schemaVersion: 1`, numeric `product`, `sourceDate` (`YYYY-MM-DD`), UTC timestamps `fetchedAt`, `validFrom`, `validUntil`, `publicationStatus`, and `feed.items`.

`publicationStatus` is `available` for a nonempty snapshot, `not_yet_published` for an empty tomorrow snapshot before 14:30 Perth, and `empty` for other valid empty results. Price periods run from 06:00 Perth on the source date to 06:00 the next day. Before 06:00, request yesterday for the currently effective price period. An explicit request for an expired period may return validated historical prices with `no-store`; its metadata retains the actual validity dates.

`/v1` returns normalized station objects in **`feed.items`**, retaining the envelope so Home Assistant can verify product, source date and freshness. It omits redundant descriptions, `content`, `contentSnippet` and parser-generated `isoDate`. `/` retains the original flat RSS representation with E.164 phone numbers. The integration derives station IDs from FuelWatch address, suburb and numeric coordinates because distinct neighbouring sites can share an address. Brand or price changes keep identity; coordinate/address corrections change it. Existing address-only IDs are retained for the coordinates in the saved snapshots/catalogue, preserving configured selections. A saved station whose coordinates are unavailable or subsequently corrected may need reselection.

Example station (illustrative values):

```json
{
  "name": "Example Station",
  "trading-name": "Example Station",
  "brand": "Independent",
  "price": { "perLitre": 185.9, "asAt": "2026-09-29T00:00:00.000+08:00" },
  "address": { "street": "1 Test Road", "suburb": "PERTH", "state": "WA", "postcode": null },
  "is24Hours": false,
  "phone": "+61899811151",
  "latitude": -31.95,
  "longitude": 115.86,
  "site-features": ["ATM", "EFTPOS"],
  "open-hours": { "Monday": "06:00-20:30", "Sunday": "Closed" },
  "restrictions": null
}
```

`price.perLitre` is a JSON **number in Australian cents per litre** (185.9 means AUD 1.859/L). `price.asAt` is the source date at Perth midnight, not the price period's 06:00 start and not a retrieval timestamp. Consumers needing decimal arithmetic should parse JSON numbers as decimals, as the Python adapter does. Coordinates are numbers; postcodes stay strings. Google never changes FuelWatch prices, coordinates, names, brands, streets or suburbs. Missing postcodes are `null` until a confident place match supplies one; example data is never used as a lookup database.

Phone parsing uses `libphonenumber-js` with the Australian default region, strict whole-value parsing and validity checks. Invalid/ambiguous numbers produce `phone: null` and retain the original text in `source-notes.phone`; an explicitly supplied invalid number blocks Google replacement. Empty fields and FuelWatch's `--EMPTY--` marker permit a fallback. Extensions and lists of numbers are not silently discarded to invent a canonical number.

Opening hours use local Perth wall times: `HH:mm-HH:mm`, comma-separated split shifts, or `Closed`. Closing at `24:00` is allowed; a closing time before the opening time denotes the following day. FuelWatch weekday ranges expand into named days. Google's overnight periods split at midnight. Unknown days are omitted, not inferred closed from a partial FuelWatch schedule. `is24Hours` is `true` for confirmed continuous opening, `false` for a known limited schedule, and `null` when unknown. Confirmed 24-hour stations omit `open-hours`. Malformed source schedules remain in `source-notes.open-hours` and block Google replacement.

`site-features` and `restrictions` use the exact labels exported by `FEATURES` and `RESTRICTIONS` in `src/station.ts`. Order is deterministic and duplicates are removed. Features include Fuel Cards, ATM, Toilets, Bottled Gas, Trailer Hire, EFTPOS, Restaurant, Carwash, Workshop, Air, Water, Ice, Discount, Voucher, Bottled AdBlue, Pumped AdBlue, Truck Friendly, Convenience Store, Credit Cards, Debit Cards and Open 24 hours. Restrictions are Unmanned site (credit card charges may apply), Entry Permit Required, Membership Required and Low Aromatic Fuel. Unknown text is preserved separately in `source-notes.features` or `source-notes.restrictions`; it never silently enters the controlled vocabulary. Empty features are `[]`; no known restrictions is `null`.

Google can add explicitly reported facilities and supply missing phone/postcode/hours. Existing FuelWatch hours win per weekday, including explicit closed days. A station with FuelWatch's `Open 24 hours` cannot acquire a narrower Google schedule. Added fields carry `enrichment` metadata: provider, place ID, fetched timestamp, stale flag, affected field names, Google Maps URL and third-party attributions. Consumers displaying these fields should retain the accompanying attribution. The Home Assistant price adapter currently consumes the FuelWatch-owned price and identity fields only.

Whole snapshots are rejected for invalid prices, coordinates, dates, missing station identity fields, conflicting records at the same address/coordinates, unsafe or malformed XML, duplicate scalar XML fields, over 5,000 stations or decompressed input over 4 MiB. Identical duplicate records are collapsed.

Errors use `{ "error": { "code": "...", "message": "..." } }` and `Cache-Control: no-store`. Clients should keep their last good snapshot on failure, as the integration coordinator does.

| HTTP | Meaning |
| --- | --- |
| 400 / 404 / 405 | Invalid query / unknown endpoint / unsupported method |
| 502 | Origin denied or redirected the request, or returned an invalid/oversized snapshot |
| 503 | Origin unavailable or rate limited; `Retry-After` is preserved when supplied |
| 504 | The eight-second upstream deadline expired |
| 500 | Unexpected worker/configuration failure |

## Freshness and failure handling

The Cache API stores successful nonempty results for at most 300 seconds and empty results for at most 30 seconds. Lifetimes stop at the next Perth midnight, 06:00, 14:30 or price expiry, whichever is sooner. Browser responses require revalidation (`max-age=0`); shared cache freshness uses the remaining lifetime. ETags support conditional requests against the same cached representation.

Cache reads and writes can fail without losing a valid origin response. Writes run under `waitUntil`. `X-FuelWatch-Cache`, `X-FuelWatch-Source-Date` and `X-FuelWatch-Fetched-At` expose provenance. Expired cache data is never used as an outage fallback. Home Assistant retains its own last good snapshots and displays their age/errors.

One eight-second deadline covers upstream headers, streaming body reads and retry delay. Network failures, 429 and 5xx responses allow at most one retry with jitter. A long `Retry-After` returns immediately rather than holding a worker open. Redirects and validation failures are not retried. Origin cookies, cache headers and XML ETags are not forwarded.

Caching is per Cloudflare data centre. Concurrent cold misses can still issue separate origin requests, and arbitrary suburb queries can create many cache entries. Monitor traffic before adding Cloudflare rate-limiting rules or coordinated refresh storage; there is no distributed single-flight mechanism here.

## Deploy and operate

Authenticate Wrangler to the Cloudflare account containing the `bhodges.me` zone. `wrangler.jsonc` binds the existing `fuelwatch` D1 database as `FUELWATCH_DB`. Apply the migration before deploying:

```sh
pnpm run db:migrate:remote
pnpm exec wrangler secret put GOOGLE_MAPS_API_KEY
pnpm run deploy
```

The key must belong to a billing-enabled Google project with **Places API (New)** enabled. Restrict it to that API and set Google Cloud quotas/budget alerts. The selected phone, hours and facility fields affect the billed SKU; the worker limits request counts, not currency spend. No separate Geocoding API call is made. Local development reads the ignored `.dev.vars` file; never commit it or print the key. Setting a local environment variable does not install the production Worker secret. For FuelWatch-only operation set `GOOGLE_DAILY_REQUEST_LIMIT` to `0`; public requests also work when enrichment is unavailable.

Wrangler's Custom Domain configuration provisions the hostname; `workers.dev` is disabled. `FUELWATCH_URL` must remain an HTTPS origin URL without credentials. Clients of the public read-only endpoint need no API key.

After deployment, verify a real `/v1?Product=1&Day=today` response, a subsequent cache hit, a HEAD request and a rejected invalid query. Deploy the worker before distributing the updated Home Assistant integration. A deployment dry run does not verify DNS, account permissions or live Cloudflare behaviour.

Structured Workers logs include `feed_fetched` (product, date, station count, elapsed time and TTL), `upstream_http_error` (HTTP status and attempt), `request_failed`, `cache_read_failed` and `cache_write_failed`. Observability currently samples every invocation; review retention and sampling as traffic grows. Alert on sustained 502/503/504 rates, repeated cache failures and unexpectedly empty current-period feeds. `pnpm exec wrangler tail` streams logs.

XML parsing is CPU work on cold requests. Measure production CPU usage and select a Workers plan/CPU limit that accommodates whole-state feeds; local tests do not establish production capacity.

## D1 enrichment lifecycle and cost controls

Public requests **never contact Google**. On an edge-cache miss, `/v1` reads the requested stations from D1 in indexed batches of 80 keys with a total 1.5-second read budget. Missing tables, corrupt records and D1 outages degrade to FuelWatch-only output. New enrichment becomes visible when the normal response cache expires (up to five minutes). Fuel prices retain their separate short-lived Cache API policy.

The scheduled handler runs every 15 minutes. It rotates through all seven fuel products, discovers identities from validated current FuelWatch RSS, and refreshes due stations. Names/brands are part of the match fingerprint, while the storage key excludes product, day and price; one lookup serves multiple fuel types and dates. Identity changes invalidate the provider association. Repeated discovery updates `last_seen_at` at most once daily to reduce D1 writes. A 120-second database lease prevents overlapping refreshes, conditional writes fence stale invocations, and the worker stops starting work after 60 seconds. A small batch may therefore finish with fewer stations than its configured maximum.

| Setting | Checked-in value | Accepted range / effect |
| --- | --- | --- |
| `GOOGLE_DAILY_REQUEST_LIMIT` | `1000` | `0–10000`; `0` disables refresh. Atomic D1 reservations count failures/timeouts too. UTC day resets at 08:00 Perth. |
| `ENRICHMENT_BATCH_SIZE` | `100` | `1–500`; maximum stations per cron invocation, also subject to time and daily request limits. Each station can use two requests. |
| `ENRICHMENT_REFRESH_DAYS` | `30` | `1–30`; successful records become due after this interval. |

If omitted, the code uses conservative fallbacks of 100 requests/day, 10 stations/batch and 7 days. Invalid settings fail the scheduled invocation rather than removing limits. Allow enough capacity for initial discovery, refreshes and negative matches; approximate ongoing successful refresh demand is station count divided by refresh days. The cap is shared only by invocations using this D1 database; other projects using the key need their own controls. Never delete the daily budget table to force a refresh.

Initial discovery starts with a name/address Text Search. If there is no accepted match, one additional address search looks for petrol stations without the FuelWatch trading name in the query. Stored place IDs use one Place Details request. Ambiguous results and provider errors do not trigger a fallback; every request, including the address fallback, reserves its own unit of the daily budget. A station lookup makes at most two requests, so a batch can use more requests than stations. The fallback is another search strategy, not a retry of a failed HTTP request.

Matching requires a unique nearby operational WA petrol station and rejects conflicting known brands. Exact distinctive names within 50 metres tolerate alternative street addresses: rural lot notation, highway aliases and different roads at a corner otherwise reject correct businesses. For example, Google lists Billabong Roadhouse on Tourist Drive 354 while FuelWatch lists North West Coastal Highway. Generic brand/locality names still require compatible addresses. Other name matches require compatible addresses within 250 metres; locality alone never proves identity. Address-only matching requires an exact normalized street number and road within 50 metres. Unknown street numbers, different roads, nearby competitors and multiple accepted candidates cannot qualify by address alone. Google addresses are matching evidence and a postcode source; FuelWatch's public street/suburb remain unchanged.

Unmatched results wait one day before another attempt. Network, malformed-response and provider failures keep previous data and back off from one hour up to one day. HTTP 400/401/403 pause all refreshes for a day; 429 pauses for an hour. No automatic HTTP retries occur. Each Google request has a five-second deadline and a 256 KiB response limit; a two-search station lookup can take up to ten seconds of HTTP time. Credentials travel only in request headers.

Cached provider data can be served after its refresh deadline with `enrichment.stale: true`, but never once it reaches **30 days old**, regardless of the configured refresh interval. With the checked-in 30-day interval there is no stale grace period: fields may temporarily disappear until cron refreshes them. Stations absent from discovery for 90 days and request counters older than 35 days are pruned. Retained-but-expired records may still hold the place ID for the next refresh. Persistent provider caching is an explicit deployment choice; the [Places policies](https://developers.google.com/maps/documentation/places/web-service/policies) describe Google's storage and attribution requirements.

For local scheduled testing, run `pnpm exec wrangler dev --test-scheduled` after applying local migrations, then visit `http://localhost:8787/__scheduled`. This **can make paid Google calls** when your local key is configured; the local D1 budget is separate from production. Automated tests use mock Google responses and real local D1 migrations instead.

Operational queries (run with `pnpm exec wrangler d1 execute FUELWATCH_DB --remote --command '<SQL>'`, or use the D1 console):

```sql
SELECT utc_day, requests FROM google_request_budget ORDER BY utc_day DESC LIMIT 7;
SELECT COUNT(*) AS discovered, COUNT(enrichment_json) AS cached FROM station_enrichment;
SELECT
  SUM(enrichment_json IS NULL AND next_attempt_at = 0) AS pending,
  SUM(enrichment_json IS NULL AND next_attempt_at > 0 AND failure_count = 0) AS unmatched,
  SUM(enrichment_json IS NULL AND failure_count > 0) AS failed
FROM station_enrichment;
SELECT owner, lease_until, blocked_until, next_product FROM enrichment_refresh;
SELECT station_key, next_attempt_at, failure_count FROM station_enrichment
ORDER BY next_attempt_at LIMIT 20;
```

`enrichment_refreshed` logs `discovered` (rows in this product's feed, not newly inserted stations), `matched` (enrichments actually saved), `unmatched`, `failed`, `writeSkipped`, `requestsReserved`, `stopReason` and duration. `cache` reports database-wide `stations`, `enriched` (stored profiles, including those due for refresh) and `pending` (no recorded result yet). These replace the ambiguous old `completed` counter, which included no-match results. A run can process more stations than its product feed contains because the due queue includes previously discovered products. A zero-row lease-fenced write is never counted as a saved match.

`enrichment_lookup_failed` includes a safe classification: configuration, rate_limited, upstream, invalid_response, timeout or budget_exhausted. `enrichment_cache_read_failed` means public output continued without some or all enrichment. Missing configuration logs `enrichment_not_configured`. D1 write failures fail the cron invocation so Workers observability records the failure. After correcting credentials, a deliberate `UPDATE enrichment_refresh SET blocked_until = 0 WHERE id = 1` clears the global cooldown; individual retry times still apply. Do not expose database maintenance through the public endpoint.

## Developer map

| Module | Responsibility |
| --- | --- |
| `src/index.ts` | HTTP/scheduled entrypoints, representation selection, ETags and cache publication. |
| `src/feed.ts`, `src/fuelwatch.ts` | XML validation, raw source types and price-period metadata. |
| `src/station.ts` | Public DTOs, controlled vocabularies, phone/hours parsing and source-priority merge. |
| `src/google.ts` | Bounded Places HTTP boundary, confidence matching, hours/facility conversion. |
| `src/enrichment.ts` | D1 trust boundary, read deadline, discovery, lease, budget and retry state. |
| `src/cache.ts`, `src/query.ts`, `src/upstream.ts` | Edge freshness, request validation and bounded origin transport. |
| `migrations/0001_station_enrichment.sql` | Station cache, scheduler lease and daily request-counter tables. |

Exported utilities and domain boundaries carry JSDoc; dependency injection of clocks/fetchers permits deterministic failure tests. Update normalization tests before extending the controlled vocabularies or source grammar. A DTO change must update `tests/fixtures/fuelwatch-v1.json`, the worker runtime assertions and the Python adapter contract tests together. Do not weaken source validation to accommodate missing enrichment.

Relevant documentation: [D1 migrations](https://developers.cloudflare.com/d1/reference/migrations/), [Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/), [Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/), [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/), [Workers logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/), [Places Text Search](https://developers.google.com/maps/documentation/places/web-service/text-search).

# FuelWatch API worker

Validates the WA FuelWatch RSS feed and serves JSON for the Home Assistant integration at `https://fuelwatch.oss.bhodges.me/v1`. The domain is declared in `wrangler.jsonc`; it must be deployed before the integration can fetch prices.

## Develop and verify

Use Node.js 24 or newer and pnpm 11.6.0. Run these commands from `api-worker`:

```sh
pnpm install --frozen-lockfile
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

`publicationStatus` is `available` for a nonempty snapshot, `not_yet_published` for an empty tomorrow snapshot before 14:30 Perth, and `empty` for other valid empty results. Prices remain decimal strings in **cents per litre**. Price periods run from 06:00 Perth on the source date to 06:00 the next day. Before 06:00, request yesterday for the currently effective price period. An explicit request for an expired period may return validated historical prices with `no-store`; its metadata retains the actual validity dates.

`/v1` omits redundant `content`, `contentSnippet` and parser-generated `isoDate`; station descriptions and FuelWatch fields remain available. It adds no persistent station ID: the source does not provide one. The integration derives IDs from address, suburb and numeric coordinates because distinct neighbouring sites can share an address. Brand or price changes keep identity; coordinate/address corrections change it. Existing address-only IDs are retained for the coordinates in the saved snapshots/catalogue, preserving configured selections. A saved station whose coordinates are unavailable or subsequently corrected may need reselection.

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

Authenticate Wrangler to the Cloudflare account containing the `bhodges.me` zone, review `wrangler.jsonc`, then run `pnpm run deploy` when ready. Wrangler's Custom Domain configuration provisions the hostname; `workers.dev` is disabled. `FUELWATCH_URL` must remain an HTTPS origin URL without credentials. No API key is required by this public read-only service.

After deployment, verify a real `/v1?Product=1&Day=today` response, a subsequent cache hit, a HEAD request and a rejected invalid query. Deploy the worker before distributing the updated Home Assistant integration. A deployment dry run does not verify DNS, account permissions or live Cloudflare behaviour.

Structured Workers logs include `feed_fetched` (product, date, station count, elapsed time and TTL), `upstream_http_error` (HTTP status and attempt), `request_failed`, `cache_read_failed` and `cache_write_failed`. Observability currently samples every invocation; review retention and sampling as traffic grows. Alert on sustained 502/503/504 rates, repeated cache failures and unexpectedly empty current-period feeds. `pnpm exec wrangler tail` streams logs.

XML parsing is CPU work on cold requests. Measure production CPU usage and select a Workers plan/CPU limit that accommodates whole-state feeds; local tests do not establish production capacity. A development sample of 939 RSS rows produced 938 unique stations and reduced JSON from 808,097 to 493,765 bytes with `/v1` (about 39%); this is a payload measurement, not a latency benchmark.

Relevant Cloudflare documentation: [Cache API](https://developers.cloudflare.com/workers/runtime-apis/cache/), [Custom Domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/), [Workers logs](https://developers.cloudflare.com/workers/observability/logs/workers-logs/).

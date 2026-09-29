# FuelWatch worker hardening implementation plan

> Execute inline on the existing main branch using Superpowers TDD, verification, and a fresh final code review. The user approved the review findings and implementation; no deployment or push is requested.

**Goal:** Provide validated, bounded, correctly cached FuelWatch data to Home Assistant through https://fuelwatch.oss.bhodges.me.

**Architecture:** Preserve the existing root JSON response, add a compact /v1 JSON endpoint, and share validation/query/freshness logic. Home Assistant consumes /v1 JSON using equivalent strict runtime validation and its existing persistent outage cache. Use the existing edge Cache API; persistent distributed refresh infrastructure is deferred until traffic justifies it.

**Tech stack:** TypeScript, Cloudflare Workers/Wrangler, rss-parser, Node test runner with Miniflare, Python/Home Assistant.

**Spec:** The approved review and follow-up in this chat, recorded below.

## Global constraints and design decisions

- Work on main. Preserve unrelated untracked user files; stage only task files.
- Retain existing exported product/region/brand metadata and root JSON fields.
- The versioned JSON response omits redundant RSS content/contentSnippet fields and has explicit product, sourceDate, fetchedAt, validFrom, validUntil and publication status metadata. Fuel prices stay decimal strings in cents/litre.
- HTTP routes: / (legacy JSON), /v1 (compact JSON). GET/HEAD/OPTIONS only, with explicit 404/405 behavior and correct CORS.
- Validate and canonicalize query values before cache access. Support Product, Day, Suburb, Region, Brand, Surrounding and existing exported metadata. Absolute DD/MM/YYYY dates must fall within yesterday/today/tomorrow in Perth and are translated to upstream relative days. Keys include resolved source date, product, canonical filters and representation version.
- Reject unsafe/malformed XML, missing required station fields, invalid numeric values, unexpected dates, more than 5000 items, conflicting station identities, and decompressed bodies larger than 4 MiB.
- Total upstream deadline 8 seconds, at most two attempts, bounded jitter and Retry-After support for transient failures. Never retry denied, malformed or oversized feeds. No upstream headers copied blindly.
- Successful nonempty data: maximum 300 seconds cache TTL, capped at the next Perth midnight/06:00/14:30 boundary and validity expiry. Empty results: maximum 30 seconds. Error responses: no-store. No expired fallback presented as fresh.
- Cache errors must not fail valid responses. Cache writes use waitUntil with structured diagnostics. No cross-request sharing of I/O promises; edge caching is per data centre and cold concurrency remains an explicit limitation.
- Custom domain fuelwatch.oss.bhodges.me declared in Wrangler. Enable sampled structured observability. Do not deploy.

## Review focus

- Near-midnight/06:00/publication requests must never mix source dates or continue past freshness deadlines.
- Slow response bodies and retry delays must share the same deadline and release response streams.
- Malformed/duplicate query parameters must not cause upstream request amplification.
- Failed cache reads/writes must still yield valid responses with no leaked upstream headers.
- Home Assistant must parse the versioned worker JSON and preserve existing retry/last-good-snapshot behavior.

## Task 1: Harden the worker and pin its HTTP contract

Files: api-worker/src/{index,fuelwatch,httpOptions,query,feed,upstream,cache,errors}.ts as appropriate; api-worker/tests/*; api-worker/package.json; api-worker/pnpm-lock.yaml; api-worker/tsconfig.json; api-worker/biome.json; api-worker/wrangler.jsonc; api-worker/worker-configuration.d.ts.

- [x] Add a runtime harness and tests demonstrating current failures: non-200 upstream responses, wrong/invalid quote fields, non-GET methods, preflight without optional headers, arbitrary query/path keys, oversized body, and versioned JSON contract.
- [x] Run tests and record expected failures before implementation.
- [x] Implement validation, canonical dates/keys, explicit responses, deadline/retries, bounded reads and safe caching. Preserve legacy fields and provide compact JSON.
- [x] Add deterministic unit tests for dates/TTL, retry deadlines and cache fault handling, plus runtime route tests using the deployed bundle.
- [x] Run worker tests, type check, lint and dry-run build. Commit fix(api-worker): harden feed validation and caching.

## Task 2: Connect Home Assistant and document operation

Files: custom_components/fuelwatch_wa/{api,const,coordinator}.py and manifest.json; tests/test_api.py; tests/test_worker_coordinator.py; api-worker/README.md; .github/workflows/ci.yml; tools/test_api_adapter.py.

- [x] Add an integration test asserting the worker URL, product/date request and JSON parsing; run RED.
- [x] Set the integration endpoint to https://fuelwatch.oss.bhodges.me/v1, updating the parser to validate JSON, retaining timeouts, retries and coordinator persistence.
- [x] Add pinned worker CI install, lint, typecheck, runtime tests and dry-run deployment checks.
- [x] Document endpoints, query/error contract, deployment/domain requirements, freshness, logs, capacity limitations and local commands.
- [x] Run focused Python validation and worker CI checks. Commit coherent integration and tooling changes.

## Task 3: Independent review and final verification

- [x] Review all changes against the approved findings with a fresh reviewer.
- [x] Reproduce and fix substantive findings with failing regression tests first.
- [x] Run final affected checks, confirm main branch and commit scope, and report tests/limits/commits.

## Evidence and adjustments

- Runtime failures were reproduced before changes. Final worker checks: 44 tests, strict TypeScript, Biome lint, frozen installation and Wrangler dry-run build.
- A fresh reviewer reproduced cache expiry during an awaited lookup, cancellation promises outliving the deadline and rss-parser accepting trailing XML/duplicate fields. Each now has a failing-then-passing regression test. Direct SAX validation uses the already-transitive parser as an explicit production dependency; it prevents lossy RSS parsing from hiding malformed input.
- Live validation passed for all seven products. Product 1 contained 939 source rows and 938 unique stations; neighbouring sites in Brabham and Armadale shared addresses but had distinct coordinates. Identity therefore includes coordinates, with the Python client restoring earlier address-only IDs from persisted snapshots/catalogue for compatibility.
- The live Product 1 JSON shrank from 808,097 to 493,765 bytes; the Python adapter accepted all 938 resulting station records.
- Isolated Python adapter validation passes 32 tests. Native full HA pytest is blocked by Windows' missing fcntl module; the available WSL distro has a missing VHD and Docker Engine did not become available. The Linux CI job retains the full suite, including a coordinator integration regression test. A portable focused runner explicitly bypasses only the HA package bootstrap.
- Deployment and push remain out of scope. Unrelated existing working-tree files are preserved.
- Final independent review found no remaining actionable defects after the station identity compatibility fix. Worker milestone: f777ca9.

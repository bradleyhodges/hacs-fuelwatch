import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { afterEach, beforeEach, test } from "node:test";
import {
    convertV4MiniflareOptions,
    Miniflare,
    NoOpLog,
    Response,
} from "miniflare";

const script = await readFile(
    new URL("../dist/test/index.js", import.meta.url),
    "utf8",
);
const perthDate = () =>
    new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);
const xml = (fields = "", day = perthDate()) =>
    `<?xml version="1.0"?><rss version="2.0"><channel><title>FuelWatch</title><item><title>Example</title><description>Station description</description><brand>Example</brand><date>${day}</date><price>185.9</price><trading-name>Example Station</trading-name><location>PERTH</location><address>1 Test Road</address><latitude>-31.95</latitude><longitude>115.86</longitude>${fields}</item></channel></rss>`;
let worker;
let requests;
let origin;
const migration = await readFile(
    new URL("../migrations/0001_station_enrichment.sql", import.meta.url),
    "utf8",
);

beforeEach(() => {
    requests = [];
    origin = () =>
        new Response(xml(), { headers: { "Content-Type": "text/xml" } });
    worker = new Miniflare(
        convertV4MiniflareOptions({
            modules: true,
            script,
            compatibilityDate: "2026-09-29",
            compatibilityFlags: ["nodejs_compat"],
            cf: false,
            log: new NoOpLog(),
            bindings: {
                FUELWATCH_URL: "https://source.example/rss",
                GOOGLE_MAPS_API_KEY: "test-secret",
            },
            d1Databases: { FUELWATCH_DB: "worker-enrichment" },
            outboundService: (request) => {
                requests.push(request.url);
                return origin(request);
            },
        }),
    );
});
afterEach(async () => {
    await worker.dispose();
});
const fetchWorker = (path = "/", init) =>
    worker.dispatchFetch(`https://fuelwatch.example${path}`, init);

test("legacy JSON keeps fuel fields and RSS content", async () => {
    const response = await fetchWorker();
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.feed.items[0].price, "185.9");
    assert.equal(body.feed.items[0].brand, "Example");
    assert.equal(body.feed.items[0].content, "Station description");
});

test("public requests merge D1 data without contacting Google", async () => {
    const db = await worker.getD1Database("FUELWATCH_DB");
    await db.batch(
        migration
            .replace(/--[^\n]*/g, "")
            .split(";")
            .filter((sql) => sql.trim())
            .map((sql) => db.prepare(sql)),
    );
    const now = Date.now();
    const seed = {
        name: "Example Station",
        brand: "Example",
        street: "1 Test Road",
        suburb: "PERTH",
        latitude: -31.95,
        longitude: 115.86,
    };
    const cached = {
        placeId: "test_place",
        fetchedAt: new Date(now - 1000).toISOString(),
        expiresAt: new Date(now + 86400_000).toISOString(),
        postcode: "6000",
        phone: "+61899811151",
        features: ["Toilets"],
        hours: {},
        is24Hours: null,
        googleMapsUri:
            "https://www.google.com/maps/search/?api=1&query_place_id=test_place",
        attributions: [],
    };
    await db
        .prepare(
            "INSERT INTO station_enrichment(station_key, seed_json, last_seen_at, enrichment_json) VALUES (?, ?, ?, ?)",
        )
        .bind(
            JSON.stringify(["1 test road", "perth", -31.95, 115.86]),
            JSON.stringify(seed),
            now,
            JSON.stringify(cached),
        )
        .run();
    const response = await fetchWorker("/v1");
    assert.equal(response.status, 200);
    const station = (await response.json()).feed.items[0];
    assert.equal(station.address.postcode, "6000");
    assert.equal(station.phone, "+61899811151");
    assert.equal(station.enrichment.provider, "Google Maps");
    assert.equal(requests.length, 1);
    assert.ok(
        requests.every((url) => new URL(url).hostname === "source.example"),
    );
    // A fresh HTTP representation must not extend the provider's own retirement deadline.
    cached.fetchedAt = new Date(
        Date.now() - 30 * 86400_000 + 5000,
    ).toISOString();
    cached.expiresAt = new Date(Date.now() + 5000).toISOString();
    await db
        .prepare("UPDATE station_enrichment SET enrichment_json = ?")
        .bind(JSON.stringify(cached))
        .run();
    const nearExpiry = await fetchWorker("/v1?Product=4");
    const ttl = Number(
        /s-maxage=(\d+)/.exec(nearExpiry.headers.get("Cache-Control"))?.[1] ??
            0,
    );
    assert.ok(ttl <= 5);
    assert.equal(
        (await nearExpiry.json()).feed.items[0].address.postcode,
        "6000",
    );
});

test("legacy phone normalization preserves E.164 output", async () => {
    origin = () => new Response(xml("<phone>(08) 9981 1151</phone>"));
    const response = await fetchWorker();
    assert.equal((await response.json()).feed.items[0].phone, "+61899811151");
});

test("versioned JSON excludes redundant descriptions and exposes provenance", async () => {
    const response = await fetchWorker("/v1?Product=4");
    const body = await response.json();
    assert.equal(body.schemaVersion, 1);
    assert.equal(body.product, 4);
    assert.equal(body.sourceDate, perthDate());
    assert.equal(body.publicationStatus, "available");
    assert.equal(body.feed.items[0].content, undefined);
    assert.equal(body.feed.items[0].contentSnippet, undefined);
    assert.equal(body.feed.items[0].description, undefined);
    assert.equal(body.feed.items[0].price.perLitre, 185.9);
    assert.equal(
        body.feed.items[0].price.asAt,
        `${perthDate()}T00:00:00.000+08:00`,
    );
    assert.equal(body.feed.items[0].address.suburb, "PERTH");
    assert.ok(body.fetchedAt && body.validFrom && body.validUntil);
});

test("unknown paths and unsupported methods do not contact upstream", async () => {
    assert.equal((await fetchWorker("/missing")).status, 404);
    const post = await fetchWorker("/", { method: "POST" });
    assert.equal(post.status, 405);
    assert.equal(post.headers.get("Allow"), "GET, HEAD, OPTIONS");
    assert.equal(requests.length, 0);
});

test("cold HEAD succeeds and shares the GET cache entry", async () => {
    const head = await fetchWorker("/v1", { method: "HEAD" });
    assert.equal(head.status, 200);
    assert.equal(await head.text(), "");
    // Cache writes run in waitUntil: poll only the local runtime until visible.
    for (let i = 0; i < 20; i++) {
        const result = await fetchWorker("/v1");
        await result.text();
        if (result.headers.get("X-FuelWatch-Cache") === "HIT") return;
        await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.fail("GET never hit the HEAD-populated cache");
});

test("CORS preflight does not require Access-Control-Request-Headers", async () => {
    const response = await fetchWorker("/", {
        method: "OPTIONS",
        headers: {
            Origin: "https://app.example",
            "Access-Control-Request-Method": "GET",
        },
    });
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), "*");
    assert.equal(
        response.headers.get("Access-Control-Allow-Methods"),
        "GET, HEAD, OPTIONS",
    );
    assert.equal(requests.length, 0);
});

test("conditional GET and HEAD return a bodyless 304 for a cached representation", async () => {
    let response;
    for (let i = 0; i < 20; i++) {
        response = await fetchWorker("/v1");
        await response.text();
        if (response.headers.get("X-FuelWatch-Cache") === "HIT") break;
        await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal(response.headers.get("X-FuelWatch-Cache"), "HIT");
    const count = requests.length;
    for (const method of ["GET", "HEAD"]) {
        const result = await fetchWorker("/v1", {
            method,
            headers: { "If-None-Match": `W/${response.headers.get("ETag")}` },
        });
        assert.equal(result.status, 304);
        assert.equal(await result.text(), "");
        assert.equal(result.headers.get("X-FuelWatch-Fresh-Until"), null);
    }
    assert.equal(requests.length, count);
});

test("valid empty snapshots have a short explicit cache lifetime", async () => {
    origin = () =>
        new Response(
            '<rss version="2.0"><channel><title>FuelWatch</title></channel></rss>',
        );
    const response = await fetchWorker("/v1");
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.publicationStatus, "empty");
    assert.deepEqual(body.feed.items, []);
    const ttl = Number(
        /s-maxage=(\d+)/.exec(response.headers.get("Cache-Control"))?.[1] ?? 0,
    );
    assert.ok(ttl <= 30);
});

test("denied upstream responses return a safe uncached error without retries", async () => {
    origin = () => new Response("<html>Forbidden</html>", { status: 403 });
    const response = await fetchWorker();
    assert.equal(response.status, 502);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal((await response.json()).error.code, "upstream_denied");
    assert.equal(requests.length, 1);
});

test("Retry-After is respected and an upstream 503 never becomes cached 200", async () => {
    origin = () =>
        new Response(xml(), { status: 503, headers: { "Retry-After": "120" } });
    const response = await fetchWorker();
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("Retry-After"), "120");
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.equal(requests.length, 1);
});

test("temporary upstream failure recovers with a bounded retry", async () => {
    origin = () =>
        requests.length === 1
            ? new Response("Unavailable", { status: 503 })
            : new Response(xml());
    const response = await fetchWorker();
    assert.equal(response.status, 200);
    assert.equal(requests.length, 2);
});

for (const [name, body] of [
    ["invalid price", () => xml().replace("185.9", "NaN")],
    ["zero price", () => xml().replace("185.9", "0")],
    ["blank latitude", () => xml().replace("-31.95", " ")],
    ["invalid coordinate", () => xml().replace("-31.95", "999")],
    ["wrong source date", () => xml("", "2000-01-01")],
    [
        "missing identity",
        () => xml().replace("<address>1 Test Road</address>", ""),
    ],
    ["unsafe XML", () => `<!DOCTYPE rss [<!ENTITY x "bad">]>${xml()}`],
    ["malformed XML", () => "<rss><channel>"],
])
    test(`${name} is rejected before cache publication`, async () => {
        origin = () => new Response(body());
        const response = await fetchWorker();
        assert.equal(response.status, 502);
        assert.equal((await response.json()).error.code, "invalid_feed");
        assert.equal(requests.length, 1);
    });

test("oversized decompressed input is rejected", async () => {
    origin = () =>
        new Response(
            xml().replace("Station description", "x".repeat(4 * 1024 * 1024)),
        );
    const response = await fetchWorker();
    assert.equal(response.status, 502);
    assert.equal((await response.json()).error.code, "response_too_large");
});

test("unrelated upstream headers cannot break caching or leak into JSON", async () => {
    origin = () =>
        new Response(xml(), {
            headers: {
                "Content-Type": "text/xml",
                "Set-Cookie": "session=private",
                Vary: "*",
                ETag: '"xml-hash"',
                "Cache-Control": "public, max-age=86400",
            },
        });
    const response = await fetchWorker();
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("Set-Cookie"), null);
    assert.notEqual(response.headers.get("ETag"), '"xml-hash"');
    assert.notEqual(response.headers.get("Vary"), "*");
    assert.match(response.headers.get("Cache-Control"), /s-maxage=/);
});

test("invalid, duplicate and unknown query values never contact upstream", async () => {
    for (const query of [
        "Product=999",
        "Day=nonsense",
        "Product=1&Product=4",
        "nonce=1",
        "Surrounding=maybe",
        "Region=999",
        "Brand=-1",
    ]) {
        assert.equal((await fetchWorker(`/?${query}`)).status, 400, query);
    }
    assert.equal(requests.length, 0);
});

test("equivalent query order and absolute dates resolve to one cache key", async () => {
    const [year, month, day] = perthDate().split("-");
    await (await fetchWorker("/?Product=4&Day=today")).text();
    let result;
    for (let i = 0; i < 20; i++) {
        result = await fetchWorker(
            `/?Day=${day}%2F${month}%2F${year}&Product=4`,
        );
        await result.text();
        if (result.headers.get("X-FuelWatch-Cache") === "HIT") break;
        await new Promise((resolve) => setTimeout(resolve, 10));
    }
    assert.equal(result.headers.get("X-FuelWatch-Cache"), "HIT");
    assert.equal(new URL(requests[0]).searchParams.get("Day"), "today");
});

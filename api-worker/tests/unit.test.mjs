import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const output = fileURLToPath(
    new URL("../dist/test/units.cjs", import.meta.url),
);
await build({
    stdin: {
        contents:
            'export * from "./src/cache"; export * from "./src/query"; export * from "./src/feed"; export * from "./src/upstream"; export * from "./src/fuelwatch";',
        resolveDir: fileURLToPath(new URL("..", import.meta.url)),
        loader: "ts",
    },
    bundle: true,
    platform: "node",
    format: "cjs",
    outfile: output,
    logLevel: "silent",
});
const api = createRequire(import.meta.url)(output);
const time = (value) => Date.parse(value);
const query = (value, now = "2026-09-29T16:00:00+08:00") =>
    api.parseQuery(new URLSearchParams(value), time(now));
const source = new URL("https://source.example/rss");

test("worker output matches the Home Assistant contract fixture", async () => {
    const expected = JSON.parse(
        await readFile(
            new URL("./fixtures/fuelwatch-v1.json", import.meta.url),
            "utf8",
        ),
    );
    const fields = Object.entries(expected.feed.items[0])
        .map(([key, value]) => `<${key}>${value}</${key}>`)
        .join("");
    const feed = await api.parseFeed(
        `<rss version="2.0"><channel><title>FuelWatch</title><item>${fields}</item></channel></rss>`,
        expected.sourceDate,
    );
    assert.deepEqual(
        {
            ...api.metadata(
                query(""),
                feed.items.length,
                time(expected.fetchedAt),
            ),
            feed: api.compactFeed(feed),
        },
        expected,
    );
});

test("Perth calendar transitions include leap days and year boundaries", () => {
    assert.equal(
        query("Day=yesterday", "2026-01-01T00:00:00+08:00").sourceDate,
        "2025-12-31",
    );
    assert.equal(
        query("Day=tomorrow", "2028-02-28T23:59:59+08:00").sourceDate,
        "2028-02-29",
    );
    assert.equal(
        query("Day=28/09/2026", "2026-09-29T05:59:59+08:00").upstream.get(
            "Day",
        ),
        "yesterday",
    );
    assert.throws(() => query("Day=31/09/2026"), { code: "invalid_query" });
});

test("canonical keys normalize defaults, suburb whitespace and date aliases", () => {
    const first = query("Suburb=Perth&Day=today");
    const second = query("Product=1&Day=29/09/2026&Suburb=%20PERTH%20");
    const url = new URL("https://worker.example/v1");
    assert.equal(api.cacheKey(url, first).url, api.cacheKey(url, second).url);
    assert.notEqual(
        api.cacheKey(url, first).url,
        api.cacheKey(new URL("https://worker.example/"), first).url,
    );
});

for (const [now, day, count, expected] of [
    ["2026-09-29T16:00:00+08:00", "today", 1, 300],
    ["2026-09-29T16:00:00+08:00", "today", 0, 30],
    ["2026-09-29T23:59:45+08:00", "today", 1, 15],
    ["2026-09-29T05:59:45+08:00", "yesterday", 1, 15],
    ["2026-09-29T06:00:00+08:00", "yesterday", 1, 0],
    ["2026-09-29T14:29:45+08:00", "tomorrow", 0, 15],
])
    test(`freshness at ${now}, ${day}, count ${count}`, () => {
        const info = api.metadata(query(`Day=${day}`, now), count, time(now));
        assert.equal(api.cacheTtl(info, time(now)), expected);
    });

test("provenance distinguishes unpublished future prices from empty results", () => {
    const now = "2026-09-29T10:00:00+08:00";
    const info = api.metadata(query("Day=tomorrow", now), 0, time(now));
    assert.equal(info.publicationStatus, "not_yet_published");
    assert.equal(info.validFrom, "2026-09-29T22:00:00.000Z");
    assert.equal(info.validUntil, "2026-09-30T22:00:00.000Z");
});

test("cache failures do not escape into the request handler", async () => {
    const key = new Request("https://worker.example/cache");
    const cache = {
        match: async () => {
            throw new Error("unavailable");
        },
        put: async () => {
            throw new Error("unavailable");
        },
    };
    assert.equal(await api.readCache(cache, key, Date.now()), undefined);
    await assert.doesNotReject(
        api.writeCache(cache, key, new Response("valid")),
    );
});

test("expired or malformed cache records are never served", async () => {
    for (const expiry of ["invalid", "1", ""]) {
        const cache = {
            match: async () =>
                new Response("stale", {
                    headers: { "X-FuelWatch-Fresh-Until": expiry },
                }),
        };
        assert.equal(
            await api.readCache(
                cache,
                new Request("https://worker.example"),
                Date.now(),
            ),
            undefined,
        );
    }
});

test("cache records expiring during lookup are treated as misses", async () => {
    const started = Date.now();
    const cache = {
        match: async () => {
            await new Promise((resolve) => setTimeout(resolve, 40));
            return new Response("stale", {
                headers: { "X-FuelWatch-Fresh-Until": String(started + 10) },
            });
        },
    };
    assert.equal(
        await api.readCache(
            cache,
            new Request("https://worker.example"),
            started,
        ),
        undefined,
    );
});

for (const [status, oversized, expected] of [
    [503, false, "upstream_timeout"],
    [200, true, "response_too_large"],
]) {
    test(`cleanup cannot stall the deadline for status ${status}`, async () => {
        const fetcher = async () =>
            new Response(
                new ReadableStream({
                    start(controller) {
                        if (oversized)
                            controller.enqueue(
                                new Uint8Array(api.MAX_RESPONSE_BYTES + 1),
                            );
                    },
                    cancel() {
                        return new Promise(() => {});
                    },
                }),
                { status },
            );
        let timer;
        try {
            await assert.rejects(
                Promise.race([
                    api.fetchFeed(source, { fetcher, timeoutMs: 20 }),
                    new Promise((_, reject) => {
                        timer = setTimeout(
                            () => reject(new Error("Cleanup stalled")),
                            150,
                        );
                    }),
                ]),
                { code: expected },
            );
        } finally {
            clearTimeout(timer);
        }
    });
}

test("XML validation rejects extra roots, trailing text and duplicate singleton fields", async () => {
    const xml =
        '<rss version="2.0"><channel><title>FuelWatch</title><item><price>185.9</price><date>2026-09-29</date><trading-name>Station</trading-name><address>1 Road</address><location>PERTH</location><latitude>-31.95</latitude><longitude>115.86</longitude></item></channel></rss>';
    for (const body of [
        `${xml}<junk/>`,
        `${xml}not xml`,
        xml.replace("</price>", "</price><price>NaN</price>"),
    ]) {
        await assert.rejects(api.parseFeed(body, "2026-09-29"), {
            code: "invalid_feed",
        });
    }
});

test("timeout aborts a stalled origin before response headers", async () => {
    let aborted = false;
    const fetcher = (_url, { signal }) =>
        new Promise((_resolve, reject) =>
            signal.addEventListener(
                "abort",
                () => {
                    aborted = true;
                    reject(signal.reason);
                },
                { once: true },
            ),
        );
    await assert.rejects(api.fetchFeed(source, { fetcher, timeoutMs: 20 }), {
        code: "upstream_timeout",
        status: 504,
    });
    assert.equal(aborted, true);
});

test("timeout aborts a stalled body and cancels its reader", async () => {
    let cancelled = false;
    const fetcher = async () =>
        new Response(
            new ReadableStream({
                cancel() {
                    cancelled = true;
                },
            }),
        );
    await assert.rejects(api.fetchFeed(source, { fetcher, timeoutMs: 20 }), {
        code: "upstream_timeout",
    });
    assert.equal(cancelled, true);
});

test("retry sleep uses the same overall request deadline", async () => {
    let attempts = 0;
    const fetcher = async () => {
        attempts++;
        return new Response("busy", { status: 503 });
    };
    await assert.rejects(api.fetchFeed(source, { fetcher, timeoutMs: 20 }), {
        code: "upstream_timeout",
    });
    assert.equal(attempts, 1);
});

test("malformed UTF-8 is a validation failure and is not retried", async () => {
    let attempts = 0;
    const fetcher = async () => {
        attempts++;
        return new Response(new Uint8Array([0xc3, 0x28]));
    };
    await assert.rejects(api.fetchFeed(source, { fetcher }), {
        code: "invalid_feed",
        status: 502,
    });
    assert.equal(attempts, 1);
});

test("HTTP-date Retry-After values are interpreted as seconds", () => {
    assert.equal(
        api.retryAfterSeconds(
            "Tue, 29 Sep 2026 08:01:00 GMT",
            time("2026-09-29T08:00:00Z"),
        ),
        60,
    );
    assert.equal(api.retryAfterSeconds("invalid", Date.now()), undefined);
});

test("station count and conflicting identities are bounded atomically", async () => {
    const item =
        "<item><price>185.9</price><date>2026-09-29</date><trading-name>Station</trading-name><address>1 Road</address><location>PERTH</location><latitude>-31.95</latitude><longitude>115.86</longitude></item>";
    const feed = (items) =>
        `<rss version="2.0"><channel><title>FuelWatch</title>${items}</channel></rss>`;
    await assert.rejects(api.parseFeed(feed(item.repeat(5001)), "2026-09-29"), {
        code: "invalid_feed",
    });
    await assert.rejects(
        api.parseFeed(
            feed(item + item.replace("185.9", "199.9")),
            "2026-09-29",
        ),
        { code: "invalid_feed" },
    );
    assert.equal(
        (await api.parseFeed(feed(item + item), "2026-09-29")).items.length,
        1,
    );
    assert.equal(
        (
            await api.parseFeed(
                feed(
                    item +
                        item
                            .replace("115.86", "115.87")
                            .replace("185.9", "199.9"),
                ),
                "2026-09-29",
            )
        ).items.length,
        2,
    );
});

import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const output = fileURLToPath(
    new URL("../dist/test/google.cjs", import.meta.url),
);
await build({
    entryPoints: [fileURLToPath(new URL("../src/google.ts", import.meta.url))],
    outfile: output,
    bundle: true,
    platform: "node",
    format: "cjs",
    logLevel: "silent",
});
const api = createRequire(import.meta.url)(output);
const seed = {
    name: "Example Station",
    brand: "Example",
    street: "1 Test Road",
    suburb: "PERTH",
    latitude: -31.95,
    longitude: 115.86,
};
const now = Date.parse("2026-09-29T08:00:00Z");
const place = (extra = {}) => ({
    id: "test_place",
    displayName: { text: "Example Station" },
    types: ["gas_station"],
    location: { latitude: -31.95, longitude: 115.86 },
    businessStatus: "OPERATIONAL",
    addressComponents: [
        { types: ["country"], shortText: "AU" },
        { types: ["administrative_area_level_1"], shortText: "WA" },
        { types: ["postal_code"], longText: "6000" },
        { types: ["street_number"], longText: "1" },
        { types: ["route"], longText: "Test Road" },
        { types: ["locality"], longText: "Perth" },
    ],
    internationalPhoneNumber: "+61 8 9981 1151",
    restroom: true,
    regularOpeningHours: {
        periods: [{ open: { day: 0, hour: 0, minute: 0 } }],
    },
    attributions: [
        { displayName: "Example provider", uri: "https://example.com" },
    ],
    ...extra,
});

test("Google search uses a field mask, secret header and a single bounded request", async () => {
    let requests = 0;
    const result = await api.lookupPlace(seed, "test-secret", {
        now,
        refreshDays: 7,
        fetcher: async (url, init) => {
            requests++;
            assert.equal(
                String(url),
                "https://places.googleapis.com/v1/places:searchText",
            );
            assert.equal(init.headers["X-Goog-Api-Key"], "test-secret");
            assert.ok(!init.headers["X-Goog-FieldMask"].includes("*"));
            assert.equal(JSON.parse(init.body).includedType, "gas_station");
            return Response.json({ places: [place()] });
        },
    });
    assert.equal(requests, 1);
    assert.equal(result.postcode, "6000");
    assert.equal(result.phone, "+61899811151");
    assert.equal(result.is24Hours, true);
    assert.ok(result.features.includes("Toilets"));
    assert.equal(result.attributions[0].displayName, "Example provider");
});

test("stored place IDs use Details and are still matched to the station", async () => {
    const result = await api.lookupPlace(seed, "key", {
        now,
        placeId: "test_place",
        fetcher: async (url) => {
            assert.equal(
                String(url),
                "https://places.googleapis.com/v1/places/test_place",
            );
            return Response.json(place());
        },
    });
    assert.equal(result.placeId, "test_place");
});

test("wrong, distant, closed and ambiguous places are not used", async () => {
    for (const places of [
        [place({ displayName: { text: "Different business" } })],
        [place({ location: { latitude: -32.0, longitude: 115.86 } })],
        [place({ businessStatus: "CLOSED_PERMANENTLY" })],
        [
            place({
                addressComponents: place().addressComponents.map((component) =>
                    component.types.includes("street_number")
                        ? { ...component, longText: "55" }
                        : component,
                ),
            }),
        ],
        [place(), place({ id: "another_place" })],
    ]) {
        assert.equal(
            await api.lookupPlace(seed, "key", {
                now,
                fetcher: async () => Response.json({ places }),
            }),
            null,
        );
    }
});

test("a shared suburb cannot match a competing neighbouring station", async () => {
    const nearby = { ...seed, name: "BP Perth", brand: "BP" };
    assert.equal(
        await api.lookupPlace(nearby, "key", {
            now,
            fetcher: async () =>
                Response.json({
                    places: [place({ displayName: { text: "Shell Perth" } })],
                }),
        }),
        null,
    );
});

test("a shared generic name cannot match a different brand or street", async () => {
    const nearby = { ...seed, name: "BP Service Centre", brand: "BP" };
    for (const candidate of [
        place({ displayName: { text: "Shell Service Centre" } }),
        place({
            displayName: { text: "BP Service Centre" },
            addressComponents: place().addressComponents.map((value) =>
                value.types.includes("route")
                    ? { ...value, longText: "Other Road" }
                    : value,
            ),
        }),
    ]) {
        assert.equal(
            await api.lookupPlace(nearby, "key", {
                now,
                fetcher: async () => Response.json({ places: [candidate] }),
            }),
            null,
        );
    }
});

test("provider errors expose safe classifications and never log secret or response bodies", async () => {
    for (const [status, code] of [
        [403, "configuration"],
        [429, "rate_limited"],
        [503, "upstream"],
    ]) {
        await assert.rejects(
            api.lookupPlace(seed, "test-secret", {
                now,
                fetcher: async () => new Response("test-secret", { status }),
            }),
            { code },
        );
    }
});

test("budget storage failures propagate without attempting a provider request", async () => {
    const failure = new Error("D1 unavailable");
    await assert.rejects(
        api.lookupPlace(seed, "key", {
            beforeRequest: async () => {
                throw failure;
            },
            fetcher: async () =>
                assert.fail("Google must not be called without a reservation"),
        }),
        (error) => error === failure,
    );
});

test("Google hours split overnight periods and preserve multiple shifts", () => {
    const result = api.googleHours({
        periods: [
            {
                open: { day: 1, hour: 22, minute: 0 },
                close: { day: 2, hour: 2, minute: 0 },
            },
            {
                open: { day: 2, hour: 8, minute: 0 },
                close: { day: 2, hour: 12, minute: 0 },
            },
        ],
    });
    assert.equal(result.hours.Monday, "22:00-24:00");
    assert.equal(result.hours.Tuesday, "00:00-02:00, 08:00-12:00");
    assert.equal(result.hours.Sunday, "Closed");
    assert.equal(result.is24Hours, false);
    assert.deepEqual(
        api.googleHours({ periods: [{ open: { day: 8, hour: 0 } }] }),
        { hours: {}, is24Hours: null },
    );
});

test("malformed provider envelopes fail safely instead of becoming a negative match", async () => {
    for (const value of [
        [],
        null,
        { places: "bad" },
        { places: [false] },
        { places: [{}] },
        { places: [{ id: "test_place" }] },
        { places: [place({ location: null })] },
    ]) {
        await assert.rejects(
            api.lookupPlace(seed, "key", {
                now,
                fetcher: async () => Response.json(value),
            }),
            { code: "invalid_response" },
        );
    }
    await assert.rejects(
        api.lookupPlace(seed, "key", {
            now,
            placeId: "test_place",
            fetcher: async () => Response.json({}),
        }),
        { code: "invalid_response" },
    );
    for (const value of [{}, { places: [] }]) {
        assert.equal(
            await api.lookupPlace(seed, "key", {
                now,
                fetcher: async () => Response.json(value),
            }),
            null,
        );
    }
});

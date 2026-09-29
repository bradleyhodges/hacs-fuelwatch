import { brands } from "./fuelwatch";
import {
    type Feature,
    normalisePhone,
    type OpeningHours,
    type StationEnrichment,
    WEEKDAYS,
} from "./station";
import { type Fetcher, readBounded } from "./upstream";

/** FuelWatch-owned identity used for matching; provider locations never replace these coordinates. */
export interface StationSeed {
    name: string;
    brand: string;
    street: string;
    suburb: string;
    latitude: number;
    longitude: number;
}

/** Operator-safe classification. Never attach an upstream response body or API-key URL as a cause. */
export class GoogleLookupError extends Error {
    constructor(
        readonly code:
            | "configuration"
            | "rate_limited"
            | "upstream"
            | "invalid_response"
            | "timeout"
            | "budget_exhausted",
    ) {
        super(`Google enrichment ${code}`);
    }
}

const object = (value: unknown): Record<string, unknown> =>
    value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
const string = (value: unknown, maximum = 500): string =>
    typeof value === "string" && value.length <= maximum ? value.trim() : "";
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const normalise = (value: string): string =>
    value
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, " ")
        .trim();

/** Compare street routes conservatively while accepting common Australian suffix abbreviations. */
function streetRoute(value: string): string {
    const aliases: Record<string, string> = {
        rd: "road",
        st: "street",
        hwy: "highway",
        fwy: "freeway",
        ave: "avenue",
        av: "avenue",
        dr: "drive",
        tce: "terrace",
        pde: "parade",
        cres: "crescent",
        ct: "court",
        pl: "place",
    };
    return normalise(
        value.replace(/^(?:lot\s+)?\d+[a-z]?(?:-\d+[a-z]?)?\s+/i, ""),
    )
        .split(" ")
        .map((token) => aliases[token] ?? token)
        .join(" ");
}

const knownBrands = [...new Set(Object.values(brands).map(normalise))].filter(
    (brand) => brand !== "independent",
);

/** Read one address component by type; response ordering is not a public Google API guarantee. */
function component(
    place: Record<string, unknown>,
    type: string,
    short = false,
): string {
    const entry = list(place.addressComponents)
        .map(object)
        .find((value) => list(value.types).includes(type));
    return string(entry?.[short ? "shortText" : "longText"]);
}

/** Great-circle distance used only to reject ambiguous matches, never to rewrite station coordinates. */
function distance(
    seed: StationSeed,
    location: Record<string, unknown>,
): number {
    if (
        typeof location.latitude !== "number" ||
        typeof location.longitude !== "number" ||
        !Number.isFinite(location.latitude) ||
        !Number.isFinite(location.longitude) ||
        Math.abs(location.latitude) > 90 ||
        Math.abs(location.longitude) > 180
    )
        return Infinity;
    const rad = Math.PI / 180;
    const a =
        Math.sin(((location.latitude - seed.latitude) * rad) / 2) ** 2 +
        Math.cos(seed.latitude * rad) *
            Math.cos(location.latitude * rad) *
            Math.sin(((location.longitude - seed.longitude) * rad) / 2) ** 2;
    return 6371000 * 2 * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** Conservative matching requires an Australian WA fuel site, nearby coordinates and overlapping name. */
function matches(seed: StationSeed, place: Record<string, unknown>): boolean {
    if (
        !list(place.types).includes("gas_station") ||
        place.businessStatus !== "OPERATIONAL" ||
        component(place, "country", true) !== "AU" ||
        component(place, "administrative_area_level_1", true) !== "WA" ||
        distance(seed, object(place.location)) > 250
    )
        return false;
    const sourceName = normalise(seed.name);
    const googleName = normalise(string(object(place.displayName).text));
    if (!googleName) return false;
    // Nearby forecourts can share a brand/suburb. A conflicting street number rules out even an exact name.
    const sourceNumber = /^(?:lot\s+)?(\d+[a-z]?(?:-\d+[a-z]?)?)\b/i
        .exec(seed.street)?.[1]
        ?.toLowerCase();
    const googleNumber = component(place, "street_number").toLowerCase();
    if (sourceNumber && googleNumber && sourceNumber !== googleNumber)
        return false;
    const route = component(place, "route");
    if (route && streetRoute(seed.street) !== streetRoute(route)) return false;
    // Recognize explicit competing brands, but allow unbranded business names such as corner stores.
    const sourceBrand = normalise(seed.brand);
    const namedBrands = knownBrands.filter((brand) =>
        ` ${googleName} `.includes(` ${brand} `),
    );
    if (
        sourceBrand !== "independent" &&
        namedBrands.length &&
        !namedBrands.some(
            (brand) =>
                sourceBrand === brand ||
                ` ${sourceBrand} `.includes(` ${brand} `),
        )
    )
        return false;
    if (sourceName === googleName) return true;
    // Locality names are location evidence, not business identity (BP Perth must not match Shell Perth).
    const ignored = new Set([
        "the",
        "of",
        "fuel",
        "station",
        "service",
        "petrol",
        "centre",
        "center",
        ...normalise(seed.suburb).split(" "),
    ]);
    const sourceTokens = sourceName
        .split(" ")
        .filter((value) => !ignored.has(value));
    const googleTokens = googleName
        .split(" ")
        .filter((value) => !ignored.has(value));
    const overlap = sourceTokens.filter((value) =>
        googleTokens.includes(value),
    ).length;
    return (
        overlap > 0 &&
        overlap / Math.max(sourceTokens.length, googleTokens.length) >= 0.5 &&
        normalise(component(place, "locality")) === normalise(seed.suburb)
    );
}

/**
 * Convert Google's Sunday-first week periods into named Perth weekdays.
 * @remarks A single Sunday midnight opening without a close is Google's documented always-open form.
 * Overnight periods are split at midnight; overlapping intervals are merged without inventing opening hours.
 */
export function googleHours(value: unknown): {
    hours: OpeningHours;
    is24Hours: boolean | null;
} {
    const unknown = { hours: {}, is24Hours: null };
    const periods = list(object(value).periods);
    if (!periods.length || periods.length > 42) return unknown;
    const point = (value: unknown): number | null => {
        const p = object(value);
        const minute = p.minute ?? 0;
        if (
            typeof p.day !== "number" ||
            typeof p.hour !== "number" ||
            typeof minute !== "number" ||
            !Number.isInteger(p.day) ||
            p.day < 0 ||
            p.day > 6 ||
            !Number.isInteger(p.hour) ||
            p.hour < 0 ||
            p.hour > 23 ||
            !Number.isInteger(minute) ||
            minute < 0 ||
            minute > 59
        )
            return null;
        return p.day * 1440 + p.hour * 60 + minute;
    };
    if (
        periods.length === 1 &&
        point(object(periods[0]).open) === 0 &&
        object(periods[0]).close === undefined
    )
        return { hours: {}, is24Hours: true };
    const days: [number, number][][] = Array.from({ length: 7 }, () => []);
    for (const period of periods) {
        const start = point(object(period).open);
        let end = point(object(period).close);
        if (start === null || end === null || end === start) return unknown;
        if (end < start) end += 7 * 1440;
        for (let cursor = start; cursor < end; ) {
            const day = Math.floor(cursor / 1440);
            const stop = Math.min(end, (day + 1) * 1440);
            days[day % 7].push([cursor % 1440, stop - day * 1440]);
            cursor = stop;
        }
    }
    const hours: OpeningHours = {};
    const clock = (minute: number) =>
        `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
    for (let day = 0; day < 7; day++) {
        const merged: [number, number][] = [];
        for (const interval of days[day].sort((a, b) => a[0] - b[0])) {
            const last = merged.at(-1);
            if (last && interval[0] <= last[1])
                last[1] = Math.max(last[1], interval[1]);
            else merged.push([...interval]);
        }
        hours[WEEKDAYS[(day + 6) % 7]] = merged.length
            ? merged
                  .map(([from, to]) => `${clock(from)}-${clock(to)}`)
                  .join(", ")
            : "Closed";
    }
    return {
        hours,
        is24Hours: Object.values(hours).every(
            (value) => value === "00:00-24:00",
        ),
    };
}

/** Only explicitly reported Google facilities become canonical features; gas_station alone implies none. */
function features(
    place: Record<string, unknown>,
    alwaysOpen: boolean | null,
): Feature[] {
    const result: Feature[] = [];
    for (const [type, label] of [
        ["atm", "ATM"],
        ["car_wash", "Carwash"],
        ["restaurant", "Restaurant"],
        ["convenience_store", "Convenience Store"],
    ] as const) {
        if (list(place.types).includes(type)) result.push(label);
    }
    if (place.restroom === true) result.push("Toilets");
    if (object(place.paymentOptions).acceptsCreditCards === true)
        result.push("Credit Cards");
    if (object(place.paymentOptions).acceptsDebitCards === true)
        result.push("Debit Cards");
    if (alwaysOpen) result.push("Open 24 hours");
    return result;
}

const FIELDS = [
    "id",
    "displayName",
    "types",
    "businessStatus",
    "location",
    "addressComponents",
    "internationalPhoneNumber",
    "regularOpeningHours.periods",
    "restroom",
    "paymentOptions",
    "googleMapsUri",
    "attributions",
];
interface LookupOptions {
    placeId?: string;
    now?: number;
    refreshDays?: number;
    fetcher?: Fetcher;
    signal?: AbortSignal;
    /** Atomically reserve one unit of the daily D1 budget immediately before each paid request. */
    beforeRequest?: () => Promise<void>;
}

/**
 * Resolve one FuelWatch station with exactly one Places API request, never a geocoding waterfall.
 * @param seed Validated FuelWatch identity used both in the query and to reject neighbouring businesses.
 * @param apiKey Server-side Places API (New) credential; never include it in logs or returned data.
 * @param options Persisted place ID, refresh interval, request-budget hook and test dependencies.
 * @returns Verified enrichment, or null for no confident unique match. Existing IDs use Place Details.
 * @throws GoogleLookupError for provider failures. Budget-hook/storage errors propagate unchanged.
 * @remarks Only scheduled refresh calls this function. The API key travels in a header and is never logged.
 */
export async function lookupPlace(
    seed: StationSeed,
    apiKey: string,
    options: LookupOptions = {},
): Promise<StationEnrichment | null> {
    if (!apiKey) throw new GoogleLookupError("configuration");
    const now = options.now ?? Date.now();
    const timeout = AbortSignal.timeout(5000);
    const signal = options.signal
        ? AbortSignal.any([options.signal, timeout])
        : timeout;
    const details = !!options.placeId;
    const url = new URL(
        details
            ? `https://places.googleapis.com/v1/places/${encodeURIComponent(options.placeId ?? "")}`
            : "https://places.googleapis.com/v1/places:searchText",
    );
    // Keep D1 reservation failures distinct from provider outages so cron fails visibly without spending.
    await options.beforeRequest?.();
    try {
        signal.throwIfAborted();
        const response = await (options.fetcher ?? fetch)(url, {
            method: details ? "GET" : "POST",
            signal,
            redirect: "manual",
            headers: {
                "Content-Type": "application/json",
                "X-Goog-Api-Key": apiKey,
                "X-Goog-FieldMask": FIELDS.map((field) =>
                    details ? field : `places.${field}`,
                ).join(","),
            },
            ...(details
                ? {}
                : {
                      body: JSON.stringify({
                          textQuery: `${seed.name}, ${seed.street}, ${seed.suburb}, WA, Australia`,
                          includedType: "gas_station",
                          strictTypeFiltering: true,
                          pageSize: 5,
                          regionCode: "AU",
                          languageCode: "en",
                          locationBias: {
                              circle: {
                                  center: {
                                      latitude: seed.latitude,
                                      longitude: seed.longitude,
                                  },
                                  radius: 500,
                              },
                          },
                      }),
                  }),
        });
        if (response.status !== 200) {
            void response.body?.cancel().catch(() => {});
            if (details && response.status === 404) return null;
            throw new GoogleLookupError(
                response.status === 429
                    ? "rate_limited"
                    : response.status === 400 ||
                        response.status === 401 ||
                        response.status === 403
                      ? "configuration"
                      : "upstream",
            );
        }
        const value: unknown = JSON.parse(
            await readBounded(response, signal, 256 * 1024),
        );
        // An invalid envelope is an outage, not evidence that a previously matched station vanished.
        if (
            !value ||
            typeof value !== "object" ||
            Array.isArray(value) ||
            (!details &&
                object(value).places !== undefined &&
                !Array.isArray(object(value).places))
        ) {
            throw new GoogleLookupError("invalid_response");
        }
        const rawCandidates = details ? [value] : list(object(value).places);
        if (
            rawCandidates.length > 5 ||
            rawCandidates.some((candidate) => {
                const place = object(candidate);
                return (
                    !/^[\w-]+$/.test(string(place.id, 200)) ||
                    ![
                        "OPERATIONAL",
                        "CLOSED_TEMPORARILY",
                        "CLOSED_PERMANENTLY",
                    ].includes(string(place.businessStatus)) ||
                    (place.businessStatus === "OPERATIONAL" &&
                        (!string(object(place.displayName).text) ||
                            !Array.isArray(place.types) ||
                            !place.types.every(
                                (type) => typeof type === "string",
                            ) ||
                            !Array.isArray(place.addressComponents) ||
                            !Number.isFinite(
                                distance(seed, object(place.location)),
                            )))
                );
            })
        ) {
            throw new GoogleLookupError("invalid_response");
        }
        const candidates = rawCandidates
            .map(object)
            .filter((place) => matches(seed, place));
        if (candidates.length !== 1) return null;
        const place = candidates[0];
        const placeId = string(place.id, 200);
        if (!/^[\w-]+$/.test(placeId))
            throw new GoogleLookupError("invalid_response");
        const schedule = googleHours(place.regularOpeningHours);
        const postcode = component(place, "postal_code");
        return {
            placeId,
            fetchedAt: new Date(now).toISOString(),
            expiresAt: new Date(
                now + (options.refreshDays ?? 7) * 86_400_000,
            ).toISOString(),
            postcode: /^\d{4}$/.test(postcode) ? postcode : null,
            phone: normalisePhone(string(place.internationalPhoneNumber)),
            features: features(place, schedule.is24Hours),
            hours: schedule.hours,
            is24Hours: schedule.is24Hours,
            googleMapsUri: `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(seed.name)}&query_place_id=${encodeURIComponent(placeId)}`,
            attributions: list(place.attributions)
                .slice(0, 10)
                .flatMap((raw) => {
                    const data = object(raw);
                    const displayName = string(
                        data.provider ?? data.displayName,
                        200,
                    );
                    const uri = string(data.providerUri ?? data.uri, 2000);
                    return displayName && /^https:\/\//.test(uri)
                        ? [{ displayName, uri }]
                        : [];
                }),
        };
    } catch (error) {
        if (error instanceof GoogleLookupError) throw error;
        throw new GoogleLookupError(
            signal.aborted
                ? "timeout"
                : error instanceof SyntaxError
                  ? "invalid_response"
                  : "upstream",
        );
    }
}

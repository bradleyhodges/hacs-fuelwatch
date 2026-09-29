import { ApiError } from "./errors";
import {
    brands,
    FUELWATCH_PRODUCTS,
    FUELWATCH_QUERY_PARAMETERS,
    type FuelWatchProductId,
    regions,
} from "./fuelwatch";

const DAY_MS = 86_400_000;
export const PERTH_OFFSET_MS = 8 * 3_600_000;
export interface FeedQuery {
    product: FuelWatchProductId;
    sourceDate: string;
    upstream: URLSearchParams;
    canonical: URLSearchParams;
}

/** Perth has no daylight saving; arithmetic never uses the host timezone. */
export const perthDate = (now: number): string =>
    new Date(now + PERTH_OFFSET_MS).toISOString().slice(0, 10);
export const shiftDate = (date: string, days: number): string =>
    new Date(Date.parse(`${date}T00:00:00Z`) + days * DAY_MS)
        .toISOString()
        .slice(0, 10);
const invalid = (): never => {
    throw new ApiError(
        400,
        "invalid_query",
        "Invalid or unsupported FuelWatch query.",
    );
};

/** Validate before cache lookup or origin access, then normalize equivalent queries. */
export function parseQuery(params: URLSearchParams, now: number): FeedQuery {
    const allowed: readonly string[] = FUELWATCH_QUERY_PARAMETERS;
    if (params.size > allowed.length || params.toString().length > 1024)
        invalid();
    for (const [name, value] of params) {
        if (
            !allowed.includes(name) ||
            params.getAll(name).length !== 1 ||
            !value.trim()
        )
            invalid();
    }
    const productText = params.get("Product") ?? "1";
    if (
        !/^\d+$/.test(productText) ||
        !Object.hasOwn(FUELWATCH_PRODUCTS, productText)
    )
        invalid();
    // Membership was checked against the finite product catalogue above.
    const product = Number(productText) as FuelWatchProductId;
    const today = perthDate(now);
    const days = [shiftDate(today, -1), today, shiftDate(today, 1)];
    const names = ["yesterday", "today", "tomorrow"];
    const requested = params.get("Day") ?? "today";
    let dayIndex = names.indexOf(requested);
    if (dayIndex < 0) {
        const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(requested);
        if (!match) return invalid();
        dayIndex = days.indexOf(`${match[3]}-${match[2]}-${match[1]}`);
        if (dayIndex < 0) invalid();
    }
    const sourceDate = days[dayIndex];
    const upstream = new URLSearchParams({
        Product: String(product),
        Day: names[dayIndex],
    });
    for (const [name, catalogue] of [
        ["Region", regions],
        ["Brand", brands],
    ] as const) {
        const value = params.get(name);
        if (value !== null) {
            if (!/^\d+$/.test(value) || !Object.hasOwn(catalogue, value))
                invalid();
            upstream.set(name, value);
        }
    }
    const suburb = params.get("Suburb");
    if (suburb !== null) {
        if (
            suburb.length > 100 ||
            [...suburb].some(
                (character) =>
                    character.charCodeAt(0) < 32 ||
                    character.charCodeAt(0) === 127,
            )
        )
            invalid();
        upstream.set(
            "Suburb",
            suburb.trim().replace(/\s+/g, " ").toUpperCase(),
        );
    }
    const surrounding = params.get("Surrounding");
    if (surrounding !== null) {
        if (surrounding !== "yes" && surrounding !== "no") invalid();
        upstream.set("Surrounding", surrounding);
    }
    upstream.sort();
    const canonical = new URLSearchParams(upstream);
    canonical.set("Day", sourceDate);
    return { product, sourceDate, upstream, canonical };
}

/** Only configuration chooses the origin; client input supplies validated filters. */
export function upstreamUrl(base: string, query: FeedQuery): URL {
    const url = new URL(base);
    if (url.protocol !== "https:" || url.username || url.password)
        throw new Error("FUELWATCH_URL must be HTTPS without credentials");
    url.search = query.upstream.toString();
    url.hash = "";
    return url;
}

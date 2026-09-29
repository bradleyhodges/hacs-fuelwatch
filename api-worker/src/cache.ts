import type { FeedMetadata } from "./feed";
import { type FeedQuery, perthDate, shiftDate } from "./query";

/** Expire at price/publication boundaries even when the origin advertises a longer TTL. */
export function cacheTtl(info: FeedMetadata, now: number): number {
    const today = perthDate(now);
    const boundaries = [
        `${today}T06:00:00+08:00`,
        `${today}T14:30:00+08:00`,
        `${shiftDate(today, 1)}T00:00:00+08:00`,
    ]
        .map(Date.parse)
        .filter((time) => time > now);
    const expiry = Math.min(...boundaries, Date.parse(info.validUntil));
    const maximum = info.publicationStatus === "available" ? 300 : 30;
    return Math.max(0, Math.min(maximum, Math.floor((expiry - now) / 1000)));
}

/** Cache identity contains no client headers, ignored filters or relative dates. */
export function cacheKey(request: URL, query: FeedQuery): Request {
    const url = new URL(
        `/__fuelwatch_cache/v1/${request.pathname === "/" ? "legacy" : "compact"}`,
        request.origin,
    );
    url.search = query.canonical.toString();
    return new Request(url, { method: "GET" });
}

/** Cache availability is an optimization, never a data-availability dependency. */
export async function readCache(
    cache: Cache,
    key: Request,
    now: number,
): Promise<Response | undefined> {
    try {
        const response = await cache.match(key);
        if (!response) return undefined;
        const expiry = Number(response.headers.get("X-FuelWatch-Fresh-Until"));
        if (!Number.isFinite(expiry) || expiry <= Math.max(now, Date.now())) {
            void response.body?.cancel().catch(() => {});
            return undefined;
        }
        return response;
    } catch {
        console.warn({ event: "cache_read_failed" });
        return undefined;
    }
}

/** Attach to waitUntil; cache failures are observable and contained. */
export async function writeCache(
    cache: Cache,
    key: Request,
    response: Response,
): Promise<void> {
    try {
        await cache.put(key, response);
    } catch {
        console.warn({ event: "cache_write_failed" });
    }
}

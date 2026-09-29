import { cacheKey, cacheTtl, readCache, writeCache } from "./cache";
import { ApiError } from "./errors";
import { compactFeed, metadata, parseFeed } from "./feed";
import { ALLOWED_METHODS, corsHeaders, handleOptions } from "./httpOptions";
import { parseQuery, upstreamUrl } from "./query";
import { fetchFeed } from "./upstream";

function deliver(
    response: Response,
    request: Request,
    cacheStatus: string,
): Response {
    const headers = new Headers(response.headers);
    headers.set("X-FuelWatch-Cache", cacheStatus);
    const expires = Number(headers.get("X-FuelWatch-Fresh-Until"));
    headers.delete("X-FuelWatch-Fresh-Until");
    headers.set(
        "Cache-Control",
        Number.isFinite(expires) && expires > Date.now()
            ? `public, max-age=0, s-maxage=${Math.max(0, Math.floor((expires - Date.now()) / 1000))}`
            : "no-store",
    );
    const etag = headers.get("ETag");
    const matches = request.headers
        .get("If-None-Match")
        ?.split(",")
        .some(
            (tag) =>
                tag.trim() === "*" || tag.trim().replace(/^W\//, "") === etag,
        );
    if (matches || request.method === "HEAD") {
        void response.body?.cancel().catch(() => {});
        return new Response(null, {
            status: matches ? 304 : response.status,
            headers,
        });
    }
    return new Response(response.body, { status: response.status, headers });
}

export default {
    async fetch(
        request: Request,
        env: Env,
        ctx: ExecutionContext,
    ): Promise<Response> {
        const started = Date.now();
        try {
            const url = new URL(request.url);
            if (url.pathname !== "/" && url.pathname !== "/v1")
                throw new ApiError(404, "not_found", "Endpoint not found.");
            if (request.method === "OPTIONS") return handleOptions(request);
            if (request.method !== "GET" && request.method !== "HEAD")
                throw new ApiError(
                    405,
                    "method_not_allowed",
                    "Use GET or HEAD to request prices.",
                );
            const query = parseQuery(url.searchParams, started);
            const key = cacheKey(url, query);
            const cached = await readCache(caches.default, key, started);
            if (cached) return deliver(cached, request, "HIT");
            const xml = await fetchFeed(upstreamUrl(env.FUELWATCH_URL, query), {
                signal: request.signal,
            });
            const feed = await parseFeed(xml, query.sourceDate);
            const now = Date.now();
            const info = metadata(query, feed.items.length, now);
            // A request started just before a rollover cannot cache into the next period.
            const ttl = Math.min(
                cacheTtl(info, now),
                Math.max(
                    0,
                    cacheTtl(info, started) - Math.ceil((now - started) / 1000),
                ),
            );
            const body = JSON.stringify(
                url.pathname === "/"
                    ? { feed }
                    : { ...info, feed: compactFeed(feed) },
            );
            const hash = await crypto.subtle.digest(
                "SHA-256",
                new TextEncoder().encode(body),
            );
            const etag = `"${Array.from(new Uint8Array(hash), (byte) => byte.toString(16).padStart(2, "0")).join("")}"`;
            const response = new Response(body, {
                headers: {
                    ...corsHeaders,
                    "Content-Type": "application/json;charset=UTF-8",
                    "X-Content-Type-Options": "nosniff",
                    "Cache-Control":
                        ttl > 0
                            ? `public, max-age=0, s-maxage=${ttl}`
                            : "no-store",
                    "X-FuelWatch-Fresh-Until": String(now + ttl * 1000),
                    "X-FuelWatch-Source-Date": info.sourceDate,
                    "X-FuelWatch-Fetched-At": info.fetchedAt,
                    ETag: etag,
                },
            });
            if (ttl > 0)
                ctx.waitUntil(
                    writeCache(caches.default, key, response.clone()),
                );
            console.log({
                event: "feed_fetched",
                product: query.product,
                sourceDate: query.sourceDate,
                stations: feed.items.length,
                durationMs: now - started,
                ttl,
            });
            return deliver(response, request, "MISS");
        } catch (error) {
            const failure =
                error instanceof ApiError
                    ? error
                    : new ApiError(
                          500,
                          "internal_error",
                          "The price service could not complete the request.",
                      );
            console.warn({
                event: "request_failed",
                code: failure.code,
                status: failure.status,
                durationMs: Date.now() - started,
            });
            const headers = new Headers({
                ...corsHeaders,
                "Content-Type": "application/json;charset=UTF-8",
                "Cache-Control": "no-store",
            });
            if (failure.status === 405) headers.set("Allow", ALLOWED_METHODS);
            if (failure.retryAfter !== undefined)
                headers.set("Retry-After", String(failure.retryAfter));
            return new Response(
                request.method === "HEAD"
                    ? null
                    : JSON.stringify({
                          error: {
                              code: failure.code,
                              message: failure.message,
                          },
                      }),
                { status: failure.status, headers },
            );
        }
    },
} satisfies ExportedHandler<Env>;
